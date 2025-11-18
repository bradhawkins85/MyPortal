from __future__ import annotations

import json
from datetime import datetime
from typing import Any, List, Optional

from app.core.database import db
from app.repositories import users as user_repo
from app.repositories import user_permissions as user_permissions_repo

_VALID_STATUSES = {"invited", "active", "suspended"}


async def list_company_memberships(company_id: int) -> list[dict[str, Any]]:
    rows = await db.fetch_all(
        """
        SELECT m.*, u.email AS user_email, u.first_name, u.last_name, r.name AS role_name, r.permissions
        FROM company_memberships AS m
        INNER JOIN users AS u ON u.id = m.user_id
        INNER JOIN roles AS r ON r.id = m.role_id
        WHERE m.company_id = %s
        ORDER BY u.email
        """,
        (company_id,),
    )
    result = []
    for row in rows:
        membership = _normalise_membership(row)
        # Add user-specific permissions
        user_permissions = await user_permissions_repo.list_user_permissions(
            membership["user_id"], company_id
        )
        membership["user_permissions"] = user_permissions
        # Combine role and user permissions for total permissions
        all_permissions = set(membership.get("permissions", [])) | set(user_permissions)
        membership["combined_permissions"] = sorted(all_permissions)
        result.append(membership)
    return result


async def get_membership_by_id(membership_id: int) -> Optional[dict[str, Any]]:
    row = await db.fetch_one(
        """
        SELECT m.*, u.email AS user_email, u.first_name, u.last_name, r.name AS role_name, r.permissions
        FROM company_memberships AS m
        INNER JOIN users AS u ON u.id = m.user_id
        INNER JOIN roles AS r ON r.id = m.role_id
        WHERE m.id = %s
        """,
        (membership_id,),
    )
    if not row:
        return None
    membership = _normalise_membership(row)
    # Add user-specific permissions
    user_permissions = await user_permissions_repo.list_user_permissions(
        membership["user_id"], membership["company_id"]
    )
    membership["user_permissions"] = user_permissions
    # Combine role and user permissions
    all_permissions = set(membership.get("permissions", [])) | set(user_permissions)
    membership["combined_permissions"] = sorted(all_permissions)
    return membership


async def get_membership_by_company_user(company_id: int, user_id: int) -> Optional[dict[str, Any]]:
    row = await db.fetch_one(
        """
        SELECT m.*, u.email AS user_email, u.first_name, u.last_name, r.name AS role_name, r.permissions
        FROM company_memberships AS m
        INNER JOIN users AS u ON u.id = m.user_id
        INNER JOIN roles AS r ON r.id = m.role_id
        WHERE m.company_id = %s AND m.user_id = %s
        """,
        (company_id, user_id),
    )
    if not row:
        return None
    return _normalise_membership(row)


async def list_memberships_for_user(user_id: int, *, status: str | None = "active") -> List[dict[str, Any]]:
    filters = ["m.user_id = %s"]
    params: list[Any] = [user_id]
    if status is not None:
        filters.append("m.status = %s")
        params.append(status)
    where_clause = " AND ".join(filters)
    rows = await db.fetch_all(
        f"""
        SELECT m.*, u.email AS user_email, u.first_name, u.last_name, r.name AS role_name, r.permissions
        FROM company_memberships AS m
        INNER JOIN users AS u ON u.id = m.user_id
        INNER JOIN roles AS r ON r.id = m.role_id
        WHERE {where_clause}
        ORDER BY m.company_id
        """,
        tuple(params),
    )
    return [_normalise_membership(row) for row in rows]


async def create_membership(
    *,
    company_id: int,
    user_id: int,
    role_id: int,
    status: str = "active",
    invited_by: int | None = None,
) -> dict[str, Any]:
    if status not in _VALID_STATUSES:
        raise ValueError("Invalid membership status")
    now = datetime.utcnow()
    joined_at: datetime | None = None
    if status == "active":
        joined_at = now
    await db.execute(
        """
        INSERT INTO company_memberships (company_id, user_id, role_id, status, invited_by, invited_at, joined_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        """,
        (
            company_id,
            user_id,
            role_id,
            status,
            invited_by,
            now,
            joined_at,
        ),
    )
    created = await get_membership_by_company_user(company_id, user_id)
    if not created:
        raise RuntimeError("Failed to create membership")
    return created


