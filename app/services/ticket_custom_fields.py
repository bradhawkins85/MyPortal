"""Custom ticket details: field definitions, visibility and value handling.

Admins define extra fields shown under *Additional details* on a ticket.

* **Visibility** decides who sees a field: ``public`` (anyone who can view the
  ticket, including the requester's portal view and ticket list), ``private``
  (helpdesk technicians and super admins) or ``admin_only`` (super admins).
* **Editable** fields can be changed by technicians/admins who can see them;
  read-only fields are shown but can only be set by automations.
"""
from __future__ import annotations

import json
import math
import re
from datetime import date
from typing import Any, Iterable, Mapping, Sequence

from app.core.logging import log_error
from app.repositories import ticket_custom_fields as repo
from app.services import custom_forms

#: Field types a ticket detail can use — the custom form types that hold a
#: single stored answer (uploads, signatures, matrices, passwords and hidden
#: fields do not suit a ticket detail).
FIELD_TYPES: dict[str, str] = {
    key: custom_forms.FIELD_TYPES[key]
    for key in (
        "text", "textarea", "email", "number", "time", "url", "date",
        "select", "radio", "multiselect", "checkbox", "rating",
    )
}
FIELD_TYPE_HELP: dict[str, str] = {
    "text": "A short answer",
    "textarea": "Several lines of notes",
    "email": "An email address",
    "number": "A whole or decimal number",
    "time": "A time of day",
    "url": "A web link",
    "date": "A calendar date",
    "select": "Pick one from a list",
    "radio": "Pick one, all choices shown",
    "multiselect": "Pick any from a list",
    "checkbox": "Yes or no",
    "rating": "A score out of stars",
}
OPTION_TYPES = custom_forms.OPTION_TYPES

VISIBILITY_PUBLIC = "public"
VISIBILITY_PRIVATE = "private"
VISIBILITY_ADMIN_ONLY = "admin_only"
VISIBILITIES: dict[str, str] = {
    VISIBILITY_PUBLIC: "Public",
    VISIBILITY_PRIVATE: "Private",
    VISIBILITY_ADMIN_ONLY: "Admin only",
}
VISIBILITY_HELP: dict[str, str] = {
    VISIBILITY_PUBLIC: "Requesters see it on their ticket and in their ticket list.",
    VISIBILITY_PRIVATE: "Only technicians and admins see it.",
    VISIBILITY_ADMIN_ONLY: "Only super admins see and change it.",
}

MAX_KEY_LENGTH = 100
MAX_LABEL_LENGTH = 255
MAX_HELP_LENGTH = 500
MAX_TEXT_LENGTH = 1000
MAX_TEXTAREA_LENGTH = 10000
DEFAULT_RATING_MAX = 5

_KEY_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_KEY_SANITISE_RE = re.compile(r"[^a-z0-9]+")
_TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d(:[0-5]\d)?$")
_TRUE_VALUES = {"1", "true", "yes", "y", "on", "checked"}
_FALSE_VALUES = {"0", "false", "no", "n", "off", "unchecked"}


# ---------------------------------------------------------------------------
# Visibility
# ---------------------------------------------------------------------------


def visible_scopes(*, is_super_admin: bool, is_staff: bool) -> frozenset[str]:
    """Visibility levels a viewer may see."""
    if is_super_admin:
        return frozenset(VISIBILITIES)
    if is_staff:
        return frozenset({VISIBILITY_PUBLIC, VISIBILITY_PRIVATE})
    return frozenset({VISIBILITY_PUBLIC})


def can_edit(definition: Mapping[str, Any], *, is_super_admin: bool, is_staff: bool) -> bool:
    """Whether a viewer may change a field directly (automations always can)."""
    if not definition.get("editable") or not definition.get("is_active", True):
        return False
    if not (is_super_admin or is_staff):
        return False
    return definition.get("visibility") in visible_scopes(is_super_admin=is_super_admin, is_staff=is_staff)


