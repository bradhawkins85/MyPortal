"""Client onboarding magic links and per-site company details."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from app.core.database import db

_SELECT = (
    "SELECT id, token_hash, client_name, recipient_email, status, expires_at, "
    "created_by_user_id, company_id, ticket_id, submission, error_message, "
    "submitted_at, approved_at, approved_by_user_id, created_at FROM client_onboardings"
)


def _normalise(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    record = dict(row)
    for key in ("id", "created_by_user_id", "company_id", "ticket_id", "approved_by_user_id"):
        if record.get(key) is not None:
            record[key] = int(record[key])
    raw = record.get("submission")
    if isinstance(raw, str) and raw:
        try:
            record["submission"] = json.loads(raw)
        except json.JSONDecodeError:
            record["submission"] = None
    return record


async def create(
    *,
    token_hash: str,
    client_name: str | None,
    recipient_email: str | None,
    expires_at: datetime,
    created_by_user_id: int | None,
) -> dict[str, Any]:
    onboarding_id = await db.execute_returning_lastrowid(
        """INSERT INTO client_onboardings
           (token_hash, client_name, recipient_email, status, expires_at, created_by_user_id)
           VALUES (%s, %s, %s, 'pending', %s, %s)""",
        (token_hash, client_name, recipient_email, expires_at, created_by_user_id),
    )
    return (await get_by_id(int(onboarding_id))) or {}


async def get_by_id(onboarding_id: int) -> dict[str, Any] | None:
    row = await db.fetch_one(
        _SELECT + " WHERE id = %s",
        (onboarding_id,),
    )
    return _normalise(row)


async def get_by_token_hash(token_hash: str) -> dict[str, Any] | None:
    row = await db.fetch_one(
        _SELECT + " WHERE token_hash = %s",
        (token_hash,),
    )
    return _normalise(row)


async def get_by_company_id(company_id: int) -> dict[str, Any] | None:
    row = await db.fetch_one(
        _SELECT + " WHERE company_id = %s ORDER BY id DESC LIMIT 1",
        (company_id,),
    )
    return _normalise(row)


async def list_recent(limit: int = 200) -> list[dict[str, Any]]:
    rows = await db.fetch_all(
        _SELECT + " ORDER BY created_at DESC, id DESC LIMIT %s",
        (int(limit),),
    )
    return [record for record in (_normalise(row) for row in rows) if record]


async def claim_for_processing(onboarding_id: int, submission: dict[str, Any]) -> bool:
    """Move a pending link to ``processing`` so a link can only be submitted once."""

    count = await db.execute_rowcount(
        """UPDATE client_onboardings SET status = 'processing', submission = %s
           WHERE id = %s AND status = 'pending'""",
        (json.dumps(submission), onboarding_id),
    )
    return count == 1


async def mark_submitted(
    onboarding_id: int,
    *,
    company_id: int,
    ticket_id: int | None,
    submitted_at: datetime,
) -> None:
    await db.execute(
        """UPDATE client_onboardings
           SET status = 'submitted', company_id = %s, ticket_id = %s, submitted_at = %s,
               error_message = NULL
           WHERE id = %s""",
        (company_id, ticket_id, submitted_at, onboarding_id),
    )


async def mark_failed(onboarding_id: int, *, error_message: str, company_id: int | None) -> None:
    await db.execute(
        """UPDATE client_onboardings SET status = 'failed', error_message = %s, company_id = %s
           WHERE id = %s""",
        (error_message[:500], company_id, onboarding_id),
    )


async def mark_approved(company_id: int, *, approved_by_user_id: int, approved_at: datetime) -> None:
    await db.execute(
        """UPDATE client_onboardings
           SET status = 'approved', approved_at = %s, approved_by_user_id = %s
           WHERE company_id = %s AND status = 'submitted'""",
        (approved_at, approved_by_user_id, company_id),
    )


async def approve_company(company_id: int) -> bool:
    """Clear the company's pending flag. Returns False when it was not pending."""

    count = await db.execute_rowcount(
        "UPDATE companies SET pending_approval = 0 WHERE id = %s AND pending_approval = 1",
        (company_id,),
    )
    return count == 1


async def enable_onboarding_contacts(company_id: int) -> int:
    return await db.execute_rowcount(
        "UPDATE staff SET enabled = 1 WHERE company_id = %s AND source = %s AND enabled = 0",
        (company_id, "client_onboarding"),
    )


async def revoke(onboarding_id: int) -> bool:
    count = await db.execute_rowcount(
        "UPDATE client_onboardings SET status = 'revoked' WHERE id = %s AND status = 'pending'",
        (onboarding_id,),
    )
    return count == 1


async def rotate_token(onboarding_id: int, *, token_hash: str, expires_at: datetime) -> bool:
    count = await db.execute_rowcount(
        """UPDATE client_onboardings SET token_hash = %s, expires_at = %s
           WHERE id = %s AND status = 'pending'""",
        (token_hash, expires_at, onboarding_id),
    )
    return count == 1


async def save_site_profile(
    *,
    address_id: int,
    company_id: int,
    phone: str | None,
    primary_contact_staff_id: int | None,
    timezone_name: str | None,
    weekly_hours: dict[str, Any] | None,
) -> None:
    await db.execute(
        """INSERT INTO company_site_profiles
           (address_id, company_id, phone, primary_contact_staff_id, timezone, weekly_hours)
           VALUES (%s, %s, %s, %s, %s, %s)""",
        (
            address_id,
            company_id,
            phone,
            primary_contact_staff_id,
            timezone_name,
            json.dumps(weekly_hours) if weekly_hours is not None else None,
        ),
    )


async def list_site_profiles(company_id: int) -> list[dict[str, Any]]:
    rows = await db.fetch_all(
        """SELECT p.address_id, p.company_id, p.phone, p.primary_contact_staff_id,
                  p.timezone, p.weekly_hours, a.label, a.street, a.city, a.state,
                  a.postcode, a.country, s.first_name, s.last_name, s.email,
                  s.mobile_phone
           FROM company_site_profiles p
           JOIN company_addresses a ON a.id = p.address_id
           LEFT JOIN staff s ON s.id = p.primary_contact_staff_id
           WHERE p.company_id = %s
           ORDER BY a.label, a.id""",
        (company_id,),
    )
    profiles = []
    for row in rows:
        record = dict(row)
        raw = record.get("weekly_hours")
        if isinstance(raw, str) and raw:
            try:
                record["weekly_hours"] = json.loads(raw)
            except json.JSONDecodeError:
                record["weekly_hours"] = None
        profiles.append(record)
    return profiles


async def ensure_new_client_status() -> None:
    """Recreate the ``new_client`` ticket status if an admin removed it."""

    if await db.fetch_one(
        "SELECT 1 FROM ticket_statuses WHERE tech_status = %s", ("new_client",)
    ):
        return
    await db.execute(
        """INSERT INTO ticket_statuses (tech_status, tech_label, public_status)
           VALUES (%s, %s, %s)""",
        ("new_client", "New Client", "New Client"),
    )
