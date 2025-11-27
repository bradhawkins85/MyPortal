from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from app.core.database import db


async def list_user_permissions(user_id: int, company_id: int) -> list[str]:
    """Get list of permission strings directly assigned to a user for a company."""
    rows = await db.fetch_all(
        """
        SELECT permission
        FROM user_permissions
        WHERE user_id = %s AND company_id = %s
        ORDER BY permission
        """,
        (user_id, company_id),
    )
    return [row["permission"] for row in rows]


async def get_user_permission(user_id: int, company_id: int, permission: str) -> Optional[dict[str, Any]]:
    """Get a specific user permission record."""
    row = await db.fetch_one(
        """
        SELECT id, user_id, company_id, permission, created_at, created_by
        FROM user_permissions
        WHERE user_id = %s AND company_id = %s AND permission = %s
        """,
        (user_id, company_id, permission),
    )
    return dict(row) if row else None


async def add_user_permission(
    user_id: int,
    company_id: int,
    permission: str,
    created_by: Optional[int] = None,
) -> dict[str, Any]:
    """Add a permission directly to a user for a company."""
    now = datetime.utcnow()
    await db.execute(
        """
        INSERT INTO user_permissions (user_id, company_id, permission, created_at, created_by)
        VALUES (%s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE permission = permission
        """,
        (user_id, company_id, permission, now, created_by),
    )
    result = await get_user_permission(user_id, company_id, permission)
    if not result:
        raise RuntimeError("Failed to create user permission")
    return result


async def remove_user_permission(user_id: int, company_id: int, permission: str) -> None:
    """Remove a permission from a user for a company."""
    await db.execute(
        """
        DELETE FROM user_permissions
        WHERE user_id = %s AND company_id = %s AND permission = %s
        """,
        (user_id, company_id, permission),
    )


async def set_user_permissions(
    user_id: int,
    company_id: int,
    permissions: list[str],
    created_by: Optional[int] = None,
) -> list[str]:
    """
    Set the exact list of permissions for a user in a company.
    This replaces all existing user permissions with the provided list.
    """
    # Get current permissions
    current_permissions = await list_user_permissions(user_id, company_id)
    current_set = set(current_permissions)
    new_set = set(permissions)

    # Add new permissions
    to_add = new_set - current_set
    for permission in to_add:
        await add_user_permission(user_id, company_id, permission, created_by)

    # Remove permissions no longer in the list
    to_remove = current_set - new_set
    for permission in to_remove:
        await remove_user_permission(user_id, company_id, permission)

    return sorted(new_set)


async def clear_user_permissions(user_id: int, company_id: int) -> None:
    """Remove all direct permissions for a user in a company."""
    await db.execute(
        """
        DELETE FROM user_permissions
        WHERE user_id = %s AND company_id = %s
        """,
        (user_id, company_id),
    )
