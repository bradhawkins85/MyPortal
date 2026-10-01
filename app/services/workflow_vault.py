"""Least-privilege bridge from staff workflows to the credential vault."""

from __future__ import annotations

from datetime import date
from typing import Any

from app.core.database import db
from app.repositories import vault as vault_repo
from app.security import vault


class WorkflowVaultError(RuntimeError):
    """A deliberately non-sensitive workflow-facing vault failure."""


def _date(value: Any, label: str) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise WorkflowVaultError(f"{label} must be an ISO date (YYYY-MM-DD)") from exc


async def create_for_staff_workflow(
    *, company_id: int, staff_id: int, execution_id: int, step_identity: str,
    name: str, username: str | None, credential_class: str, plaintext: str,
    owner: str | None, expires_on: Any = None, review_on: Any = None,
    asset_id: int | None = None, ticket_id: int | None = None,
) -> dict[str, int]:
    """Create once within the workflow company; no reveal/update authority is exposed."""
    if not plaintext:
        raise WorkflowVaultError("The approved prior-step secret is missing")
    if not name.strip():
        raise WorkflowVaultError("Credential name is required")
    if credential_class not in {"user", "shared", "service", "device", "other"}:
        raise WorkflowVaultError("Unsupported credential class")
    vault.ensure_configured()
    if not await vault_repo.credential_feature_enabled(company_id):
        raise WorkflowVaultError("The credential vault is disabled for this company")

    lock_name = f"workflow-vault:{execution_id}:{step_identity}"
    async with db.acquire_lock(lock_name) as acquired:
        if not acquired:
            raise WorkflowVaultError("Credential creation is already in progress")
        existing = await vault_repo.get_workflow_credential(
            company_id, execution_id, step_identity
        )
        if existing:
            return {"credential_id": int(existing["id"]), "credential_version": int(existing["current_version"])}
        links = [("staff", staff_id)]
        if asset_id is not None:
            links.append(("asset", asset_id))
        if ticket_id is not None:
            links.append(("ticket", ticket_id))
        try:
            result = await vault_repo.create_credential(
                company_id=company_id, name=name.strip(), username=username,
                credential_class=credential_class, owner=owner,
                intended_recipient=None, expires_on=_date(expires_on, "Expiry date"),
                review_on=_date(review_on, "Review date"), plaintext=plaintext,
                created_by=None, links=links, workflow_execution_id=execution_id,
                workflow_step_identity=step_identity,
            )
        except Exception:
            # Repository calls autocommit; remove any header if a later encrypted-version/link
            # write failed. Cascades ensure callers never observe a partial credential.
            partial = await vault_repo.get_workflow_credential(company_id, execution_id, step_identity)
            if partial:
                await db.execute(
                    "DELETE FROM credentials WHERE id = %s AND company_id = %s AND workflow_execution_id = %s AND workflow_step_identity = %s",
                    (partial["id"], company_id, execution_id, step_identity),
                )
            raise WorkflowVaultError("Credential creation failed") from None
        return {"credential_id": int(result["id"]), "credential_version": int(result["current_version"])}
