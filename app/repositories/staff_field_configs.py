from __future__ import annotations

import json
from typing import Any

from app.core.database import db


_ALLOWED_FIELD_TYPES = {"text", "select", "checkbox", "date"}


def _as_bool(value: Any) -> bool:
    return bool(int(value)) if value is not None else False


def _decode_json(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, dict):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        decoded = json.loads(text)
    except json.JSONDecodeError:
        return None
    return decoded if isinstance(decoded, dict) else None


async def list_effective_staff_fields(company_id: int) -> list[dict[str, Any]]:
    rows = await db.fetch_all(
        """
        SELECT
            d.id AS definition_id,
            d.field_key,
            d.label,
            d.field_type,
            d.default_visible,
            d.default_required,
            d.default_sort_order,
            d.validation_metadata AS default_validation_metadata,
            c.visible AS override_visible,
            c.required AS override_required,
            c.sort_order AS override_sort_order,
            c.validation_metadata AS override_validation_metadata
        FROM staff_field_definitions AS d
        LEFT JOIN company_staff_field_configs AS c
            ON c.field_definition_id = d.id
           AND c.company_id = %s
        ORDER BY COALESCE(c.sort_order, d.default_sort_order), d.id
        """,
        (company_id,),
    )

    options_rows = await db.fetch_all(
        """
        SELECT
            o.field_definition_id,
            o.option_value,
            o.option_label,
            o.sort_order
        FROM company_staff_field_options AS o
        WHERE o.company_id = %s
        ORDER BY o.field_definition_id, o.sort_order, o.id
        """,
        (company_id,),
    )
    options_map: dict[int, list[dict[str, str]]] = {}
    for row in options_rows:
        field_definition_id = row.get("field_definition_id")
        if field_definition_id is None:
            continue
        option_entry = {
            "value": str(row.get("option_value") or "").strip(),
            "label": str(row.get("option_label") or "").strip(),
        }
        if not option_entry["value"]:
            continue
        options_map.setdefault(int(field_definition_id), []).append(option_entry)

    fields: list[dict[str, Any]] = []
    for row in rows:
        field_type = str(row.get("field_type") or "text").strip().lower()
        if field_type not in _ALLOWED_FIELD_TYPES:
            field_type = "text"
        definition_id = int(row.get("definition_id"))
        override_visible = row.get("override_visible")
        override_required = row.get("override_required")
        override_sort_order = row.get("override_sort_order")

        validation_metadata = _decode_json(row.get("default_validation_metadata")) or {}
        override_validation = _decode_json(row.get("override_validation_metadata")) or {}
        validation_metadata.update(override_validation)

        fields.append(
            {
                "definition_id": definition_id,
                "key": str(row.get("field_key") or "").strip(),
                "label": str(row.get("label") or "").strip(),
                "type": field_type,
                "visible": _as_bool(override_visible)
                if override_visible is not None
                else _as_bool(row.get("default_visible")),
                "required": _as_bool(override_required)
                if override_required is not None
                else _as_bool(row.get("default_required")),
                "sort_order": int(override_sort_order)
                if override_sort_order is not None
                else int(row.get("default_sort_order") or 0),
                "validation_metadata": validation_metadata,
                "options": options_map.get(definition_id, []),
            }
        )
    return fields


async def upsert_company_staff_field_configs(
    company_id: int,
    configs: list[dict[str, Any]],
) -> None:
    for config in configs:
        definition_id = config.get("definition_id")
        if definition_id is None:
            continue
        await db.execute(
            """
            INSERT INTO company_staff_field_configs (
                company_id,
                field_definition_id,
                visible,
                required,
                sort_order,
                validation_metadata
            ) VALUES (%s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                visible = VALUES(visible),
                required = VALUES(required),
                sort_order = VALUES(sort_order),
                validation_metadata = VALUES(validation_metadata)
            """,
            (
                company_id,
                int(definition_id),
                1 if config.get("visible") else 0,
                1 if config.get("required") else 0,
                int(config.get("sort_order") or 0),
                json.dumps(config.get("validation_metadata") or {}),
            ),
        )


async def replace_company_staff_field_options(
    company_id: int,
    options_by_definition: dict[int, list[dict[str, str]]],
) -> None:
    await db.execute(
        "DELETE FROM company_staff_field_options WHERE company_id = %s",
        (company_id,),
    )

    for definition_id, options in options_by_definition.items():
        for index, option in enumerate(options):
            value = str(option.get("value") or "").strip()
            label = str(option.get("label") or value).strip()
            if not value:
                continue
            await db.execute(
                """
                INSERT INTO company_staff_field_options (
                    company_id,
                    field_definition_id,
                    option_value,
                    option_label,
                    sort_order
                ) VALUES (%s, %s, %s, %s, %s)
                """,
                (company_id, int(definition_id), value, label, index),
            )
