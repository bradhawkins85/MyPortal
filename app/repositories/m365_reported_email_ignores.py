"""Persistence for user-reported email alerts technicians marked as ignored."""

from __future__ import annotations

from typing import Any

from app.core.database import db


async def list_ignored(company_id: int) -> dict[str, dict[str, Any]]:
    """Map ignored alert IDs to their ignore record for a company."""
    rows = await db.fetch_all(
        """
        SELECT alert_id, ignored_by, ignored_at FROM m365_reported_email_ignores
        WHERE company_id = %s
        """,
        (company_id,),
    )
    return {str(row["alert_id"]): dict(row) for row in rows}


async def ignore(company_id: int, alert_id: str, user_id: int | None) -> bool:
    """Mark an alert as ignored. Returns False when it was already ignored."""
    existing = await db.fetch_one(
        "SELECT id FROM m365_reported_email_ignores WHERE company_id = %s AND alert_id = %s",
        (company_id, alert_id),
    )
    if existing:
        return False
    await db.execute(
        """
        INSERT INTO m365_reported_email_ignores (company_id, alert_id, ignored_by)
        VALUES (%s, %s, %s)
        """,
        (company_id, alert_id, user_id),
    )
    return True


async def unignore(company_id: int, alert_id: str) -> None:
    """Show an ignored alert again."""
    await db.execute(
        "DELETE FROM m365_reported_email_ignores WHERE company_id = %s AND alert_id = %s",
        (company_id, alert_id),
    )