# ---------------------------------------------------------------------------
# Definitions
# ---------------------------------------------------------------------------


def key_from_label(label: str) -> str:
    key = _KEY_SANITISE_RE.sub("_", (label or "").strip().lower()).strip("_")
    if key and not key[0].isalpha():
        key = "field_" + key
    return (key or "field")[:MAX_KEY_LENGTH]


def _parse_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in _TRUE_VALUES


def _parse_options(raw: Any) -> list[str]:
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return []
        try:
            parsed = json.loads(text)
        except ValueError:
            parsed = [line for line in text.splitlines()]
        raw = parsed
    if not isinstance(raw, (list, tuple)):
        return []
    options: list[str] = []
    for item in raw:
        if isinstance(item, Mapping):
            item = item.get("value") or item.get("label")
        text = str(item or "").strip()
        if text and text not in options:
            options.append(text)
    return options


def normalise_definition(
    data: Mapping[str, Any],
    *,
    existing_keys: Iterable[str] = (),
) -> tuple[dict[str, Any], list[str]]:
    """Validate submitted definition data; return ``(values, errors)``."""
    errors: list[str] = []
    label = str(data.get("label") or "").strip()
    field_key = str(data.get("field_key") or "").strip().lower() or key_from_label(label)
    field_type = str(data.get("field_type") or "text").strip()
    visibility = str(data.get("visibility") or VISIBILITY_PRIVATE).strip()
    options = _parse_options(data.get("options"))
    config: dict[str, Any] = {}

    if not label:
        errors.append("Enter a label for the field.")
    elif len(label) > MAX_LABEL_LENGTH:
        errors.append(f"Labels can be at most {MAX_LABEL_LENGTH} characters.")
    if len(field_key) > MAX_KEY_LENGTH or not _KEY_RE.match(field_key):
        errors.append("The key must start with a letter and use only lowercase letters, numbers and underscores.")
    elif field_key in set(existing_keys):
        errors.append(f"Another field already uses the key “{field_key}”.")
    if field_type not in FIELD_TYPES:
        errors.append("Choose a supported field type.")
    if visibility not in VISIBILITIES:
        errors.append("Choose who can see the field.")
    if field_type in OPTION_TYPES:
        errors.extend(custom_forms.validate_field_definition(field_type, label or "x", options))
    else:
        options = []
    if field_type == "rating":
        try:
            rating_max = int(data.get("rating_max") or (data.get("config") or {}).get("max") or DEFAULT_RATING_MAX)
        except (TypeError, ValueError, AttributeError):
            rating_max = 0
        if not 1 <= rating_max <= custom_forms.MAX_RATING:
            errors.append(f"A rating must be out of 1 to {custom_forms.MAX_RATING} stars.")
        config["max"] = rating_max
    help_text = str(data.get("help_text") or "").strip()
    if len(help_text) > MAX_HELP_LENGTH:
        errors.append(f"Help text can be at most {MAX_HELP_LENGTH} characters.")
    try:
        sort_order = int(str(data.get("sort_order") or "0").strip() or 0)
    except ValueError:
        errors.append("Sort order must be a whole number.")
        sort_order = 0

    values = {
        "field_key": field_key,
        "label": label,
        "help_text": help_text or None,
        "field_type": field_type,
        "options": options,
        "config": config,
        "visibility": visibility,
        "editable": _parse_bool(data.get("editable")),
        "show_in_list": _parse_bool(data.get("show_in_list")),
        "is_active": _parse_bool(data.get("is_active", True)),
        "sort_order": sort_order,
    }
    return values, errors


