"""Account anonymisation workflow (issue #4554).

* A signed-in user submits a request from their profile. This records a
  ``pending`` request, raises a support ticket, writes an audit entry and
  emails the user a confirmation. Re-submitting never creates a duplicate.
* A super admin approves the request, which runs the anonymisation, or
  rejects it with a reason that is emailed to the user.
* A super admin can also start anonymisation directly from the Users page for
  requests received by email or phone.

The database work lives in :func:`app.repositories.anonymisation.anonymise_user`.
This module gathers what the run needs, then handles the side effects: the
final email (sent *before* the address is replaced), the marketing opt-out,
RAG cleanup, deleting recording and voicemail files, and the audit trail.
Audit entries never hold the person's original personal data in plain text.
"""

from __future__ import annotations

import html
from pathlib import Path
from typing import Any, Mapping, Sequence

from loguru import logger

from app.repositories import anonymisation as anonymisation_repo
from app.repositories import users as users_repo
from app.services import audit as audit_service
from app.services import email as email_service

STATUS_PENDING = anonymisation_repo.STATUS_PENDING
STATUS_APPROVED = anonymisation_repo.STATUS_APPROVED
STATUS_REJECTED = anonymisation_repo.STATUS_REJECTED
STATUS_COMPLETED = anonymisation_repo.STATUS_COMPLETED


class AnonymisationError(Exception):
    """Raised when an anonymisation operation cannot proceed."""


def _user_id(user: Mapping[str, Any] | None) -> int:
    try:
        return int((user or {}).get("id"))
    except (TypeError, ValueError):
        raise AnonymisationError("A valid user id is required") from None


def _phones(*values: Any) -> list[str]:
    return sorted({str(v).strip() for v in values if v is not None and str(v).strip()})


