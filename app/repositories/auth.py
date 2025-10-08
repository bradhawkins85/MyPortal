from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Optional

from app.core.database import db
from app.security.encryption import decrypt_secret, encrypt_secret


async def create_session(
    *,
    user_id: int,
    session_token: str,
    csrf_token: str,
    created_at: datetime,
    expires_at: datetime,
    last_seen_at: datetime,
    ip_address: str | None,
    user_agent: str | None,
    pending_totp_secret: str | None = None,
) -> dict[str, Any]:
    await db.execute(
        """
        INSERT INTO user_sessions (
            user_id,
            session_token,
            csrf_token,
            created_at,
            expires_at,
            last_seen_at,
            ip_address,
            user_agent,
            pending_totp_secret
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            user_id,
            session_token,
            csrf_token,
            created_at,
            expires_at,
            last_seen_at,
            ip_address,
            user_agent,
            pending_totp_secret,
        ),
    )
    return await get_session_by_token(session_token)


async def get_session_by_token(token: str) -> Optional[dict[str, Any]]:
    row = await db.fetch_one(
        "SELECT * FROM user_sessions WHERE session_token = %s", (token,)
    )
    return row


_SENTINEL = object()


async def update_session(
    session_id: int,
    *,
    last_seen_at: datetime | None = None,
    expires_at: datetime | None = None,
    csrf_token: str | None = None,
    pending_totp_secret: Any = _SENTINEL,
    is_active: Optional[bool] = None,
) -> None:
    updates: list[str] = []
    params: list[Any] = []
    if last_seen_at is not None:
        updates.append("last_seen_at = %s")
        params.append(last_seen_at)
    if expires_at is not None:
        updates.append("expires_at = %s")
        params.append(expires_at)
    if csrf_token is not None:
        updates.append("csrf_token = %s")
        params.append(csrf_token)
    if pending_totp_secret is not _SENTINEL:
        updates.append("pending_totp_secret = %s")
        params.append(pending_totp_secret)
    if is_active is not None:
        updates.append("is_active = %s")
        params.append(1 if is_active else 0)
    if not updates:
        return
    params.append(session_id)
    sql = f"UPDATE user_sessions SET {', '.join(updates)} WHERE id = %s"
    await db.execute(sql, tuple(params))


async def deactivate_session(session_id: int) -> None:
    await update_session(session_id, is_active=False)


async def list_active_sessions_for_user(user_id: int) -> list[dict[str, Any]]:
    rows = await db.fetch_all(
        """
        SELECT * FROM user_sessions
        WHERE user_id = %s AND is_active = 1
        ORDER BY last_seen_at DESC
        """,
        (user_id,),
    )
    return list(rows)


async def register_login_attempt(
    identifier: str, *, window_seconds: int, max_attempts: int
) -> bool:
    now = datetime.utcnow()
    row = await db.fetch_one(
        "SELECT * FROM login_rate_limits WHERE identifier = %s",
        (identifier,),
    )
    if not row:
        await db.execute(
            "INSERT INTO login_rate_limits (identifier, window_start, attempts) VALUES (%s, %s, %s)",
            (identifier, now, 1),
        )
        return True

    window_start = row["window_start"]
    attempts = int(row["attempts"])
    if isinstance(window_start, datetime):
        window_start_dt = window_start
    else:
        window_start_dt = datetime.strptime(str(window_start), "%Y-%m-%d %H:%M:%S")

    if now - window_start_dt > timedelta(seconds=window_seconds):
        await db.execute(
            "UPDATE login_rate_limits SET window_start = %s, attempts = %s WHERE identifier = %s",
            (now, 1, identifier),
        )
        return True

    attempts += 1
    await db.execute(
        "UPDATE login_rate_limits SET attempts = %s WHERE identifier = %s",
        (attempts, identifier),
    )
    return attempts <= max_attempts


async def clear_login_attempts(identifier: str) -> None:
    await db.execute(
        "DELETE FROM login_rate_limits WHERE identifier = %s",
        (identifier,),
    )


async def create_password_reset_token(
    *, user_id: int, token: str, expires_at: datetime
) -> None:
    await db.execute(
        """
        INSERT INTO password_tokens (token, user_id, expires_at, used)
        VALUES (%s, %s, %s, 0)
        """,
        (token, user_id, expires_at),
    )


async def get_password_reset_token(token: str) -> Optional[dict[str, Any]]:
    row = await db.fetch_one(
        "SELECT * FROM password_tokens WHERE token = %s",
        (token,),
    )
    return row


async def mark_password_reset_token_used(token: str) -> None:
    await db.execute(
        "UPDATE password_tokens SET used = 1 WHERE token = %s",
        (token,),
    )


async def get_totp_authenticators(user_id: int) -> list[dict[str, Any]]:
    rows = await db.fetch_all(
        "SELECT id, name, secret FROM user_totp_authenticators WHERE user_id = %s",
        (user_id,),
    )
    decoded: list[dict[str, Any]] = []
    for row in rows:
        secret = decrypt_secret(row["secret"])
        decoded.append({"id": row["id"], "name": row["name"], "secret": secret})
    return decoded


async def create_totp_authenticator(
    *, user_id: int, name: str, secret: str
) -> dict[str, Any]:
    encrypted = encrypt_secret(secret)
    await db.execute(
        """
        INSERT INTO user_totp_authenticators (user_id, name, secret)
        VALUES (%s, %s, %s)
        """,
        (user_id, name, encrypted),
    )
    rows = await db.fetch_all(
        "SELECT id, name, secret FROM user_totp_authenticators WHERE user_id = %s ORDER BY id DESC LIMIT 1",
        (user_id,),
    )
    if not rows:
        raise RuntimeError("Failed to create TOTP authenticator")
    row = rows[0]
    return {"id": row["id"], "name": row["name"], "secret": decrypt_secret(row["secret"])}


async def delete_totp_authenticator(user_id: int, authenticator_id: int) -> None:
    await db.execute(
        "DELETE FROM user_totp_authenticators WHERE user_id = %s AND id = %s",
        (user_id, authenticator_id),
    )
