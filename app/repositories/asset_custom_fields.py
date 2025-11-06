from __future__ import annotations

from typing import Any

from app.core.database import db


async def list_field_definitions() -> list[dict[str, Any]]:
    """Get all custom field definitions ordered by display_order."""
    rows = await db.fetch_all(
        """
        SELECT id, name, field_type, display_order, created_at, updated_at
        FROM asset_custom_field_definitions
        ORDER BY display_order ASC, id ASC
        """,
    )
    return list(rows or [])


async def get_field_definition(definition_id: int) -> dict[str, Any] | None:
    """Get a single field definition by ID."""
    return await db.fetch_one(
        "SELECT id, name, field_type, display_order, created_at, updated_at FROM asset_custom_field_definitions WHERE id = %s",
        (definition_id,),
    )


async def create_field_definition(
    name: str,
    field_type: str,
    display_order: int = 0,
) -> int | None:
    """Create a new custom field definition."""
    result = await db.execute(
        """
        INSERT INTO asset_custom_field_definitions (name, field_type, display_order)
        VALUES (%s, %s, %s)
        """,
        (name, field_type, display_order),
    )
    return result


async def update_field_definition(
    definition_id: int,
    name: str | None = None,
    field_type: str | None = None,
    display_order: int | None = None,
) -> None:
    """Update a custom field definition."""
    updates = []
    params = []
    
    if name is not None:
        updates.append("name = %s")
        params.append(name)
    if field_type is not None:
        updates.append("field_type = %s")
        params.append(field_type)
    if display_order is not None:
        updates.append("display_order = %s")
        params.append(display_order)
    
    if not updates:
        return
    
    params.append(definition_id)
    await db.execute(
        f"UPDATE asset_custom_field_definitions SET {', '.join(updates)} WHERE id = %s",
        tuple(params),
    )


async def delete_field_definition(definition_id: int) -> None:
    """Delete a custom field definition (cascades to values)."""
    await db.execute(
        "DELETE FROM asset_custom_field_definitions WHERE id = %s",
        (definition_id,),
    )


async def get_asset_field_values(asset_id: int) -> list[dict[str, Any]]:
    """Get all custom field values for an asset."""
    rows = await db.fetch_all(
        """
        SELECT 
            v.id,
            v.asset_id,
            v.field_definition_id,
            v.value_text,
            v.value_date,
            v.value_boolean,
            d.name as field_name,
            d.field_type,
            d.display_order
        FROM asset_custom_field_values v
        JOIN asset_custom_field_definitions d ON v.field_definition_id = d.id
        WHERE v.asset_id = %s
        ORDER BY d.display_order ASC, d.id ASC
        """,
        (asset_id,),
    )
    return list(rows or [])


async def set_asset_field_value(
    asset_id: int,
    field_definition_id: int,
    value_text: str | None = None,
    value_date: str | None = None,
    value_boolean: bool | None = None,
) -> None:
    """Set or update a custom field value for an asset."""
    # Check if value exists
    existing = await db.fetch_one(
        "SELECT id FROM asset_custom_field_values WHERE asset_id = %s AND field_definition_id = %s",
        (asset_id, field_definition_id),
    )
    
    if existing:
        # Update existing value
        await db.execute(
            """
            UPDATE asset_custom_field_values
            SET value_text = %s, value_date = %s, value_boolean = %s
            WHERE asset_id = %s AND field_definition_id = %s
            """,
            (value_text, value_date, value_boolean, asset_id, field_definition_id),
        )
    else:
        # Insert new value
        await db.execute(
            """
            INSERT INTO asset_custom_field_values 
            (asset_id, field_definition_id, value_text, value_date, value_boolean)
            VALUES (%s, %s, %s, %s, %s)
            """,
            (asset_id, field_definition_id, value_text, value_date, value_boolean),
        )


async def delete_asset_field_value(asset_id: int, field_definition_id: int) -> None:
    """Delete a custom field value for an asset."""
    await db.execute(
        "DELETE FROM asset_custom_field_values WHERE asset_id = %s AND field_definition_id = %s",
        (asset_id, field_definition_id),
    )