async def save_definition(
    data: Mapping[str, Any], *, definition_id: int | None = None
) -> tuple[int | None, list[str]]:
    """Create or update a definition; return ``(id, errors)``."""
    existing = await repo.list_definitions()
    if definition_id is not None and not any(item["id"] == definition_id for item in existing):
        return None, ["That field no longer exists."]
    other_keys = [item["field_key"] for item in existing if item["id"] != definition_id]
    values, errors = normalise_definition(data, existing_keys=other_keys)
    if errors:
        return None, errors
    if definition_id is None:
        return await repo.create_definition(values), []
    await repo.update_definition(definition_id, values)
    return definition_id, []


# ---------------------------------------------------------------------------
# Values
# ---------------------------------------------------------------------------


def _is_blank(raw: Any) -> bool:
    if raw is None:
        return True
    if isinstance(raw, str):
        return not raw.strip()
    if isinstance(raw, (list, tuple, set)):
        return not any(str(item).strip() for item in raw)
    return False


def _as_list(raw: Any) -> list[str]:
    if isinstance(raw, (list, tuple, set)):
        return [str(item).strip() for item in raw if str(item).strip()]
    text = str(raw).strip()
    if text.startswith("["):
        try:
            parsed = json.loads(text)
        except ValueError:
            parsed = None
        if isinstance(parsed, list):
            return [str(item).strip() for item in parsed if str(item).strip()]
    return [part.strip() for part in text.split(",") if part.strip()]


def _looks_like_email(text: str) -> bool:
    """One ``@`` with a non-empty local part and a dotted domain; no regex backtracking."""
    if len(text) > 320 or any(char.isspace() for char in text) or text.count("@") != 1:
        return False
    local, _, domain = text.partition("@")
    return bool(local) and "." in domain.strip(".") and not domain.startswith(".") and not domain.endswith(".")


def coerce_value(definition: Mapping[str, Any], raw: Any) -> str | None:
    """Validate ``raw`` for a field and return the stored text (``None`` clears).

    Raises :class:`ValueError` with a user-facing message when the value does
    not suit the field.
    """
    field_type = definition.get("field_type") or "text"
    label = definition.get("label") or definition.get("field_key") or "This field"
    if field_type == "checkbox":
        if _is_blank(raw):
            return None
        if isinstance(raw, bool):
            return "1" if raw else "0"
        text = str(raw).strip().lower()
        if text in _TRUE_VALUES:
            return "1"
        if text in _FALSE_VALUES:
            return "0"
        raise ValueError(f"{label} must be yes or no.")
    if _is_blank(raw):
        return None
    options = [str(option) for option in definition.get("options") or []]
    if field_type == "multiselect":
        chosen = _as_list(raw)
        unknown = [item for item in chosen if item not in options]
        if unknown:
            raise ValueError(f"{label} has no option “{unknown[0]}”.")
        ordered = [option for option in options if option in chosen]
        return json.dumps(ordered) if ordered else None
    if isinstance(raw, (list, tuple, set)):
        raw = next((item for item in raw if str(item).strip()), "")
    text = str(raw).strip()
    if field_type in ("select", "radio"):
        if text not in options:
            raise ValueError(f"{label} has no option “{text}”.")
        return text
    if field_type == "number":
        try:
            number = float(text)
        except ValueError:
            raise ValueError(f"{label} must be a number.") from None
        if not math.isfinite(number):
            raise ValueError(f"{label} must be a number.")
        return str(int(number)) if number.is_integer() else repr(number)
    if field_type == "rating":
        try:
            rating = int(float(text))
        except ValueError:
            raise ValueError(f"{label} must be a whole number of stars.") from None
        maximum = int((definition.get("config") or {}).get("max") or DEFAULT_RATING_MAX)
        if not 0 <= rating <= maximum:
            raise ValueError(f"{label} must be between 0 and {maximum}.")
        return str(rating) if rating else None
    if field_type == "date":
        try:
            return date.fromisoformat(text[:10]).isoformat()
        except ValueError:
            raise ValueError(f"{label} must be a date (YYYY-MM-DD).") from None
    if field_type == "time":
        if not _TIME_RE.match(text):
            raise ValueError(f"{label} must be a time (HH:MM).")
        return text[:5]
    if field_type == "email":
        if not _looks_like_email(text):
            raise ValueError(f"{label} must be an email address.")
        return text
    if field_type == "url":
        if not re.match(r"^https?://\S+$", text, re.IGNORECASE) or len(text) > 2000:
            raise ValueError(f"{label} must be a web address starting with http:// or https://.")
        return text
    limit = MAX_TEXTAREA_LENGTH if field_type == "textarea" else MAX_TEXT_LENGTH
    if len(text) > limit:
        raise ValueError(f"{label} can be at most {limit} characters.")
    return text


