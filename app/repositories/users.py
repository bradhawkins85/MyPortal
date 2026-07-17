from __future__ import annotations

import re
from datetime import datetime
from typing import Any, List, Optional

from app.core.database import db
from app.core.logging import log_error, log_info
from app.security.passwords import hash_password

_ALLOWED_UPDATE_COLUMNS = {
    "email",
    "first_name",
    "last_name",
    "mobile_phone",
    "company_id",
    "is_super_admin",
    "booking_link_url",
    "email_signature",
    "matrix_user_id",
    "last_login_at",
    "is_active",
    "email_verified_at",
    "force_password_change",
}


def _build_safe_update_clause(updates: dict[str, Any]) -> tuple[str, list[Any]]:
    unknown = [column for column in updates if column not in _ALLOWED_UPDATE_COLUMNS]
    if unknown:
        raise ValueError(f"Unsupported update fields: {', '.join(sorted(unknown))}")
    items = list(updates.items())
    columns = ", ".join(f"{column} = %s" for column, _ in items)
    return columns, [value for _, value in items]


async def get_user_by_email(email: str) -> Optional[dict[str, Any]]:
    row = await db.fetch_one("SELECT * FROM users WHERE email = %s", (email,))
    return row


async def get_user_by_phone(phone: str) -> Optional[dict[str, Any]]:
    """Return the first user whose mobile number matches ``phone``.

    Phone numbers are compared after removing common formatting characters so
    tray submissions can match stored numbers despite formatting differences.
    """
    normalised_phone = re.sub(r"[\s\-\(\)\+]", "", phone.strip())
    if not normalised_phone:
        return None
    return await db.fetch_one(
        """
        SELECT * FROM users
        WHERE REPLACE(REPLACE(REPLACE(REPLACE(REPLACE(COALESCE(mobile_phone, ''), ' ', ''), '-', ''), '(', ''), ')', ''), '+', '') = %s
        ORDER BY id ASC
        LIMIT 1
        """,
        (normalised_phone,),
    )


async def get_user_by_id(user_id: int) -> Optional[dict[str, Any]]:
    row = await db.fetch_one("SELECT * FROM users WHERE id = %s", (user_id,))
    return row


async def count_users() -> int:
    row = await db.fetch_one("SELECT COUNT(*) AS count FROM users")
    return int(row["count"]) if row else 0


async def list_users() -> List[dict[str, Any]]:
    rows = await db.fetch_all("SELECT * FROM users ORDER BY id DESC")
    return list(rows)


async def list_users_for_company(company_id: int) -> List[dict[str, Any]]:
    rows = await db.fetch_all(
        """
        SELECT id, email
        FROM users
        WHERE company_id = %s
        ORDER BY LOWER(email), id
        """,
        (company_id,),
    )
    return [dict(row) for row in rows]


async def create_user(
    *,
    email: str,
    password: str,
    first_name: str | None = None,
    last_name: str | None = None,
    mobile_phone: str | None = None,
    company_id: int | None = None,
    is_super_admin: bool = False,
) -> dict[str, Any]:
    log_info(
        "Creating user",
        email=email,
        company_id=company_id,
        is_super_admin=is_super_admin,
    )
    password_hash = hash_password(password)
    await db.execute(
        """
        INSERT INTO users (email, password_hash, first_name, last_name, mobile_phone, company_id, is_super_admin)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        """,
        (
            email,
            password_hash,
            first_name,
            last_name,
            mobile_phone,
            company_id,
            1 if is_super_admin else 0,
        ),
    )
    row = await get_user_by_email(email)
    if not row:
        log_error("Failed to create user - user not found after insert", email=email)
        raise RuntimeError("Failed to create user")
    log_info("User created successfully", user_id=row.get("id"), email=email)
    return row


async def update_user(user_id: int, **updates: Any) -> dict[str, Any]:
    if not updates:
        user = await get_user_by_id(user_id)
        if not user:
            raise ValueError("User not found")
        return user

    log_info("Updating user", user_id=user_id, fields=list(updates.keys()))
    columns, params = _build_safe_update_clause(updates)
    params.append(user_id)
    await db.execute(f"UPDATE users SET {columns} WHERE id = %s", tuple(params))
    updated = await get_user_by_id(user_id)
    if not updated:
        log_error("User not found after update", user_id=user_id)
        raise ValueError("User not found after update")
    log_info("User updated successfully", user_id=user_id)
    return updated


async def record_login(user_id: int, logged_in_at: datetime) -> dict[str, Any]:
    return await update_user(user_id, last_login_at=logged_in_at)


async def delete_user(user_id: int) -> None:
    log_info("Deleting user", user_id=user_id)
    await db.execute("DELETE FROM users WHERE id = %s", (user_id,))
    log_info("User deleted successfully", user_id=user_id)


async def set_user_password(user_id: int, password: str) -> None:
    log_info("Setting user password", user_id=user_id)
    password_hash = hash_password(password)
    await db.execute(
        "UPDATE users SET password_hash = %s, force_password_change = 0 WHERE id = %s",
        (password_hash, user_id),
    )
    log_info("User password updated successfully", user_id=user_id)
