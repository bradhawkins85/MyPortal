from __future__ import annotations

from datetime import date
from typing import Any

from app.core.database import db


def _int_csv(values: list[int]) -> str:
    """Return a comma-separated list of integers for FIND_IN_SET filters."""
    return ",".join(str(int(value)) for value in values)


async def list_field_definitions(company_id: int) -> list[dict[str, Any]]:
    """List effective field definitions for a company (global with company overrides)."""
    rows = await db.fetch_all(
        """
        SELECT
            COALESCE(ovr.id, base.id) AS id,
            base.id AS base_definition_id,
            COALESCE(ovr.name, base.name) AS name,
            COALESCE(ovr.display_name, base.display_name) AS display_name,
            COALESCE(ovr.field_type, base.field_type) AS field_type,
            COALESCE(ovr.field_group, base.field_group) AS field_group,
            COALESCE(ovr.display_order, base.display_order) AS display_order,
            COALESCE(ovr.is_active, base.is_active) AS is_active,
            COALESCE(ovr.condition_parent_name, base.condition_parent_name) AS condition_parent_name,
            COALESCE(ovr.condition_operator, base.condition_operator) AS condition_operator,
            COALESCE(ovr.condition_value, base.condition_value) AS condition_value,
            COALESCE(ovr.company_id, base.company_id) AS company_id
        FROM staff_custom_field_definitions AS base
        LEFT JOIN staff_custom_field_definitions AS ovr
          ON ovr.base_definition_id = base.id
         AND ovr.company_id = %s
        WHERE base.base_definition_id IS NULL
          AND (base.company_id IS NULL OR base.company_id = %s)
          AND COALESCE(ovr.is_active, base.is_active) = 1
        ORDER BY
            CASE WHEN COALESCE(ovr.field_group, base.field_group) IS NULL OR TRIM(COALESCE(ovr.field_group, base.field_group)) = '' THEN 0 ELSE 1 END,
            COALESCE(ovr.field_group, base.field_group),
            COALESCE(ovr.display_order, base.display_order),
            COALESCE(ovr.id, base.id)
        """,
        (company_id, company_id),
    )
    definitions = [dict(row) for row in (rows or [])]
    if not definitions:
        return []

    ids = [int(item["id"]) for item in definitions]
    id_csv = _int_csv(ids)
    option_rows = await db.fetch_all(
        """
        SELECT field_definition_id, option_value, option_label, sort_order
        FROM staff_custom_field_options
        WHERE FIND_IN_SET(field_definition_id, %s) > 0
        ORDER BY field_definition_id, sort_order, id
        """,
        (id_csv,),
    )
    options_map: dict[int, list[dict[str, Any]]] = {}
    for row in option_rows or []:
        fid = int(row["field_definition_id"])
        options_map.setdefault(fid, []).append(
            {
                "value": str(row.get("option_value") or "").strip(),
                "label": str(row.get("option_label") or row.get("option_value") or "").strip(),
            }
        )

    for definition in definitions:
        definition["options"] = options_map.get(int(definition["id"]), [])
    return definitions


async def list_company_owned_definitions(company_id: int) -> list[dict[str, Any]]:
    rows = await db.fetch_all(
        """
        SELECT
            id,
            base_definition_id,
            company_id,
            name,
            display_name,
            field_type,
            field_group,
            display_order,
            is_active,
            condition_parent_name,
            condition_operator,
            condition_value,
            created_at,
            updated_at
        FROM staff_custom_field_definitions
        WHERE company_id = %s
        ORDER BY
            CASE WHEN field_group IS NULL OR TRIM(field_group) = '' THEN 0 ELSE 1 END,
            field_group,
            display_order,
            id
        """,
        (company_id,),
    )
    definitions = [dict(row) for row in (rows or [])]
    if not definitions:
        return []
    ids = [int(item["id"]) for item in definitions]
    id_csv = _int_csv(ids)
    option_rows = await db.fetch_all(
        """
        SELECT field_definition_id, option_value, option_label, sort_order
        FROM staff_custom_field_options
        WHERE FIND_IN_SET(field_definition_id, %s) > 0
        ORDER BY field_definition_id, sort_order, id
        """,
        (id_csv,),
    )
    options_map: dict[int, list[dict[str, Any]]] = {}
    for row in option_rows or []:
        fid = int(row["field_definition_id"])
        options_map.setdefault(fid, []).append(
            {
                "value": str(row.get("option_value") or "").strip(),
                "label": str(row.get("option_label") or row.get("option_value") or "").strip(),
            }
        )
    for definition in definitions:
        definition["options"] = options_map.get(int(definition["id"]), [])
    return definitions


async def create_company_definition(
    *,
    company_id: int,
    name: str,
    display_name: str | None,
    field_type: str,
    field_group: str | None = None,
    display_order: int = 0,
    condition_parent_name: str | None = None,
    condition_operator: str | None = None,
    condition_value: str | None = None,
    options: list[dict[str, str]] | None = None,
) -> int:
    definition_id = await db.execute_returning_lastrowid(
        """
        INSERT INTO staff_custom_field_definitions (
            company_id,
            base_definition_id,
            name,
            display_name,
            field_type,
            field_group,
            display_order,
            is_active,
            condition_parent_name,
            condition_operator,
            condition_value
        ) VALUES (%s, NULL, %s, %s, %s, %s, %s, 1, %s, %s, %s)
        """,
        (
            company_id,
            name,
            display_name or None,
            field_type,
            field_group or None,
            display_order,
            condition_parent_name or None,
            condition_operator or None,
            condition_value or None,
        ),
    )
    if not definition_id:
        raise RuntimeError("Failed to create staff custom field definition")
    await replace_field_options(int(definition_id), options or [])
    return int(definition_id)


