from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Optional

from app.core.database import db


async def list_roles() -> list[dict[str, Any]]:
    rows = await db.fetch_all("SELECT * FROM roles ORDER BY name")
    return [_normalise(row) for row in rows]


async def get_role_by_id(role_id: int) -> Optional[dict[str, Any]]:
    row = await db.fetch_one("SELECT * FROM roles WHERE id = %s", (role_id,))
    if not row:
        return None
    return _normalise(row)


async def get_role_by_name(name: str) -> Optional[dict[str, Any]]:
    row = await db.fetch_one("SELECT * FROM roles WHERE name = %s", (name,))
    if not row:
        return None
    return _normalise(row)


async def create_role(*, name: str, description: str | None = None, permissions: list[str] | None = None, is_system: bool = False) -> dict[str, Any]:
    now = datetime.utcnow()
    await db.execute(
        """
        INSERT INTO roles (name, description, permissions, is_system, created_at, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s)
        """,
        (
            name,
            description,
            json.dumps(permissions or []),
            1 if is_system else 0,
            now,
            now,
        ),
    )
    created = await get_role_by_name(name)
    if not created:
        raise RuntimeError("Failed to create role")
    return created


async def update_role(role_id: int, **updates: Any) -> dict[str, Any]:
    if not updates:
        role = await get_role_by_id(role_id)
        if not role:
            raise ValueError("Role not found")
        return role
    columns = []
    params: list[Any] = []
    for column, value in updates.items():
        if column == "permissions" and value is not None:
            value = json.dumps(value)
        columns.append(f"{column} = %s")
        params.append(value)
    columns.append("updated_at = %s")
    params.append(datetime.utcnow())
    params.append(role_id)
    sql = f"UPDATE roles SET {', '.join(columns)} WHERE id = %s"
    await db.execute(sql, tuple(params))
    updated = await get_role_by_id(role_id)
    if not updated:
        raise ValueError("Role not found after update")
    return updated


async def delete_role(role_id: int) -> None:
    await db.execute("DELETE FROM roles WHERE id = %s", (role_id,))


def _normalise(row: dict[str, Any]) -> dict[str, Any]:
    permissions_raw = row.get("permissions")
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
        "is_system": bool(row.get("is_system", 0)),
    }
