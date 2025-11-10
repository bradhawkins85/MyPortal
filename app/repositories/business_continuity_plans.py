from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from app.core.database import db
from app.schemas.business_continuity_plans import PermissionLevel


async def create_plan(
    title: str,
    plan_type: str,
    content: str,
    version: str,
    status: str,
    created_by: int,
) -> dict[str, Any]:
    """Create a new business continuity plan."""
    query = """
        INSERT INTO business_continuity_plans
        (title, plan_type, content, version, status, created_by)
        VALUES (%s, %s, %s, %s, %s, %s)
    """
    plan_id = await db.execute(query, (title, plan_type, content, version, status, created_by))
    return await get_plan_by_id(plan_id)


async def get_plan_by_id(plan_id: int) -> dict[str, Any] | None:
    """Get a plan by ID."""
    query = """
        SELECT id, title, plan_type, content, version, status,
               created_by, created_at, updated_at, last_reviewed_at, last_reviewed_by
        FROM business_continuity_plans
        WHERE id = %s
    """
    return await db.fetch_one(query, (plan_id,))


async def list_plans(
    plan_type: str | None = None,
    status: str | None = None,
    user_id: int | None = None,
) -> list[dict[str, Any]]:
    """List all plans with optional filtering."""
    conditions = []
    params = []

    if plan_type:
        conditions.append("plan_type = %s")
        params.append(plan_type)
    
    if status:
        conditions.append("status = %s")
        params.append(status)

    where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    
    query = f"""
        SELECT id, title, plan_type, content, version, status,
               created_by, created_at, updated_at, last_reviewed_at, last_reviewed_by
        FROM business_continuity_plans
        {where_clause}
        ORDER BY updated_at DESC
    """
    plans = await db.fetch_all(query, tuple(params))
    
    # If user_id is provided, add permission information
    if user_id:
        for plan in plans:
            permission = await get_user_permission_for_plan(plan["id"], user_id)
            plan["user_permission"] = permission
    
    return plans


async def update_plan(
    plan_id: int,
    title: str | None = None,
    plan_type: str | None = None,
    content: str | None = None,
    version: str | None = None,
    status: str | None = None,
    last_reviewed_at: datetime | None = None,
    last_reviewed_by: int | None = None,
) -> dict[str, Any] | None:
    """Update a plan."""
    updates = []
    params = []

    if title is not None:
        updates.append("title = %s")
        params.append(title)
    if plan_type is not None:
        updates.append("plan_type = %s")
        params.append(plan_type)
    if content is not None:
        updates.append("content = %s")
        params.append(content)
    if version is not None:
        updates.append("version = %s")
        params.append(version)
    if status is not None:
        updates.append("status = %s")
        params.append(status)
    if last_reviewed_at is not None:
        updates.append("last_reviewed_at = %s")
        params.append(last_reviewed_at)
    if last_reviewed_by is not None:
        updates.append("last_reviewed_by = %s")
        params.append(last_reviewed_by)

    if not updates:
        return await get_plan_by_id(plan_id)

    params.append(plan_id)
    query = f"""
        UPDATE business_continuity_plans
        SET {', '.join(updates)}
        WHERE id = %s
    """
    await db.execute(query, tuple(params))
    return await get_plan_by_id(plan_id)


async def delete_plan(plan_id: int) -> bool:
    """Delete a plan."""
    query = "DELETE FROM business_continuity_plans WHERE id = %s"
    await db.execute(query, (plan_id,))
    return True


async def add_plan_permission(
    plan_id: int,
    user_id: int | None = None,
    company_id: int | None = None,
    permission_level: str = "read",
) -> dict[str, Any]:
    """Add a permission for a plan."""
    query = """
        INSERT INTO business_continuity_plan_permissions
        (plan_id, user_id, company_id, permission_level)
        VALUES (%s, %s, %s, %s)
    """
    perm_id = await db.execute(query, (plan_id, user_id, company_id, permission_level))
    return await get_plan_permission_by_id(perm_id)


async def get_plan_permission_by_id(perm_id: int) -> dict[str, Any] | None:
    """Get a permission by ID."""
    query = """
        SELECT id, plan_id, user_id, company_id, permission_level, created_at
        FROM business_continuity_plan_permissions
        WHERE id = %s
    """
    return await db.fetch_one(query, (perm_id,))


async def list_plan_permissions(plan_id: int) -> list[dict[str, Any]]:
    """List all permissions for a plan."""
    query = """
        SELECT id, plan_id, user_id, company_id, permission_level, created_at
        FROM business_continuity_plan_permissions
        WHERE plan_id = %s
    """
    return await db.fetch_all(query, (plan_id,))


async def update_plan_permission(
    perm_id: int,
    permission_level: str,
) -> dict[str, Any] | None:
    """Update a plan permission."""
    query = """
        UPDATE business_continuity_plan_permissions
        SET permission_level = %s
        WHERE id = %s
    """
    await db.execute(query, (permission_level, perm_id))
    return await get_plan_permission_by_id(perm_id)


async def delete_plan_permission(perm_id: int) -> bool:
    """Delete a plan permission."""
    query = "DELETE FROM business_continuity_plan_permissions WHERE id = %s"
    await db.execute(query, (perm_id,))
    return True


async def get_user_permission_for_plan(plan_id: int, user_id: int) -> str | None:
    """Get the permission level a user has for a specific plan."""
    # Check direct user permission
    query = """
        SELECT permission_level
        FROM business_continuity_plan_permissions
        WHERE plan_id = %s AND user_id = %s
    """
    result = await db.fetch_one(query, (plan_id, user_id))
    if result:
        return result["permission_level"]
    
    # Check company permission
    query = """
        SELECT bcp.permission_level
        FROM business_continuity_plan_permissions bcp
        JOIN users u ON u.company_id = bcp.company_id
        WHERE bcp.plan_id = %s AND u.id = %s AND bcp.company_id IS NOT NULL
    """
    result = await db.fetch_one(query, (plan_id, user_id))
    if result:
        return result["permission_level"]
    
    return None


async def user_can_access_plan(plan_id: int, user_id: int, is_super_admin: bool = False) -> bool:
    """Check if a user can access a plan."""
    if is_super_admin:
        return True
    
    permission = await get_user_permission_for_plan(plan_id, user_id)
    return permission is not None


async def user_can_edit_plan(plan_id: int, user_id: int, is_super_admin: bool = False) -> bool:
    """Check if a user can edit a plan."""
    if is_super_admin:
        return True
    
    permission = await get_user_permission_for_plan(plan_id, user_id)
    return permission == "edit"
