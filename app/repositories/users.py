from __future__ import annotations

from typing import Any, List, Optional

from app.core.database import db
from app.security.passwords import hash_password


async def get_user_by_email(email: str) -> Optional[dict[str, Any]]:
    row = await db.fetch_one("SELECT * FROM users WHERE email = %s", (email,))
    return row


async def get_user_by_id(user_id: int) -> Optional[dict[str, Any]]:
    row = await db.fetch_one("SELECT * FROM users WHERE id = %s", (user_id,))
    return row


async def list_users() -> List[dict[str, Any]]:
    rows = await db.fetch_all("SELECT * FROM users ORDER BY id DESC")
    return list(rows)


async def create_user(
    *,
    email: str,
    password: str,
    first_name: str | None = None,
    last_name: str | None = None,
    mobile_phone: str | None = None,
    company_id: int | None = None,
) -> dict[str, Any]:
    password_hash = hash_password(password)
    await db.execute(
        """
        INSERT INTO users (email, password_hash, first_name, last_name, mobile_phone, company_id)
        VALUES (%s, %s, %s, %s, %s, %s)
        """,
        (email, password_hash, first_name, last_name, mobile_phone, company_id),
    )
    row = await get_user_by_email(email)
    if not row:
        raise RuntimeError("Failed to create user")
    return row


async def update_user(user_id: int, **updates: Any) -> dict[str, Any]:
    if not updates:
        user = await get_user_by_id(user_id)
        if not user:
            raise ValueError("User not found")
        return user

    columns = ", ".join(f"{column} = %s" for column in updates.keys())
    params = list(updates.values()) + [user_id]
    await db.execute(f"UPDATE users SET {columns} WHERE id = %s", tuple(params))
    updated = await get_user_by_id(user_id)
    if not updated:
        raise ValueError("User not found after update")
    return updated


async def delete_user(user_id: int) -> None:
    await db.execute("DELETE FROM users WHERE id = %s", (user_id,))
