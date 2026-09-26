"""Idempotent, least-privilege credential grants for staff workflows."""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote

from app.core.config import get_settings
from app.core.database import db
from app.repositories import credential_grants, standing_credential_grants


class WorkflowCredentialShareError(RuntimeError):
    """A deliberately non-sensitive workflow-facing share failure."""


def _utc_datetime(value: Any, *, required: bool = False) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        if required:
            raise WorkflowCredentialShareError("An expiry is required for external shares")
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as exc:
        raise WorkflowCredentialShareError("Expiry must be an ISO 8601 date and time") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    if parsed <= datetime.now(timezone.utc).replace(tzinfo=None):
        raise WorkflowCredentialShareError("Expiry must be in the future")
    return parsed


def _permissions(value: Any) -> set[str]:
    raw = value if isinstance(value, list) else str(value or "").split(",")
    permissions = {str(item).strip().lower() for item in raw if str(item).strip()}
    allowed = {"enumerate", "reveal", "share", "administer"}
    if not permissions or not permissions <= allowed:
        raise WorkflowCredentialShareError("At least one valid permission is required")
    return permissions


def _base_url() -> str:
    settings = get_settings()
    return str(settings.public_base_url or settings.portal_url or "").rstrip("/")


async def create_for_staff_workflow(
    *, company_id: int, workflow_staff_id: int, execution_id: int,
    step_identity: str, credential_id: Any, selector_type: str,
    staff_id: Any, job_title: Any, recipient_email: Any, permissions: Any,
    reason: str, expires_at: Any, verification_code: Any,
    grantor_user_id: Any,
) -> dict[str, Any]:
    """Create or safely re-resolve one grant for one execution step."""
    try:
        credential_id_int = int(credential_id)
        grantor_id = int(grantor_user_id)
    except (TypeError, ValueError) as exc:
        raise WorkflowCredentialShareError("Credential and grantor identities are required") from exc
    selector = selector_type.strip().lower()
    purpose = reason.strip()
    if selector not in {"staff", "job_title", "external"} or not purpose:
        raise WorkflowCredentialShareError("Selector and reason are required")
    capabilities = _permissions(permissions)
    credential = await db.fetch_one(
        "SELECT id FROM credentials WHERE id = %s AND company_id = %s AND revoked_at IS NULL AND archived_at IS NULL",
        (credential_id_int, company_id),
    )
    if credential is None:
        raise WorkflowCredentialShareError("Credential is unavailable for this company")

    lock_name = f"workflow-credential-share:{execution_id}:{step_identity}"
    async with db.acquire_lock(lock_name) as acquired:
        if not acquired:
            raise WorkflowCredentialShareError("Credential sharing is already in progress")
        # The workflow execution/step is the idempotency key. The small metadata
        # marker contains no recipient secret and makes pause/resume safe.
        marker = f"[workflow:{execution_id}:{step_identity}]"
        stored_reason = f"{purpose} {marker}"
        existing = await db.fetch_one(
            "SELECT id, selector_type FROM credential_standing_grants WHERE company_id = %s AND credential_id = %s AND purpose LIKE %s ORDER BY id DESC LIMIT 1",
            (company_id, credential_id_int, "%" + marker),
        )
        if existing:
            return {
                "grant_id": int(existing["id"]),
                "url": f"{_base_url()}/shared-credentials?credential={credential_id_int}",
                "kind": "standing",
            }

        if selector in {"staff", "job_title"}:
            try:
                target_staff_id = int(staff_id) if selector == "staff" else None
            except (TypeError, ValueError) as exc:
                raise WorkflowCredentialShareError("Staff selector requires a numeric staff ID") from exc
            expiry = _utc_datetime(expires_at)
            # Reviews remain mandatory even for explicitly non-expiring access.
            review_due = expiry or (datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=365))
            grant = await standing_credential_grants.create(
                credential_id=credential_id_int, company_id=company_id,
                selector_type=selector, staff_id=target_staff_id,
                job_title=str(job_title or "").strip() or None,
                capabilities=capabilities, purpose=stored_reason,
                grantor_user_id=grantor_id, approver_user_id=None,
                expires_at=expiry, review_due_at=review_due,
            )
            if grant is None:
                raise WorkflowCredentialShareError("Recipient is not an eligible verified member of this company")
            return {
                "grant_id": int(grant["id"]),
                "url": f"{_base_url()}/shared-credentials?credential={credential_id_int}",
                "kind": "standing",
            }

        expiry = _utc_datetime(expires_at, required=True)
        email = str(recipient_email or "").strip().lower()
        code = str(verification_code or "").strip()
        if not email or not code:
            raise WorkflowCredentialShareError("External shares require an email and independent verification code")
        token = secrets.token_urlsafe(32)
        grant = await credential_grants.create(
            credential_id=credential_id_int, company_id=company_id,
            staff_id=workflow_staff_id, grantor_user_id=grantor_id,
            recipient_user_id=None, recipient_email=email, reason=stored_reason,
            expires_at=expiry, token=token, verification_code=code,
        )
        if grant is None:
            raise WorkflowCredentialShareError("Credential is not eligible for an external one-time share")
        return {
            "grant_id": int(grant["id"]),
            "url": f"{_base_url()}/credential-share/{quote(token, safe='')}",
            "kind": "external_one_time",
        }
