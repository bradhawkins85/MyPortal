from __future__ import annotations

import re
from datetime import datetime, time, timezone
from typing import Any

from app.repositories import staff_field_configs as staff_field_config_repo


_CORE_REQUIRED_KEYS = {"first_name", "last_name"}
_FIELD_TYPE_MAP = {
    "text": "text",
    "date": "date",
    "checkbox": "checkbox",
    "select": "select",
}


def _parse_bool(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    return text in {"1", "true", "t", "yes", "y", "on"}


def _to_datetime(value: str) -> datetime | None:
    text = (value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        try:
            parsed = datetime.strptime(text, "%Y-%m-%d")
            parsed = datetime.combine(parsed.date(), time.min)
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    else:
        parsed = parsed.astimezone(timezone.utc)
    return parsed


def _parse_options_text(text: str) -> list[dict[str, str]]:
    options: list[dict[str, str]] = []
    for part in (text or "").split(","):
        item = part.strip()
        if not item:
            continue
        if ":" in item:
            value_part, label_part = item.split(":", 1)
            value = value_part.strip()
            label = label_part.strip() or value
        else:
            value = item
            label = item
        if not value:
            continue
        options.append({"value": value, "label": label})
    return options


async def load_effective_company_staff_fields(company_id: int) -> list[dict[str, Any]]:
    rows = await staff_field_config_repo.list_effective_staff_fields(company_id)
    fields: list[dict[str, Any]] = []
    for row in rows:
        key = str(row.get("key") or "").strip()
        if not key:
            continue
        field_type = _FIELD_TYPE_MAP.get(str(row.get("type") or "text").strip().lower(), "text")
        visible = bool(row.get("visible"))
        required = bool(row.get("required"))
        if key in _CORE_REQUIRED_KEYS:
            visible = True
            required = True
        field = {
            "definition_id": int(row.get("definition_id")),
            "key": key,
            "label": str(row.get("label") or key.replace("_", " ").title()),
            "type": field_type,
            "base_type": str(row.get("base_type") or field_type),
            "visible": visible,
            "required": required,
            "sort_order": int(row.get("sort_order") or 0),
            "validation_metadata": row.get("validation_metadata") or {},
            "options": row.get("options") or [],
            "input_name": key,
            "input_id": f"staff-field-{key.replace('_', '-')}",
        }
        fields.append(field)
    return fields


def validate_staff_form_values(
    form_data: dict[str, Any],
    field_config: list[dict[str, Any]],
) -> tuple[dict[str, Any], list[str]]:
    values: dict[str, Any] = {}
    errors: list[str] = []
    field_map = {field["key"]: field for field in field_config}

    for key, field in field_map.items():
        field_type = field.get("type")
        required = bool(field.get("required"))
        raw = form_data.get(key)

        if field_type == "checkbox":
            parsed = _parse_bool(raw)
        else:
            parsed = str(raw or "").strip()

        if required:
            if field_type == "checkbox":
                if not parsed:
                    errors.append(f"{field['label']} must be selected.")
            elif not parsed:
                errors.append(f"{field['label']} is required.")

        if field_type == "date" and parsed:
            parsed_date = _to_datetime(parsed)
            if parsed_date is None:
                errors.append(f"{field['label']} must be a valid date.")
                values[key] = None
                continue
            values[key] = parsed_date
            continue

        if field_type == "select" and parsed:
            allowed_options = {str(option.get('value') or '') for option in field.get("options") or []}
            if allowed_options and parsed not in allowed_options:
                errors.append(f"{field['label']} has an invalid option.")
                continue

        validation_metadata = field.get("validation_metadata") or {}
        max_length = validation_metadata.get("max_length")
        if isinstance(max_length, int) and isinstance(parsed, str) and len(parsed) > max_length:
            errors.append(f"{field['label']} exceeds maximum length of {max_length}.")

        if isinstance(parsed, str) and validation_metadata.get("format") == "email" and parsed:
            if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", parsed):
                errors.append(f"{field['label']} must be a valid email address.")

        values[key] = parsed

    return values, errors


async def save_company_staff_field_admin_config(
    company_id: int,
    form_data: dict[str, Any],
) -> None:
    fields = await load_effective_company_staff_fields(company_id)
    configs: list[dict[str, Any]] = []
    options_by_definition: dict[int, list[dict[str, str]]] = {}

    for field in fields:
        key = field["key"]
        definition_id = int(field["definition_id"])
        visible = _parse_bool(form_data.get(f"field_{key}_visible"))
        required = _parse_bool(form_data.get(f"field_{key}_required"))
        if key in _CORE_REQUIRED_KEYS:
            visible = True
            required = True

        sort_raw = form_data.get(f"field_{key}_sort_order")
        try:
            sort_order = int(str(sort_raw or field.get("sort_order") or 0).strip())
        except ValueError:
            sort_order = int(field.get("sort_order") or 0)

        field_type_raw = str(form_data.get(f"field_{key}_type") or "").strip().lower()
        base_type = field.get("base_type") or field.get("type") or "text"
        field_type_override = field_type_raw if field_type_raw and field_type_raw in _FIELD_TYPE_MAP else None
        if field_type_override == base_type:
            field_type_override = None

        configs.append(
            {
                "definition_id": definition_id,
                "visible": visible,
                "required": required,
                "sort_order": sort_order,
                "validation_metadata": field.get("validation_metadata") or {},
                "field_type": field_type_override,
            }
        )

        effective_type = field_type_override or base_type
        if effective_type == "select":
            options_text = str(form_data.get(f"field_{key}_options") or "")
            options_by_definition[definition_id] = _parse_options_text(options_text)

    await staff_field_config_repo.upsert_company_staff_field_configs(company_id, configs)
    await staff_field_config_repo.replace_company_staff_field_options(company_id, options_by_definition)