async def update_membership(membership_id: int, **updates: Any) -> dict[str, Any]:
    if not updates:
        membership = await get_membership_by_id(membership_id)
        if not membership:
            raise ValueError("Membership not found")
        return membership

    params: list[Any] = []
    columns: list[str] = []
    status = updates.get("status")
    if status is not None:
        if status not in _VALID_STATUSES:
            raise ValueError("Invalid membership status")
    for column, value in updates.items():
        if column == "status" and value == "active":
            columns.append("joined_at = IFNULL(joined_at, %s)")
            params.append(datetime.utcnow())
        columns.append(f"{column} = %s")
        params.append(value)
    params.append(membership_id)
    sql = f"UPDATE company_memberships SET {', '.join(columns)} WHERE id = %s"
    await db.execute(sql, tuple(params))
    updated = await get_membership_by_id(membership_id)
    if not updated:
        raise ValueError("Membership not found after update")
    return updated


async def delete_membership(membership_id: int) -> None:
    await db.execute("DELETE FROM company_memberships WHERE id = %s", (membership_id,))


async def user_has_permission(user_id: int, permission: str) -> bool:
    # Check if user is super admin first
    user_record = await user_repo.get_user_by_id(user_id)
    if user_record and bool(user_record.get("is_super_admin")):
        return True
    
    # Check role-based permissions from memberships
    memberships = await list_memberships_for_user(user_id, status="active")
    for membership in memberships:
        permissions = membership.get("permissions") or []
        if permission in permissions:
            return True
        
        # Also check user-specific permissions for this company
        company_id = membership.get("company_id")
        if company_id:
            user_permissions = await user_permissions_repo.list_user_permissions(user_id, company_id)
            if permission in user_permissions:
                return True
    
    return False


async def list_users_with_permission(permission: str) -> List[dict[str, Any]]:
    rows = await db.fetch_all(
        """
        SELECT
            u.id AS user_id,
            u.email,
            u.first_name,
            u.last_name,
            u.mobile_phone,
            u.company_id,
            u.is_super_admin,
            r.permissions
        FROM company_memberships AS m
        INNER JOIN roles AS r ON r.id = m.role_id
        INNER JOIN users AS u ON u.id = m.user_id
        WHERE LOWER(m.status) = 'active'
        ORDER BY u.email
        """,
    )

    eligible_users: dict[int, dict[str, Any]] = {}
    for row in rows:
        permissions_raw = row.get("permissions")
        if isinstance(permissions_raw, str):
            try:
                permissions = json.loads(permissions_raw)
            except json.JSONDecodeError:
                permissions = []
        else:
            permissions = permissions_raw or []

        is_super_admin = bool(row.get("is_super_admin", 0))
        if permission not in permissions and not is_super_admin:
            continue

        try:
            user_id = int(row.get("user_id"))
        except (TypeError, ValueError):
            continue

        eligible_users[user_id] = {
            "id": user_id,
            "email": row.get("email"),
            "first_name": row.get("first_name"),
            "last_name": row.get("last_name"),
            "mobile_phone": row.get("mobile_phone"),
            "company_id": row.get("company_id"),
            "is_super_admin": is_super_admin,
        }

    def _sort_key(record: dict[str, Any]) -> tuple[str, int]:
        email = record.get("email") or ""
        return (email.lower(), record["id"])

    return sorted(eligible_users.values(), key=_sort_key)


async def list_impersonatable_memberships() -> list[dict[str, Any]]:
    rows = await db.fetch_all(
        """
        SELECT
            m.id AS membership_id,
            m.user_id,
            m.company_id,
            m.status,
            u.email,
            u.first_name,
            u.last_name,
            u.is_super_admin,
            c.name AS company_name,
            r.name AS role_name,
            r.permissions
        FROM company_memberships AS m
        INNER JOIN users AS u ON u.id = m.user_id
        INNER JOIN roles AS r ON r.id = m.role_id
        LEFT JOIN companies AS c ON c.id = m.company_id
        WHERE LOWER(m.status) = 'active'
        ORDER BY LOWER(u.email), u.id, c.name
        """,
    )
    return [dict(row) for row in rows]


def _normalise_membership(row: dict[str, Any]) -> dict[str, Any]:
    permissions_raw = row.get("permissions")
    permissions: list[str]
    if isinstance(permissions_raw, str):
        try:
            permissions = json.loads(permissions_raw)
        except json.JSONDecodeError:
            permissions = []
    else:
        permissions = permissions_raw or []
    return {
        **row,
        "permissions": permissions,
        "is_active": row.get("status") == "active",
    }
