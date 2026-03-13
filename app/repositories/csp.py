from __future__ import annotations

from datetime import datetime
from typing import Any

from app.core.database import db


def _normalise(row: dict[str, Any]) -> dict[str, Any]:
    normalised = dict(row)
    for key in ("expires_at", "created_at", "updated_at"):
        value = normalised.get(key)
        if isinstance(value, datetime):
            normalised[key] = value.replace(tzinfo=None)
    if "user_id" in normalised and normalised["user_id"] is not None:
        normalised["user_id"] = int(normalised["user_id"])
    return normalised


async def get_session(user_id: int) -> dict[str, Any] | None:
    row = await db.fetch_one(
        "SELECT * FROM admin_csp_sessions WHERE user_id = %s",
        (user_id,),
    )
    return _normalise(row) if row else None


async def upsert_session(
    *,
    user_id: int,
    access_token: str,
    refresh_token: str | None,
    expires_at: datetime | None,
) -> dict[str, Any]:
    await db.execute(
        """
        INSERT INTO admin_csp_sessions (user_id, access_token, refresh_token, expires_at)
        VALUES (%s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            access_token  = VALUES(access_token),
            refresh_token = VALUES(refresh_token),
            expires_at    = VALUES(expires_at),
            updated_at    = CURRENT_TIMESTAMP
        """,
        (user_id, access_token, refresh_token, expires_at),
    )
    session = await get_session(user_id)
    if not session:
        raise RuntimeError("Failed to persist CSP session")
    return session


async def delete_session(user_id: int) -> None:
    await db.execute(
        "DELETE FROM admin_csp_sessions WHERE user_id = %s",
        (user_id,),
    )