async def update_company_definition(
    definition_id: int,
    *,
    company_id: int,
    display_name: str | None,
    field_type: str,
    field_group: str | None,
    display_order: int,
    is_active: bool,
    condition_parent_name: str | None,
    condition_operator: str | None,
    condition_value: str | None,
    options: list[dict[str, str]] | None = None,
) -> None:
    await db.execute(
        """
        UPDATE staff_custom_field_definitions
        SET display_name = %s,
            field_type = %s,
            field_group = %s,
            display_order = %s,
            is_active = %s,
            condition_parent_name = %s,
            condition_operator = %s,
            condition_value = %s
        WHERE id = %s AND company_id = %s
        """,
        (
            display_name or None,
            field_type,
            field_group or None,
            display_order,
            1 if is_active else 0,
            condition_parent_name or None,
            condition_operator or None,
            condition_value or None,
            definition_id,
            company_id,
        ),
    )
    await replace_field_options(definition_id, options or [])


async def delete_company_definition(definition_id: int, company_id: int) -> None:
    await db.execute(
        "DELETE FROM staff_custom_field_definitions WHERE id = %s AND company_id = %s",
        (definition_id, company_id),
    )


async def replace_field_options(definition_id: int, options: list[dict[str, str]]) -> None:
    await db.execute(
        "DELETE FROM staff_custom_field_options WHERE field_definition_id = %s",
        (definition_id,),
    )
    for idx, option in enumerate(options):
        value = str(option.get("value") or "").strip()
        label = str(option.get("label") or value).strip()
        if not value:
            continue
        await db.execute(
            """
            INSERT INTO staff_custom_field_options (
                field_definition_id, option_value, option_label, sort_order
            ) VALUES (%s, %s, %s, %s)
            """,
            (definition_id, value, label, idx),
        )


async def get_all_staff_field_values(
    company_id: int,
    staff_ids: list[int],
) -> dict[int, dict[str, Any]]:
    if not staff_ids:
        return {}
    definitions = await list_field_definitions(company_id)
    if not definitions:
        return {}

    definition_map = {int(item["id"]): item for item in definitions}
    staff_ids_csv = _int_csv(staff_ids)
    rows = await db.fetch_all(
        """
        SELECT staff_id, field_definition_id, value_text, value_date, value_boolean
        FROM staff_custom_field_values
        WHERE FIND_IN_SET(staff_id, %s) > 0
        """,
        (staff_ids_csv,),
    )
    values: dict[int, dict[str, Any]] = {int(staff_id): {} for staff_id in staff_ids}
    for row in rows or []:
        sid = int(row["staff_id"])
        fid = int(row["field_definition_id"])
        definition = definition_map.get(fid)
        if not definition:
            continue
        key = str(definition.get("name") or "")
        if not key:
            continue
        field_type = str(definition.get("field_type") or "text")
        if field_type == "checkbox":
            value = bool(row.get("value_boolean"))
        elif field_type == "date":
            raw_date = row.get("value_date")
            if isinstance(raw_date, date):
                value = raw_date.isoformat()
            else:
                value = str(raw_date) if raw_date is not None else None
        else:
            value = row.get("value_text")
        values.setdefault(sid, {})[key] = value
    return values


async def set_staff_field_values_by_name(
    *,
    company_id: int,
    staff_id: int,
    values: dict[str, Any],
) -> None:
    definitions = await list_field_definitions(company_id)
    if not definitions:
        return
    definition_by_name = {
        str(item.get("name") or ""): item
        for item in definitions
        if item.get("name")
    }
    for name, raw_value in (values or {}).items():
        definition = definition_by_name.get(str(name))
        if not definition:
            continue
        field_type = str(definition.get("field_type") or "text")
        value_text: str | None = None
        value_date: str | None = None
        value_boolean: bool | None = None
        if raw_value in (None, ""):
            pass
        elif field_type == "checkbox":
            value_boolean = bool(raw_value)
        elif field_type == "date":
            value_date = str(raw_value)
        else:
            value_text = str(raw_value)

        fid = int(definition["id"])
        existing = await db.fetch_one(
            "SELECT id FROM staff_custom_field_values WHERE staff_id = %s AND field_definition_id = %s",
            (staff_id, fid),
        )
        if existing:
            await db.execute(
                """
                UPDATE staff_custom_field_values
                SET value_text = %s, value_date = %s, value_boolean = %s
                WHERE staff_id = %s AND field_definition_id = %s
                """,
                (value_text, value_date, value_boolean, staff_id, fid),
            )
        else:
            await db.execute(
                """
                INSERT INTO staff_custom_field_values (
                    staff_id, field_definition_id, value_text, value_date, value_boolean
                ) VALUES (%s, %s, %s, %s, %s)
                """,
                (staff_id, fid, value_text, value_date, value_boolean),
            )
