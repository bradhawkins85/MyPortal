from __future__ import annotations

import re
import unicodedata
from datetime import datetime
from typing import Any, Literal

from app.core.database import db
from app.repositories import vault

Capability = Literal["enumerate", "reveal", "share", "administer"]
_CAPABILITY_COLUMNS: dict[Capability, str] = {
    "enumerate": "can_enumerate",
    "reveal": "can_reveal",
    "share": "can_share",
    "administer": "can_administer",
}


def normalize_job_title(value: str) -> str:
    """Produce one locale-independent key for stored and current staff titles."""
    normalized = unicodedata.normalize("NFKC", value).casefold().strip()
    return re.sub(r"\s+", " ", normalized)


async def eligible_staff(company_id: int, job_title: str) -> list[dict[str, Any]]:
    """Return only unambiguous, explicitly linked, active portal identities."""
    key = normalize_job_title(job_title)
    if not key:
        return []
    rows = await db.fetch_all(
        "SELECT s.id AS staff_id, s.first_name, s.last_name, s.job_title, u.id AS user_id, u.email "
        "FROM staff s JOIN users u ON u.id = s.portal_user_id "
        "JOIN user_companies uc ON uc.user_id = u.id AND uc.company_id = s.company_id "
        "WHERE s.company_id = %s AND s.enabled = 1 AND COALESCE(s.is_ex_staff, 0) = 0 "
        "AND u.is_active = 1 AND u.email_verified_at IS NOT NULL AND s.portal_user_id IS NOT NULL "
        "AND NOT EXISTS (SELECT 1 FROM staff duplicate WHERE duplicate.company_id = s.company_id "
        "AND duplicate.portal_user_id = s.portal_user_id AND duplicate.enabled = 1 AND duplicate.id <> s.id)",
        (company_id,),
    )
    return [row for row in rows if normalize_job_title(str(row.get("job_title") or "")) == key]


async def create(
    *, credential_id: int, company_id: int, selector_type: str,
    staff_id: int | None, job_title: str | None, capabilities: set[Capability],
    purpose: str, grantor_user_id: int, approver_user_id: int | None,
    expires_at: datetime | None, review_due_at: datetime,
) -> dict[str, Any] | None:
    item = await db.fetch_one(
        "SELECT id, credential_class FROM credentials WHERE id = %s AND company_id = %s "
        "AND revoked_at IS NULL AND archived_at IS NULL", (credential_id, company_id)
    )
    if item is None:
        return None
    title_key = normalize_job_title(job_title or "") if selector_type == "job_title" else None
    if selector_type == "staff":
        target = await db.fetch_one(
            "SELECT s.id FROM staff s JOIN users u ON u.id = s.portal_user_id "
            "JOIN user_companies uc ON uc.user_id = u.id AND uc.company_id = s.company_id "
            "WHERE s.id = %s AND s.company_id = %s AND s.enabled = 1 "
            "AND COALESCE(s.is_ex_staff, 0) = 0 AND u.is_active = 1 "
            "AND u.email_verified_at IS NOT NULL AND NOT EXISTS (SELECT 1 FROM staff duplicate "
            "WHERE duplicate.company_id = s.company_id AND duplicate.portal_user_id = s.portal_user_id "
            "AND duplicate.enabled = 1 AND duplicate.id <> s.id)", (staff_id, company_id)
        )
        if target is None:
            return None
    elif selector_type == "job_title":
        if not title_key or not await eligible_staff(company_id, job_title or ""):
            return None
    else:
        return None
    if item["credential_class"] == "admin":
        if approver_user_id is None or approver_user_id == grantor_user_id:
            return None
        approver = await db.fetch_one(
            "SELECT u.id FROM users u JOIN user_companies uc ON uc.user_id = u.id "
            "WHERE u.id = %s AND uc.company_id = %s AND u.is_active = 1 AND uc.is_admin = 1",
            (approver_user_id, company_id),
        )
        if approver is None:
            return None
    grant_id = await db.execute_returning_lastrowid(
        "INSERT INTO credential_standing_grants (credential_id, company_id, selector_type, staff_id, "
        "job_title, job_title_key, can_enumerate, can_reveal, can_share, can_administer, purpose, "
        "grantor_user_id, approver_user_id, expires_at, review_due_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (credential_id, company_id, selector_type, staff_id, job_title, title_key,
         int("enumerate" in capabilities), int("reveal" in capabilities),
         int("share" in capabilities), int("administer" in capabilities), purpose,
         grantor_user_id, approver_user_id, expires_at, review_due_at),
    )
    return await get(grant_id)


