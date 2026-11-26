"""Unticked defaults for asset checkbox custom fields.

Kept apart from ``assets`` and ``asset_custom_fields`` so both can use it
without importing each other.
"""

from __future__ import annotations

from app.core.database import db


async def ensure_unchecked_checkbox_values(
    *, asset_id: int | None = None, definition_id: int | None = None,
) -> None:
    """Record an explicit unchecked value where a checkbox has none yet.

    A checkbox is always ticked or unticked, so assets get a stored ``0`` for
    every checkbox field instead of no row. Scope to one asset, one
    definition, or neither to repair every asset; existing values are kept.
    """
    await db.execute(
        """
        INSERT INTO asset_custom_field_values (asset_id, field_definition_id, value_boolean)
        SELECT a.id, d.id, 0
        FROM assets a
        JOIN asset_custom_field_definitions d ON d.field_type = 'checkbox'
        WHERE (%s IS NULL OR a.id = %s)
          AND (%s IS NULL OR d.id = %s)
          AND NOT EXISTS (
              SELECT 1 FROM asset_custom_field_values v
              WHERE v.asset_id = a.id AND v.field_definition_id = d.id
          )
        """,
        (asset_id, asset_id, definition_id, definition_id),
    )
    await db.execute(
        """
        UPDATE asset_custom_field_values
        SET value_boolean = 0
        WHERE value_boolean IS NULL
          AND (%s IS NULL OR asset_id = %s)
          AND field_definition_id IN (
              SELECT d.id FROM asset_custom_field_definitions d
              WHERE d.field_type = 'checkbox' AND (%s IS NULL OR d.id = %s)
          )
        """,
        (asset_id, asset_id, definition_id, definition_id),
    )