async def list_requests(*, status: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
    return await anonymisation_repo.list_requests(status=status, limit=limit)


async def get_request(request_id: int) -> dict[str, Any] | None:
    return await anonymisation_repo.get_request(request_id)


async def get_request_for_user(user_id: int) -> dict[str, Any] | None:
    return await anonymisation_repo.get_request_for_user(user_id)


# ---------------------------------------------------------------------------
# Submitting a request
# ---------------------------------------------------------------------------


async def create_request(
    *,
    user: Mapping[str, Any],
    reason: str | None = None,
    confirm_email: str | None = None,
    acknowledged: bool = False,
    ip_address: str | None = None,
    user_agent: str | None = None,
    request: Any = None,
) -> dict[str, Any]:
    """Submit the signed-in user's own request from the profile form."""
    user_id = _user_id(user)
    email = str(user.get("email") or "").strip()
    if not acknowledged:
        raise AnonymisationError("Please confirm that you understand anonymisation can't be undone.")
    if not email or (confirm_email or "").strip().lower() != email.lower():
        raise AnonymisationError("The email address you entered doesn't match your account.")
    return await _open_request(
        user=user,
        reason=reason,
        requested_by_user_id=user_id,
        source="profile",
        ip_address=ip_address,
        user_agent=user_agent,
        request=request,
        notify=True,
    )


async def _open_request(
    *,
    user: Mapping[str, Any],
    reason: str | None,
    requested_by_user_id: int,
    source: str,
    ip_address: str | None = None,
    user_agent: str | None = None,
    request: Any = None,
    notify: bool,
) -> dict[str, Any]:
    user_id = _user_id(user)
    existing = await anonymisation_repo.get_request_for_user(user_id)
    if existing is not None and existing.get("status") == STATUS_COMPLETED:
        raise AnonymisationError("This account has already been anonymised.")

    request_id, created = await anonymisation_repo.create_request(
        user_id=user_id,
        reason=reason,
        requested_by_user_id=requested_by_user_id,
        ip_address=ip_address,
        user_agent=user_agent,
    )
    if not created:
        current = await anonymisation_repo.get_request(request_id) or {}
        return {"request_id": request_id, "created": False, "status": current.get("status"), "ticket_id": None}

    ticket_id = await _create_support_ticket(user, request_id, source=source)
    if ticket_id is not None:
        await anonymisation_repo.set_support_ticket(request_id, ticket_id)
    await _audit(
        "account_anonymisation.request",
        actor_id=requested_by_user_id,
        request_id=request_id,
        user_id=user_id,
        details={"source": source, "support_ticket_id": ticket_id},
        request=request,
    )
    if notify:
        await _send(
            user.get("email"),
            "We've received your account anonymisation request",
            [
                "We've received your request to delete or anonymise your account.",
                "A member of our team will review it and email you when it has been actioned.",
                "If you didn't make this request, please contact us straight away.",
            ],
        )
    return {"request_id": request_id, "created": True, "status": STATUS_PENDING, "ticket_id": ticket_id}


# ---------------------------------------------------------------------------
# Admin decisions
# ---------------------------------------------------------------------------


async def approve_request(
    *, request_id: int, actor_user: Mapping[str, Any], notes: str | None = None, request: Any = None
) -> dict[str, Any]:
    """Approve a pending request and run the anonymisation. Irreversible.

    An ``approved`` request whose earlier run failed can be approved again to
    retry it; every step is idempotent.
    """
    actor_id = _user_id(actor_user)
    record = await anonymisation_repo.get_request(request_id)
    if record is None:
        raise AnonymisationError("Anonymisation request not found")
    status = record.get("status")
    if status not in (STATUS_PENDING, STATUS_APPROVED):
        raise AnonymisationError("Only pending requests can be approved (this one is " + str(status) + ").")
    user_id = int(record["user_id"])
    if user_id == actor_id:
        raise AnonymisationError("You can't approve the anonymisation of your own account.")

    target = await users_repo.get_user_by_id(user_id) or {}
    current_email = str(target.get("email") or "").strip() or None
    # On a retry the users row may already hold the placeholder.
    original_email = None if anonymisation_repo.is_anonymised_email(current_email) else current_email

    if status == STATUS_PENDING:
        claimed = await anonymisation_repo.mark_approved(
            request_id,
            decided_by=actor_id,
            original_email_hash=anonymisation_repo.email_hash(original_email),
            notes=notes,
        )
        if not claimed:
            raise AnonymisationError("This request has already been decided by someone else.")

    try:
        details = await _run(user_id, target, original_email)
    except Exception as exc:
        # Only the exception type: messages can echo the values being written.
        logger.error("Account anonymisation failed for request {}: {}", request_id, type(exc).__name__)
        await anonymisation_repo.record_error(request_id, type(exc).__name__ + ": run failed, retry to resume")
        await _audit(
            "account_anonymisation.fail",
            actor_id=actor_id,
            request_id=request_id,
            user_id=user_id,
            details={"error": type(exc).__name__},
            request=request,
        )
        raise AnonymisationError("Anonymisation failed part-way. It is safe to approve the request again to retry.") from exc

    await anonymisation_repo.mark_completed(request_id)
    await _audit(
        "account_anonymisation.complete",
        actor_id=actor_id,
        request_id=request_id,
        user_id=user_id,
        details=details,
        request=request,
    )
    return {"request_id": request_id, "user_id": user_id, "status": STATUS_COMPLETED, "details": details}


async def reject_request(
    *, request_id: int, actor_user: Mapping[str, Any], reason: str, request: Any = None
) -> dict[str, Any]:
    """Reject a pending request. The reason is required and emailed to the user."""
    actor_id = _user_id(actor_user)
    reason_text = (reason or "").strip()
    if not reason_text:
        raise AnonymisationError("Enter a reason for rejecting the request. It will be emailed to the user.")
    record = await anonymisation_repo.get_request(request_id)
    if record is None:
        raise AnonymisationError("Anonymisation request not found")
    if record.get("status") != STATUS_PENDING:
        raise AnonymisationError("Only pending requests can be rejected.")
    if not await anonymisation_repo.mark_rejected(request_id, decided_by=actor_id, notes=reason_text):
        raise AnonymisationError("This request has already been decided by someone else.")
    user_id = int(record["user_id"])
    await _audit(
        "account_anonymisation.reject",
        actor_id=actor_id,
        request_id=request_id,
        user_id=user_id,
        details={"reason_length": len(reason_text)},
        request=request,
    )
    await _send(
        record.get("request_user_email"),
        "Your account anonymisation request",
        [
            "We've reviewed your request to delete or anonymise your account and can't action it at this time.",
            "Reason: " + reason_text,
            "You can contact us if you have any questions, or submit a new request from your profile.",
        ],
    )
    return {"request_id": request_id, "user_id": user_id, "status": STATUS_REJECTED}


async def start_for_user(
    *, user_id: int, actor_user: Mapping[str, Any], notes: str | None = None, request: Any = None
) -> dict[str, Any]:
    """Super admin: anonymise an account now, for a request received by email or phone."""
    actor_id = _user_id(actor_user)
    if int(user_id) == actor_id:
        raise AnonymisationError("You can't anonymise your own account.")
    target = await users_repo.get_user_by_id(int(user_id))
    if not target:
        raise AnonymisationError("User not found.")
    opened = await _open_request(
        user=target,
        reason=notes or "Request received by email or phone.",
        requested_by_user_id=actor_id,
        source="admin",
        request=request,
        notify=False,
    )
    return await approve_request(
        request_id=int(opened["request_id"]), actor_user=actor_user, notes=notes, request=request
    )


# ---------------------------------------------------------------------------
# The run
# ---------------------------------------------------------------------------


async def _run(user_id: int, target: Mapping[str, Any], original_email: str | None) -> dict[str, int]:
    staff = await anonymisation_repo.list_staff_for_user(user_id, original_email)
    staff_ids = [int(row["id"]) for row in staff]
    phones = _phones(target.get("mobile_phone"), *(row.get("mobile_phone") for row in staff))
    ticket_ids = await anonymisation_repo.list_requested_ticket_ids(user_id, staff_ids)
    recordings = await anonymisation_repo.list_call_recordings(phones, staff_ids)
    voicemail = await anonymisation_repo.list_audio_attachments(ticket_ids)

    # Emailed before the address is replaced, so the person can still receive it.
    await _send(
        original_email,
        "Your account is being anonymised",
        [
            "Your request to delete or anonymise your account has been approved and is being actioned now.",
            "Once it is complete you won't be able to sign in, and this email address will no longer be linked to the account.",
            "We keep some records, such as invoices, where the law requires us to, but your personal details are removed from them.",
            "This is the last email we'll send to this address about your account.",
        ],
    )
    # Before the run, so the opt-out holds even if a later step fails.
    await _opt_out_marketing(original_email, anonymisation_repo.placeholder_email_for(user_id))
    rag_counts = await _purge_rag(user_id)

    details = await anonymisation_repo.anonymise_user(
        user_id,
        original_email=original_email,
        phones=phones,
        staff_ids=staff_ids,
        ticket_ids=ticket_ids,
        call_recording_ids=[int(row["id"]) for row in recordings],
        attachment_ids=[int(row["id"]) for row in voicemail],
    )

    # Files are removed only once the rows are gone, so a rolled-back run
    # never leaves rows pointing at deleted files.
    details["call_recording_files"] = _delete_recording_files(recordings)
    details["voicemail_files"] = _delete_attachment_files(voicemail)
    details.update({"rag_" + key: int(value) for key, value in rag_counts.items()})
    return details


async def _purge_rag(user_id: int) -> dict[str, int]:
    try:
        from app.services import ai_opt_out

        return await ai_opt_out.purge_user_from_rag_index(user_id)
    except Exception as exc:
        logger.warning("RAG cleanup for anonymised user failed: {}", type(exc).__name__)
        return {}


def _delete_recording_files(recordings: Sequence[Mapping[str, Any]]) -> int:
    deleted = 0
    for row in recordings:
        raw = str(row.get("file_path") or "").strip()
        if not raw:
            continue
        try:
            path = Path(raw)
            if path.is_file():
                path.unlink()
                deleted += 1
        except OSError as exc:
            logger.warning("Could not delete call recording file {}: {}", row.get("id"), type(exc).__name__)
    return deleted


def _delete_attachment_files(attachments: Sequence[Mapping[str, Any]]) -> int:
    deleted = 0
    try:
        from app.services import ticket_attachments as attachments_service
    except Exception:  # pragma: no cover - defensive
        return 0
    for row in attachments:
        filename = str(row.get("filename") or "")
        if not filename:
            continue
        try:
            path = attachments_service.get_attachment_file_path(filename)
            if path.is_file():
                path.unlink()
                deleted += 1
        except (OSError, ValueError) as exc:
            logger.warning("Could not delete voicemail file {}: {}", row.get("id"), type(exc).__name__)
    return deleted


async def _opt_out_marketing(original_email: str | None, placeholder: str) -> None:
    """Add both addresses to the sales opt-out list.

    The one-way hash stored on the request also excludes the original
    address from future campaigns (see ``marketing_campaigns.list_opted_out``).
    """
    try:
        from app.repositories import marketing_campaigns as campaign_repo
        from app.services import marketing_campaigns as campaign_service

        # The placeholder uses the reserved .invalid TLD, which the address
        # validator in normalise_email rejects, so it is added as written.
        for email in (campaign_service.normalise_email(original_email), placeholder.lower()):
            if email:
                await campaign_repo.add_opt_out(email, campaign_service.CATEGORY_SALES, None)
    except Exception as exc:
        logger.warning("Marketing opt-out for anonymised user failed: {}", type(exc).__name__)


# ---------------------------------------------------------------------------
# Side-effect helpers
# ---------------------------------------------------------------------------


async def _create_support_ticket(user: Mapping[str, Any], request_id: int, *, source: str) -> int | None:
    """Raise a ticket so staff are notified. It holds no personal details."""
    try:
        from app.services import tickets as tickets_service

        user_id = _user_id(user)
        company_id = user.get("company_id")
        via = "from their profile" if source == "profile" else "by email or phone (recorded by a super admin)"
        description = (
            "User #" + str(user_id) + " asked " + via + " to have their account anonymised.\n\n"
            "Anonymisation request #" + str(request_id) + ". Review it at /admin/anonymisation/"
            + str(request_id) + " and approve or reject it. Approving is irreversible."
        )
        ticket = await tickets_service.create_ticket(
            subject="Account anonymisation request #" + str(request_id),
            description=description,
            requester_id=user_id,
            company_id=int(company_id) if company_id is not None else None,
            assigned_user_id=None,
            priority="normal",
            status="open",
            category="Account Anonymisation",
            module_slug=None,
            external_reference="anonymisation:" + str(request_id),
        )
        if isinstance(ticket, Mapping) and ticket.get("id") is not None:
            return int(ticket["id"])
        return None
    except Exception as exc:
        logger.warning("Failed to create anonymisation support ticket: {}", type(exc).__name__)
        return None


async def _send(recipient: Any, subject: str, paragraphs: Sequence[str]) -> bool:
    address = str(recipient or "").strip()
    if not address or anonymisation_repo.is_anonymised_email(address):
        return False
    html_body = "".join("<p>" + html.escape(text) + "</p>" for text in paragraphs)
    try:
        sent, _ = await email_service.send_email(
            subject=subject,
            recipients=[address],
            html_body=html_body,
            text_body="\n\n".join(paragraphs),
        )
        return bool(sent)
    except Exception as exc:
        logger.warning("Anonymisation email failed: {}", type(exc).__name__)
        return False


async def _audit(
    action: str,
    *,
    actor_id: int,
    request_id: int,
    user_id: int,
    details: Mapping[str, Any] | None,
    request: Any = None,
) -> None:
    """Write an audit entry. It references ids and counts only, never names or addresses."""
    try:
        await audit_service.log_action(
            action=action,
            user_id=actor_id,
            entity_type="account_anonymisation_request",
            entity_id=request_id,
            new_value={"user_id": user_id, "details": dict(details or {})},
            request=request,
        )
    except Exception as exc:
        logger.warning("Anonymisation audit write failed: {}", type(exc).__name__)