async def get(grant_id: int) -> dict[str, Any] | None:
    return await db.fetch_one("SELECT * FROM credential_standing_grants WHERE id = %s", (grant_id,))


async def list_for_credential(company_id: int, credential_id: int) -> list[dict[str, Any]]:
    return await db.fetch_all(
        "SELECT * FROM credential_standing_grants WHERE company_id = %s AND credential_id = %s "
        "ORDER BY revoked_at IS NOT NULL, created_at DESC", (company_id, credential_id)
    )


async def resolve(user_id: int, company_id: int, capability: Capability) -> list[dict[str, Any]]:
    """Resolve direct and title policies from current authoritative records each time."""
    column = _CAPABILITY_COLUMNS[capability]
    rows = await db.fetch_all(
        "SELECT DISTINCT g.*, c.name, c.username, c.credential_class, c.current_version, c.review_on "
        "FROM credential_standing_grants g JOIN credentials c ON c.id = g.credential_id "
        "JOIN staff s ON s.company_id = g.company_id AND s.portal_user_id = %s "
        "JOIN users u ON u.id = s.portal_user_id JOIN user_companies uc ON uc.user_id = u.id "
        "AND uc.company_id = g.company_id WHERE g.company_id = %s AND g." + column + " = 1 "
        "AND g.revoked_at IS NULL AND (g.expires_at IS NULL OR g.expires_at > CURRENT_TIMESTAMP) "
        "AND g.review_due_at > CURRENT_TIMESTAMP AND c.revoked_at IS NULL AND c.archived_at IS NULL "
        "AND s.enabled = 1 AND COALESCE(s.is_ex_staff, 0) = 0 AND u.is_active = 1 "
        "AND u.email_verified_at IS NOT NULL AND NOT EXISTS (SELECT 1 FROM staff duplicate "
        "WHERE duplicate.company_id = s.company_id AND duplicate.portal_user_id = s.portal_user_id "
        "AND duplicate.enabled = 1 AND duplicate.id <> s.id) "
        "AND ((g.selector_type = 'staff' AND g.staff_id = s.id) OR g.selector_type = 'job_title')",
        (user_id, company_id),
    )
    result = []
    for row in rows:
        if row["selector_type"] == "job_title" and normalize_job_title(str(row.get("job_title") or "")) != row.get("job_title_key"):
            # Stored display title/key mismatch indicates tampering or a legacy bad row.
            continue
        if row["selector_type"] == "job_title":
            staff = await db.fetch_one("SELECT job_title FROM staff WHERE company_id = %s AND portal_user_id = %s AND enabled = 1", (company_id, user_id))
            if not staff or normalize_job_title(str(staff.get("job_title") or "")) != row["job_title_key"]:
                continue
        result.append(row)
    return result


async def reveal(user_id: int, company_id: int, credential_id: int) -> tuple[dict[str, Any], str] | None:
    matches = [row for row in await resolve(user_id, company_id, "reveal") if int(row["credential_id"]) == credential_id]
    if not matches:
        return None
    # Re-check active grants atomically immediately before decrypting. A revoke that wins blocks reveal.
    ids = [int(row["id"]) for row in matches]
    placeholders = ",".join(["%s"] * len(ids))
    changed = await db.execute_rowcount(
        "UPDATE credential_standing_grants SET last_resolved_at = CURRENT_TIMESTAMP WHERE id IN (" + placeholders + ") "
        "AND revoked_at IS NULL AND (expires_at IS NULL OR expires_at > CURRENT_TIMESTAMP) "
        "AND review_due_at > CURRENT_TIMESTAMP", tuple(ids)
    )
    if changed < 1:
        return None
    row = matches[0]
    secret = await vault.reveal(company_id, credential_id, int(row["current_version"]))
    return (row, secret[1]) if secret else None


async def revoke(grant_id: int, company_id: int, actor_user_id: int) -> dict[str, Any] | None:
    changed = await db.execute_rowcount(
        "UPDATE credential_standing_grants SET revoked_at = CURRENT_TIMESTAMP, revoked_by_user_id = %s "
        "WHERE id = %s AND company_id = %s AND revoked_at IS NULL",
        (actor_user_id, grant_id, company_id),
    )
    return await get(grant_id) if changed == 1 else None
