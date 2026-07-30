"""Persistence for per-user Microsoft 365 contact integrations."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.core.database import db


def _normalise(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    result = dict(row)
    expires = result.get("token_expires_at")
    if isinstance(expires, datetime) and expires.tzinfo is None:
        result["token_expires_at"] = expires.replace(tzinfo=timezone.utc)
    return result


async def get_integration(user_id: int) -> dict[str, Any] | None:
    return _normalise(await db.fetch_one(
        "SELECT * FROM user_m365_contact_integrations WHERE user_id = %s", (user_id,)
    ))


async def upsert_integration(
    user_id: int, *, tenant_id: str, account_email: str | None,
    refresh_token: str, access_token: str | None, token_expires_at: datetime | None,
) -> None:
    expires = token_expires_at.replace(tzinfo=None) if token_expires_at else None
    existing = await get_integration(user_id)
    if existing:
        await db.execute(
            """UPDATE user_m365_contact_integrations
               SET tenant_id = %s, account_email = %s, refresh_token = %s,
                   access_token = %s, token_expires_at = %s, updated_at = UTC_TIMESTAMP(6)
               WHERE user_id = %s""",
            (tenant_id, account_email, refresh_token, access_token, expires, user_id),
        )
        return
    await db.execute(
        """INSERT INTO user_m365_contact_integrations
           (user_id, tenant_id, account_email, refresh_token, access_token, token_expires_at)
           VALUES (%s, %s, %s, %s, %s, %s)""",
        (user_id, tenant_id, account_email, refresh_token, access_token, expires),
    )


async def delete_integration(user_id: int) -> None:
    await db.execute("DELETE FROM user_m365_contact_integrations WHERE user_id = %s", (user_id,))
