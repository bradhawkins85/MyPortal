from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Iterable, List, Optional

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
    "passkey_user_handle",
    "ai_opt_out",
    "ai_opt_out_at",
    "policies_accepted_version",
    "policies_accepted_at",
}


def _build_safe_update_clause(updates: dict[str, Any]) -> tuple[str, list[Any]]:
    unknown = [column for column in updates if column not in _ALLOWED_UPDATE_COLUMNS]
    if unknown:
        raise ValueError(f"Unsupported update fields: {', '.join(sorted(unknown))}")
    items = list(updates.items())
    columns = ", ".join(f"{column} = %s" for column, _ in items)
    return columns, [value for _, value in items]


async def get_user_by_email(email: str) -> Optional[dict[str, Any]]:
    clean_email = str(email or "").strip()
    if not clean_email:
        return None
    row = await db.fetch_one(
        """
        SELECT * FROM users
        WHERE LOWER(email) = LOWER(%s)
        ORDER BY id ASC
        LIMIT 1
        """,
        (clean_email,),
    )
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


async def list_active_users_for_admin() -> List[dict[str, Any]]:
    """Return active portal accounts with the context needed by the admin UI."""
    rows = await db.fetch_all(
        """
        SELECT u.id, u.email, u.first_name, u.last_name, u.mobile_phone,
               u.company_id, u.is_super_admin, u.last_login_at,
               u.ai_opt_out, u.ai_opt_out_at,
               c.name AS company_name
        FROM users AS u
        LEFT JOIN companies AS c ON c.id = u.company_id
        WHERE u.is_active = 1
        ORDER BY LOWER(COALESCE(u.last_name, '')),
                 LOWER(COALESCE(u.first_name, '')), LOWER(u.email), u.id
        """
    )
    return [dict(row) for row in rows]


async def count_active_super_admins() -> int:
    row = await db.fetch_one(
        "SELECT COUNT(*) AS count FROM users WHERE is_active = 1 AND is_super_admin = 1"
    )
    return int(row["count"]) if row else 0


async def list_users_for_company(company_id: int) -> List[dict[str, Any]]:
    rows = await db.fetch_all(
        """
        SELECT id, email, first_name, last_name, is_super_admin
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
    # Columns are produced by _build_safe_update_clause's explicit allowlist; values remain bound.
    await db.execute(  # nosec B608
        f"UPDATE users SET {columns} WHERE id = %s",  # nosec B608
        tuple(params),
    )
    updated = await get_user_by_id(user_id)
    if not updated:
        log_error("User not found after update", user_id=user_id)
        raise ValueError("User not found after update")
    log_info("User updated successfully", user_id=user_id)
    return updated


def _clean_user_ids(user_ids: Iterable[Any]) -> list[int]:
    cleaned: set[int] = set()
    for value in user_ids:
        try:
            identifier = int(value)
        except (TypeError, ValueError):
            continue
        if identifier > 0:
            cleaned.add(identifier)
    return sorted(cleaned)


async def list_ai_opted_out_user_ids(user_ids: Iterable[Any]) -> set[int]:
    """Return the subset of ``user_ids`` that have opted out of AI processing."""

    identifiers = _clean_user_ids(user_ids)
    if not identifiers or not db.is_connected():
        return set()
    placeholders = ", ".join(["%s"] * len(identifiers))
    # Only "%s" placeholders are interpolated; every identifier stays bound.
    rows = await db.fetch_all(
        "SELECT id FROM users WHERE ai_opt_out = 1 AND id IN (" + placeholders + ")",  # nosec B608
        tuple(identifiers),
    )
    return {int(row["id"]) for row in rows or []}


async def ai_opt_out_exists_for_email(email: str | None) -> bool:
    """Return True when any user with ``email`` has opted out of AI processing."""

    clean_email = str(email or "").strip()
    if not clean_email or not db.is_connected():
        return False
    row = await db.fetch_one(
        "SELECT id FROM users WHERE ai_opt_out = 1 AND LOWER(email) = LOWER(%s) LIMIT 1",
        (clean_email,),
    )
    return bool(row)


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
