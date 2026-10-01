from __future__ import annotations

from typing import Any

from app.core.database import db


async def get_settings(user_id: int) -> dict[str, Any] | None:
    return await db.fetch_one(
        "SELECT * FROM user_click_to_call_settings WHERE user_id = %s",
        (user_id,),
    )


async def upsert_settings(
    user_id: int,
    *,
    enabled: bool,
    phone_ip: str | None,
    login_username: str | None,
    password_encrypted: str | None,
) -> dict[str, Any]:
    await db.execute(
        """
        INSERT INTO user_click_to_call_settings
            (user_id, enabled, phone_ip, login_username, password_encrypted)
        VALUES (%s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            enabled = VALUES(enabled),
            phone_ip = VALUES(phone_ip),
            login_username = VALUES(login_username),
            password_encrypted = COALESCE(VALUES(password_encrypted), password_encrypted)
        """,
        (user_id, 1 if enabled else 0, phone_ip, login_username, password_encrypted),
    )
    result = await get_settings(user_id)
    if result is None:  # pragma: no cover - database invariant
        raise RuntimeError("Click-to-call settings were not saved")
    return result
