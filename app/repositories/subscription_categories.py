"""Repository for managing subscription categories."""
from __future__ import annotations

from typing import Any

from app.core.database import db


async def list_categories() -> list[dict[str, Any]]:
    """List all subscription categories."""
    rows = await db.fetch_all(
        """
        SELECT id, name, description, created_at, updated_at
        FROM subscription_categories
        ORDER BY name
        """
    )
    return [
        {
            "id": int(row["id"]),
            "name": row["name"],
            "description": row.get("description"),
            "created_at": row.get("created_at"),
            "updated_at": row.get("updated_at"),
        }
        for row in rows
    ]


async def get_category(category_id: int) -> dict[str, Any] | None:
    """Get a subscription category by ID."""
    row = await db.fetch_one(
        """
        SELECT id, name, description, created_at, updated_at
        FROM subscription_categories
        WHERE id = %s
        """,
        (category_id,),
    )
    if not row:
        return None
    return {
        "id": int(row["id"]),
        "name": row["name"],
        "description": row.get("description"),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


async def get_category_by_name(name: str) -> dict[str, Any] | None:
    """Get a subscription category by name."""
    row = await db.fetch_one(
        """
        SELECT id, name, description, created_at, updated_at
        FROM subscription_categories
        WHERE name = %s
        """,
        (name,),
    )
    if not row:
        return None
    return {
        "id": int(row["id"]),
        "name": row["name"],
        "description": row.get("description"),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


async def create_category(name: str, description: str | None = None) -> dict[str, Any]:
    """Create a new subscription category."""
    await db.execute(
        """
        INSERT INTO subscription_categories (name, description)
        VALUES (%s, %s)
        """,
        (name, description),
    )
    created = await get_category_by_name(name)
    if not created:
        raise RuntimeError(f"Failed to create subscription category: {name}")
    return created


async def update_category(
    category_id: int,
    *,
    name: str | None = None,
    description: str | None = None,
) -> None:
    """Update a subscription category."""
    updates: list[str] = []
    params: list[Any] = []
    
    if name is not None:
        updates.append("name = %s")
        params.append(name)
    
    if description is not None:
        updates.append("description = %s")
        params.append(description)
    
    if not updates:
        return
    
    params.append(category_id)
    await db.execute(
        f"UPDATE subscription_categories SET {', '.join(updates)} WHERE id = %s",
        tuple(params),
    )


async def delete_category(category_id: int) -> None:
    """Delete a subscription category."""
    await db.execute(
        "DELETE FROM subscription_categories WHERE id = %s",
        (category_id,),
    )
