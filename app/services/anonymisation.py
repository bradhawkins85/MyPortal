"""Orchestration for the account-anonymisation lifecycle (issue #4554).

The destructive PII-removal run itself lives in
:mod:`app.repositories.anonymisation` (``anonymise_user``) so that it can be
unit-tested in isolation. This service coordinates the higher-level flow and
the cross-cutting side effects:

* opening a ``pending`` request from a user's profile and raising a support
  ticket so an administrator has a work item to action;
* executing a request (admin action): the anonymisation run, best-effort
  cleanup of derived RAG documents, a marketing opt-out, an audit entry and a
  confirmation email;
* cancelling a ``pending`` request.

Every best-effort side effect (support ticket, RAG index, marketing, email,
audit) is guarded so that a failure in an optional integration never aborts an
anonymisation run that has already succeeded.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Any, Mapping, Sequence

from app.repositories import anonymisation as anonymisation_repo
from app.repositories import users as users_repo
from app.services import audit as audit_service
from app.services import email as email_service

logger = logging.getLogger(__name__)

# Canonical request-status values (kept here so callers have one import point).
STATUS_PENDING = "pending"
STATUS_EXECUTING = "executing"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"
STATUS_CANCELLED = "cancelled"


class AnonymisationError(Exception):
    """Raised when an anonymisation operation cannot proceed."""


def _user_id(user: Mapping[str, Any] | None) -> int:
    """Return the integer id of *user*, raising if it is missing or invalid."""
    try:
        return int((user or {}).get("id"))
    except (TypeError, ValueError):
        raise AnonymisationError("A valid user id is required") from None


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


async def list_requests(*, status: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
    """Return anonymisation requests (newest first), optionally by status."""
    return await anonymisation_repo.list_requests(status=status, limit=limit)


async def get_request(request_id: int) -> dict[str, Any] | None:
    """Return a single anonymisation request by id, or ``None``."""
    return await anonymisation_repo.get_request(request_id)


async def get_request_for_user(user_id: int) -> dict[str, Any] | None:
    """Return the user's anonymisation request, or ``None`` when there is none."""
    return await anonymisation_repo.get_request_for_user(user_id)