def decode_value(definition: Mapping[str, Any], value_text: str | None) -> Any:
    """Return the structured value (list, bool, number or text) for a stored value."""
    if value_text is None:
        return None
    field_type = definition.get("field_type")
    if field_type == "multiselect":
        return _as_list(value_text)
    if field_type == "checkbox":
        return value_text == "1"
    if field_type in ("number", "rating"):
        try:
            number = float(value_text)
        except ValueError:
            return value_text
        return int(number) if number.is_integer() else number
    return value_text


def display_value(definition: Mapping[str, Any], value_text: str | None) -> str:
    """Human-readable text for a stored value ('' when unset)."""
    if value_text is None:
        return ""
    field_type = definition.get("field_type")
    if field_type == "checkbox":
        return "Yes" if value_text == "1" else "No"
    if field_type == "multiselect":
        return ", ".join(_as_list(value_text))
    if field_type == "rating":
        maximum = int((definition.get("config") or {}).get("max") or DEFAULT_RATING_MAX)
        return f"{value_text} / {maximum}"
    return value_text


# ---------------------------------------------------------------------------
# Ticket page helpers
# ---------------------------------------------------------------------------


async def build_ticket_fields(
    ticket_id: int, *, is_super_admin: bool, is_staff: bool
) -> list[dict[str, Any]]:
    """Active fields the viewer may see on a ticket, with values and edit rights."""
    try:
        definitions = await repo.list_definitions(include_inactive=False)
        values = await repo.get_ticket_values(ticket_id) if definitions else {}
    except Exception as exc:  # pragma: no cover - keep the ticket page rendering
        log_error("Failed to load custom ticket details", ticket_id=int(ticket_id), error=str(exc))
        return []
    scopes = visible_scopes(is_super_admin=is_super_admin, is_staff=is_staff)
    fields: list[dict[str, Any]] = []
    for definition in definitions:
        if definition["visibility"] not in scopes:
            continue
        stored = values.get(definition["id"]) or {}
        value_text = stored.get("value_text")
        fields.append(
            {
                **definition,
                "value_text": value_text,
                "value": decode_value(definition, value_text),
                "display": display_value(definition, value_text),
                "updated_source": stored.get("updated_source"),
                "can_edit": can_edit(definition, is_super_admin=is_super_admin, is_staff=is_staff),
                "type_label": FIELD_TYPES.get(definition["field_type"], definition["field_type"]),
                "visibility_label": VISIBILITIES.get(definition["visibility"], definition["visibility"]),
            }
        )
    return fields


async def save_ticket_values(
    ticket_id: int,
    submitted: Mapping[str, Any],
    *,
    is_super_admin: bool,
    is_staff: bool,
    user_id: int | None,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Apply ``{field_key: raw}`` changes a person submitted.

    Only fields the person may edit are changed; any other key is an error.
    Returns ``(changes, errors)``; nothing is saved when there are errors.
    """
    definitions = {item["field_key"]: item for item in await repo.list_definitions(include_inactive=False)}
    current = await repo.get_ticket_values(ticket_id)
    pending: list[tuple[dict[str, Any], str | None]] = []
    errors: list[str] = []
    for key, raw in submitted.items():
        definition = definitions.get(str(key))
        if not definition or definition["visibility"] not in visible_scopes(
            is_super_admin=is_super_admin, is_staff=is_staff
        ):
            errors.append(f"Unknown ticket detail “{key}”.")
            continue
        if not can_edit(definition, is_super_admin=is_super_admin, is_staff=is_staff):
            errors.append(f"{definition['label']} is read-only and can only be set by automations.")
            continue
        try:
            pending.append((definition, coerce_value(definition, raw)))
        except ValueError as exc:
            errors.append(str(exc))
    if errors:
        return [], errors
    changes: list[dict[str, Any]] = []
    for definition, value_text in pending:
        previous = (current.get(definition["id"]) or {}).get("value_text")
        if previous == value_text:
            continue
        await repo.set_value(ticket_id, definition["id"], value_text, updated_by=user_id, source="user")
        changes.append({"field_key": definition["field_key"], "previous": previous, "value": value_text})
    return changes, []


async def set_value_by_key(
    ticket_id: int, field_key: str, raw: Any, *, source: str = "automation"
) -> dict[str, Any]:
    """Set a field from an automation or API, ignoring the read-only flag."""
    definition = await repo.get_definition_by_key(str(field_key or "").strip())
    if not definition:
        raise ValueError(f"Unknown ticket detail “{field_key}”.")
    value_text = coerce_value(definition, raw)
    previous = (await repo.get_ticket_values(ticket_id)).get(definition["id"], {}).get("value_text")
    if previous != value_text:
        await repo.set_value(ticket_id, definition["id"], value_text, source=source)
    return {
        "field_key": definition["field_key"],
        "previous": previous,
        "value": value_text,
        "changed": previous != value_text,
    }


async def automation_context(ticket_id: int) -> dict[str, Any]:
    """``{field_key: value}`` for every field, for automation templates and filters."""
    definitions = await repo.list_definitions()
    if not definitions:
        return {}
    values = await repo.get_ticket_values(ticket_id)
    return {
        definition["field_key"]: decode_value(
            definition, (values.get(definition["id"]) or {}).get("value_text")
        )
        for definition in definitions
    }


# ---------------------------------------------------------------------------
# Ticket lists
# ---------------------------------------------------------------------------


async def list_columns(*, is_super_admin: bool, is_staff: bool) -> list[dict[str, Any]]:
    """Fields shown as ticket-list columns for this viewer."""
    try:
        definitions = await repo.list_definitions(include_inactive=False)
    except Exception as exc:  # pragma: no cover - keep the ticket list rendering
        log_error("Failed to load custom ticket detail columns", error=str(exc))
        return []
    scopes = visible_scopes(is_super_admin=is_super_admin, is_staff=is_staff)
    return [
        {
            "id": definition["id"],
            "field_key": definition["field_key"],
            "label": definition["label"],
            "field_type": definition["field_type"],
            "options": definition["options"],
            "config": definition["config"],
            "column": "cf-" + definition["field_key"].replace("_", "-"),
        }
        for definition in definitions
        if definition["show_in_list"] and definition["visibility"] in scopes
    ]


async def list_values(
    ticket_ids: Iterable[Any], columns: Sequence[Mapping[str, Any]]
) -> dict[int, dict[str, str]]:
    """``{ticket_id: {field_key: display text}}`` for list columns."""
    ids: list[int] = []
    for ticket_id in ticket_ids:
        try:
            ids.append(int(ticket_id))
        except (TypeError, ValueError):
            continue
    if not ids or not columns:
        return {}
    by_id = {int(column["id"]): column for column in columns}
    try:
        rows = await repo.get_values_for_tickets(ids, list(by_id))
    except Exception as exc:  # pragma: no cover - keep the ticket list rendering
        log_error("Failed to load custom ticket detail values", error=str(exc))
        return {}
    return {
        ticket_id: {
            by_id[field_id]["field_key"]: display_value(by_id[field_id], value_text)
            for field_id, value_text in fields.items()
            if field_id in by_id
        }
        for ticket_id, fields in rows.items()
    }