async def create_request(
    *,
    user: Mapping[str, Any],
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> dict[str, Any]:
    """Idempotently open a ``pending`` anonymisation request for *user*.

    If the user already has a request in any state it is returned unchanged
    (``created`` is ``False``) rather than duplicated. On a genuine first
    request a support ticket is raised best-effort and linked back to the
    request row.
    """
    user_id = _user_id(user)

    existing = await anonymisation_repo.get_request_for_user(user_id)
    if existing is not None:
        return {
            "request": dict(existing),
            "created": False,
            "ticket_id": None,
            "status": existing.get("status"),
        }

    request_id = await anonymisation_repo.create_request(
        user_id=user_id,
        requested_by_user_id=user_id,
        reason="User requested account anonymisation from their profile.",
        ip_address=ip_address,
        user_agent=user_agent,
    )
    request = await anonymisation_repo.get_request(request_id) or {}
    ticket_id = await _create_support_ticket(user, request_id)
    if ticket_id is not None:
        await anonymisation_repo.set_support_ticket(request_id, ticket_id)

    return {
        "request": dict(request),
        "created": True,
        "ticket_id": ticket_id,
        "status": STATUS_PENDING,
    }


async def execute_request(*, request_id: int, actor_user: Mapping[str, Any]) -> dict[str, Any]:
    """Execute a ``pending`` request (admin action). This is irreversible.

    Order: claim the request (``executing``) -> capture ticket ids -> run
    ``anonymise_user`` -> best-effort RAG + marketing cleanup -> ``completed``
    -> audit -> notify. The ticket ids are read *before* the run because
    ``anonymise_user`` NULLs ``tickets.requester_id``. If the anonymisation run
    raises, the request is marked ``failed`` and the error is re-raised as
    :class:`AnonymisationError`.
    """
    actor_id = _user_id(actor_user)
    request = await anonymisation_repo.get_request(request_id)
    if request is None:
        raise AnonymisationError("Anonymisation request not found")
    if request.get("status") != STATUS_PENDING:
        raise AnonymisationError(
            f"Request cannot be executed in status '{request.get('status')}'"
        )

    user_id = int(request["user_id"])
    target = await users_repo.get_user_by_id(user_id) or {}
    original_email = str(target.get("email") or "").strip() or None
    original_phone = (
        str(target.get("mobile_phone") or target.get("phone") or "").strip() or None
    )

    # Read the ticket ids before the run clears the requester link.
    ticket_ids = await anonymisation_repo.list_request_ticket_ids(user_id)

    # Atomically claim the request so two admins cannot run it concurrently.
    claimed = await anonymisation_repo.mark_executing(
        request_id, executed_by_user_id=actor_id
    )
    if not claimed:
        raise AnonymisationError(
            "Request is no longer pending; another execution may be in progress"
        )

    try:
        details = await anonymisation_repo.anonymise_user(
            user_id, original_email=original_email, original_phone=original_phone
        )
    except Exception as exc:
        await anonymisation_repo.mark_failed(request_id, error_message=str(exc))
        await _audit_execution(
            request_id=request_id,
            actor_id=actor_id,
            user_id=user_id,
            success=False,
            details={"error": str(exc)},
        )
        raise AnonymisationError(f"Anonymisation failed: {exc}") from exc

    rag_deleted = await _delete_rag_documents_for_user(ticket_ids)
    await _opt_out_marketing(original_email)

    await anonymisation_repo.mark_completed(
        request_id,
        original_email_hash=_sha256(original_email) if original_email else None,
    )
    await _audit_execution(
        request_id=request_id,
        actor_id=actor_id,
        user_id=user_id,
        success=True,
        details={**details, "rag_documents_deleted": rag_deleted},
    )
    notified = await _notify_user_anonymised(original_email)

    return {
        "request_id": request_id,
        "user_id": user_id,
        "status": STATUS_COMPLETED,
        "details": details,
        "rag_documents_deleted": rag_deleted,
        "notified": notified,
    }


async def cancel_request(*, request_id: int, actor_user: Mapping[str, Any]) -> dict[str, Any]:
    """Cancel a ``pending`` request (admin action). Deletes the request row."""
    actor_id = _user_id(actor_user)
    request = await anonymisation_repo.get_request(request_id)
    if request is None:
        raise AnonymisationError("Anonymisation request not found")
    if request.get("status") != STATUS_PENDING:
        raise AnonymisationError(
            f"Only pending requests can be cancelled "
            f"(current status: {request.get('status')})"
        )
    cancelled = await anonymisation_repo.cancel_request(request_id)
    if not cancelled:
        raise AnonymisationError("Could not cancel request (it is no longer pending)")
    user_id = int(request["user_id"])
    await _audit_execution(
        request_id=request_id,
        actor_id=actor_id,
        user_id=user_id,
        success=True,
        details={"action": "cancelled"},
    )
    return {"request_id": request_id, "user_id": user_id, "status": STATUS_CANCELLED}


# ---------------------------------------------------------------------------
# Side-effect helpers (all best-effort / defensive)
# ---------------------------------------------------------------------------


async def _create_support_ticket(user: Mapping[str, Any], request_id: int) -> int | None:
    """Best-effort: raise a support ticket for an administrator to action."""
    try:
        from app.services import tickets as tickets_service

        user_id = _user_id(user)
        company_id = user.get("company_id")
        first = str(user.get("first_name") or "").strip()
        last = str(user.get("last_name") or "").strip()
        display = f"{first} {last}".strip() or "an account holder"
        description = (
            f"{display} (user #{user_id}) requested anonymisation of their personal "
            f"data from their profile.\n\n"
            f"Anonymisation request ID: {request_id}\n"
            "An administrator should review and execute this request from the "
            "Anonymisation admin page. Execution is irreversible and removes "
            "personal data across all MyPortal tables."
        )
        ticket = await tickets_service.create_ticket(
            subject=f"Account anonymisation request — user #{user_id}",
            description=description,
            requester_id=user_id,
            company_id=int(company_id) if company_id is not None else None,
            assigned_user_id=None,
            priority="normal",
            status="open",
            category="Account Anonymisation",
            module_slug=None,
            external_reference=f"anonymisation:{user_id}",
        )
        if isinstance(ticket, Mapping) and ticket.get("id") is not None:
            return int(ticket["id"])
        return None
    except Exception as exc:  # pragma: no cover - best effort
        logger.warning("Failed to create anonymisation support ticket: %s", exc)
        return None


async def _delete_rag_documents_for_user(ticket_ids: Sequence[int]) -> int:
    """Best-effort: remove RAG documents derived from the user's tickets."""
    if not ticket_ids:
        return 0
    try:
        from app.repositories import rag_index as rag_index_repo

        doc_ids = await rag_index_repo.list_document_ids_for_source("tickets", ticket_ids)
        if not doc_ids:
            return 0
        return int(await rag_index_repo.delete_documents_by_ids(doc_ids) or 0)
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("RAG cleanup for anonymised user failed: %s", exc)
        return 0


async def _opt_out_marketing(email_value: str | None) -> None:
    """Best-effort: opt the user out of sales/marketing by their original email.

    The marketing opt-out is keyed by email address — the single identity that
    deliberately survives anonymisation so a suppression preference is
    honoured. All other personal data is removed by the anonymisation run.
    """
    if not email_value:
        return
    try:
        from app.repositories import marketing_campaigns as campaign_repo
        from app.services import marketing_campaigns as campaign_service

        email = campaign_service.normalise_email(email_value)
        if not email:
            return
        await campaign_repo.add_opt_out(email, campaign_service.CATEGORY_SALES, None)
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Marketing opt-out for anonymised user failed: %s", exc)


async def _notify_user_anonymised(email_value: str | None) -> bool:
    """Best-effort: confirm completion to the pre-anonymisation address."""
    if not email_value:
        return False
    html_body = (
        "<p>Hello,</p>"
        "<p>Your request to anonymise your MyPortal account has been completed. "
        "The personal information you asked us to remove has been deleted from "
        "our systems.</p>"
        "<p>If you believe any of your personal data has been retained, please "
        "contact our support team and we will investigate.</p>"
        "<p>Regards,<br/>The MyPortal team</p>"
    )
    text_body = (
        "Your request to anonymise your MyPortal account has been completed. "
        "The personal information you asked us to remove has been deleted from "
        "our systems."
    )
    try:
        sent, _ = await email_service.send_email(
            subject="Your MyPortal account has been anonymised",
            recipients=[email_value],
            html_body=html_body,
            text_body=text_body,
        )
        return bool(sent)
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Anonymisation notification email failed: %s", exc)
        return False


async def _audit_execution(
    *,
    request_id: int,
    actor_id: int,
    user_id: int,
    success: bool,
    details: Mapping[str, Any] | None,
) -> None:
    """Record an audit entry, swallowing errors so the flow is unaffected."""
    try:
        await audit_service.log_action(
            action="account_anonymisation.execute"
            if success
            else "account_anonymisation.fail",
            user_id=actor_id,
            entity_type="account_anonymisation_request",
            entity_id=request_id,
            new_value={"user_id": user_id, "details": dict(details or {})},
            metadata={"success": success},
        )
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Anonymisation audit write failed: %s", exc)
