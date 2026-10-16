"""Service layer for the Custom Forms feature.

Responsible for field-type logic, submission validation/serialisation and the
submission → ticket → private-attachment orchestration.
"""
from __future__ import annotations

import html
import json
import re
from datetime import datetime
from typing import Any, Sequence

from app.core.logging import log_error, log_info
from app.repositories import forms as forms_repo

#: Standard input types an admin can add to a form, in display order.
FIELD_TYPES: dict[str, str] = {
    "text": "Single line text",
    "textarea": "Multi-line text",
    "email": "Email address",
    "number": "Number",
    "time": "Time",
    "url": "URL",
    "select": "Dropdown (single choice)",
    "radio": "Radio buttons (single choice)",
    "multiselect": "Multiple choice",
    "checkbox": "Check box (yes/no)",
    "date": "Date",
    "password": "Password",
    "rating": "Rating (stars)",
    "signature": "Signature",
    "audio": "Audio upload",
    "file": "File upload",
    "matrix": "Matrix grid",
    "hidden": "Hidden field",
}

#: Field types that expose an ``options`` list to the submitter.
OPTION_TYPES: frozenset[str] = frozenset({"select", "radio", "multiselect"})

#: Field types that accept uploaded files (file upload + audio clips).
FILE_TYPES: frozenset[str] = frozenset({"file", "audio"})

#: Field types that accept multiple uploaded files (multi-upload per question).
MULTI_FILE_TYPES: frozenset[str] = frozenset({"audio"})

#: Field types whose behaviour is configured via the ``config`` JSON column.
CONFIG_TYPES: frozenset[str] = frozenset({"matrix", "rating", "audio"})

#: Field types that store a base64/data-URL image.
SIGNATURE_TYPES: frozenset[str] = frozenset({"signature"})

MAX_OPTIONS = 100
MAX_OPTION_LENGTH = 255
MAX_AUDIO_CLIPS = 5
MAX_RATING = 10
MAX_MATRIX_ROWS = 50
MAX_MATRIX_COLS = 25

#: How people answer a matrix: one column per row, or one cell in the table.
MATRIX_MODES: dict[str, str] = {
    "row": "One answer per row",
    "table": "One answer per table",
}

#: Field types whose answers can decide whether another field is shown.
CONDITION_SOURCE_TYPES: frozenset[str] = frozenset({
    "text", "textarea", "email", "number", "time", "url", "date",
    "select", "radio", "multiselect", "checkbox", "rating",
})

#: Conditional logic operators, in the order the builder lists them.
CONDITION_OPERATORS: dict[str, str] = {
    "equals": "is",
    "not_equals": "is not",
    "contains": "contains",
    "not_contains": "does not contain",
    "greater_than": "is greater than",
    "less_than": "is less than",
    "empty": "is empty",
    "not_empty": "is not empty",
}

#: Operators that test for an answer rather than compare it with a value.
VALUELESS_OPERATORS: frozenset[str] = frozenset({"empty", "not_empty"})

#: Answers to these types are compared as numbers.
NUMERIC_CONDITION_TYPES: frozenset[str] = frozenset({"number", "rating"})

#: A checkbox answer as conditions see it.
CHECKBOX_CONDITION_VALUES: tuple[str, str] = ("Yes", "No")

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d(:[0-5]\d)?$")
_SLUG_RE = re.compile(r"[^a-z0-9]+")


def slugify(name: str) -> str:
    """Derive a URL-safe slug from a form name."""
    slug = _SLUG_RE.sub("-", (name or "").strip().lower()).strip("-")
    return slug or "form"


def unique_slug(name: str, existing: Sequence[str]) -> str:
    """Return a slug for ``name`` that is not already in ``existing``."""
    base = slugify(name)
    if base not in existing:
        return base
    candidate = f"{base}-{2}"
    suffix = 2
    while candidate in existing:
        suffix += 1
        candidate = f"{base}-{suffix}"
    return candidate


def matrix_mode(config: dict[str, Any] | None) -> str:
    """Return the matrix answer mode, treating legacy fields as one per row."""
    return "table" if (config or {}).get("mode") == "table" else "row"


def matrix_cell_answer(config: dict[str, Any] | None, cell: Any) -> dict[str, str] | None:
    """Map a ``"row_index:column_index"`` table cell to ``{row: column}``.

    Returns an empty dict when nothing was chosen and ``None`` when the cell
    does not exist in the matrix.
    """
    text = "" if cell is None else str(cell).strip()
    if not text:
        return {}
    rows = (config or {}).get("rows") or []
    columns = (config or {}).get("columns") or []
    row_index, _, column_index = text.partition(":")
    try:
        row, column = int(row_index), int(column_index)
    except ValueError:
        return None
    if not (0 <= row < len(rows) and 0 <= column < len(columns)):
        return None
    return {str(rows[row]): str(columns[column])}


def validate_field_definition(
    field_type: str,
    label: str,
    options: Sequence[str] | None,
    config: dict[str, Any] | None = None,
) -> list[str]:
    """Validate a field definition; return a list of error messages."""
    errors: list[str] = []
    if field_type not in FIELD_TYPES:
        errors.append(f"Unknown field type: {field_type}")
        return errors
    if not (label or "").strip():
        errors.append("Field label is required.")
    if field_type in OPTION_TYPES:
        opts = [opt.strip() for opt in (options or []) if str(opt).strip()]
        if not opts:
            errors.append("This field type requires at least one option.")
        if len(opts) > MAX_OPTIONS:
            errors.append(f"A field can have at most {MAX_OPTIONS} options.")
        for opt in opts:
            if len(opt) > MAX_OPTION_LENGTH:
                errors.append("Options must be at most 255 characters.")
    cfg = config or {}
    if field_type == "matrix":
        rows = cfg.get("rows") or []
        columns = cfg.get("columns") or []
        if not rows:
            errors.append("A matrix field requires at least one row.")
        elif len(rows) > MAX_MATRIX_ROWS:
            errors.append(f"A matrix field can have at most {MAX_MATRIX_ROWS} rows.")
        if not columns:
            errors.append("A matrix field requires at least one column.")
        elif len(columns) > MAX_MATRIX_COLS:
            errors.append(f"A matrix field can have at most {MAX_MATRIX_COLS} columns.")
        if len({str(row) for row in rows}) != len(rows):
            errors.append("Matrix rows must be unique.")
        if len({str(column) for column in columns}) != len(columns):
            errors.append("Matrix columns must be unique.")
        if cfg.get("mode", "row") not in MATRIX_MODES:
            errors.append("Choose one answer per row or one answer per table for a matrix field.")
    elif field_type == "rating":
        try:
            max_value = int(cfg.get("max", MAX_RATING))
        except (TypeError, ValueError):
            max_value = 0
        if not (1 <= max_value <= MAX_RATING):
            errors.append(f"A rating field max value must be between 1 and {MAX_RATING}.")
    elif field_type == "audio":
        try:
            max_clips = int(cfg.get("max_clips", MAX_AUDIO_CLIPS))
        except (TypeError, ValueError):
            max_clips = 0
        if not (1 <= max_clips <= MAX_AUDIO_CLIPS):
            errors.append(f"An audio field must allow between 1 and {MAX_AUDIO_CLIPS} clips.")
    return errors


def condition_operators_for(field_type: str) -> list[str]:
    """Operators that make sense for answers to ``field_type``."""
    if field_type == "checkbox":
        return ["equals", "not_equals"]
    if field_type in OPTION_TYPES:
        return ["equals", "not_equals", "empty", "not_empty"]
    if field_type in NUMERIC_CONDITION_TYPES or field_type in {"date", "time"}:
        return ["equals", "not_equals", "greater_than", "less_than", "empty", "not_empty"]
    return ["equals", "not_equals", "contains", "not_contains", "empty", "not_empty"]


def _condition_dependencies(field: dict[str, Any]) -> set[int]:
    rules = (field.get("conditions") or {}).get("rules") or []
    return {int(rule["field_id"]) for rule in rules if str(rule.get("field_id", "")).isdigit()}


def validate_conditions(
    conditions: dict[str, Any] | None,
    *,
    field_id: int | None,
    fields: list[dict[str, Any]],
) -> tuple[dict[str, Any] | None, list[str]]:
    """Validate a field's conditional logic against the other fields on its form.

    Returns the normalised conditions (``None`` when the field is always
    shown) and any error messages.
    """
    rules = (conditions or {}).get("rules") or []
    if not rules:
        return None, []
    by_id = {int(field["id"]): field for field in fields}
    errors: list[str] = []
    clean_rules: list[dict[str, Any]] = []
    for rule in rules:
        source = by_id.get(int(rule.get("field_id") or 0))
        if source is None or source["id"] == field_id:
            errors.append("Choose another question on this form for each condition.")
            continue
        source_type = source["field_type"]
        label = source["label"]
        if source_type not in CONDITION_SOURCE_TYPES:
            errors.append(f"'{label}' can't be used in a condition.")
            continue
        operator = str(rule.get("operator") or "")
        if operator not in condition_operators_for(source_type):
            errors.append(f"Choose a valid comparison for '{label}'.")
            continue
        value = str(rule.get("value") or "").strip()
        if operator in VALUELESS_OPERATORS:
            value = ""
        elif not value:
            errors.append(f"Enter the value to compare '{label}' with.")
            continue
        elif source_type == "checkbox" and value not in CHECKBOX_CONDITION_VALUES:
            errors.append(f"Choose Yes or No for '{label}'.")
            continue
        elif source_type in OPTION_TYPES and value not in (source.get("options") or []):
            errors.append(f"Choose one of the choices for '{label}'.")
            continue
        elif source_type in NUMERIC_CONDITION_TYPES and not _is_valid_number(value):
            errors.append(f"Enter a number to compare '{label}' with.")
            continue
        clean_rules.append({"field_id": int(source["id"]), "operator": operator, "value": value})
    if errors:
        return None, errors

    # Reject loops (A shows when B..., B shows when A...) before they are saved.
    if field_id is not None:
        graph = {fid: _condition_dependencies(field) for fid, field in by_id.items()}
        graph[field_id] = {rule["field_id"] for rule in clean_rules}
        pending, seen = list(graph[field_id]), set()
        while pending:
            current = pending.pop()
            if current == field_id:
                return None, ["These conditions depend on this question, which would create a loop."]
            if current not in seen:
                seen.add(current)
                pending.extend(graph.get(current, ()))
    match = "any" if (conditions or {}).get("match") == "any" else "all"
    return {"match": match, "rules": clean_rules}, []


def _condition_answers(field: dict[str, Any], raw: Any) -> list[str]:
    """Normalise a submitted answer into the list of strings rules compare."""
    if field["field_type"] == "checkbox":
        return [CHECKBOX_CONDITION_VALUES[0] if _truthy(raw) else CHECKBOX_CONDITION_VALUES[1]]
    if field["field_type"] == "hidden":
        raw = field.get("default_value")
    items = raw if isinstance(raw, (list, tuple)) else ([] if raw is None else [raw])
    return [str(item).strip() for item in items if str(item).strip()]


def _compare(field_type: str, answer: str, target: str) -> int | None:
    """Compare two answers: -1, 0 or 1, or None when they can't be compared."""
    if field_type in NUMERIC_CONDITION_TYPES:
        try:
            left, right = float(answer), float(target)
        except ValueError:
            return None
    else:
        left, right = answer.casefold(), target.casefold()
    return (left > right) - (left < right)


def condition_rule_matches(field_type: str, answers: list[str], operator: str, value: str | None) -> bool:
    """Return whether one rule holds for the source field's ``answers``."""
    target = str(value or "").strip()
    if operator == "empty":
        return not answers
    if operator == "not_empty":
        return bool(answers)
    if operator in {"equals", "not_equals"}:
        hit = any(_compare(field_type, answer, target) == 0 for answer in answers)
        return hit if operator == "equals" else not hit
    if operator in {"contains", "not_contains"}:
        hit = any(target.casefold() in answer.casefold() for answer in answers)
        return hit if operator == "contains" else not hit
    if operator in {"greater_than", "less_than"}:
        wanted = 1 if operator == "greater_than" else -1
        return any(_compare(field_type, answer, target) == wanted for answer in answers)
    return False


def visible_field_ids(fields: list[dict[str, Any]], values: dict[Any, Any]) -> set[int]:
    """Return the ids of fields whose conditions are met by ``values``.

    A field that depends on a hidden field sees that field as unanswered.
    Rules that point at a deleted field are ignored. Loops are rejected when
    conditions are saved; if one exists anyway, the field where the loop
    closes is treated as unanswered so evaluation always finishes.
    """
    by_id = {int(field["id"]): field for field in fields}
    state: dict[int, bool | None] = {}

    def visible(field_id: int) -> bool:
        if field_id in state:
            return bool(state[field_id])  # ``None``: still being evaluated (a loop)
        state[field_id] = None
        conditions = by_id[field_id].get("conditions") or {}
        results = []
        for rule in conditions.get("rules") or []:
            source = by_id.get(int(rule.get("field_id") or 0))
            if source is None or source["id"] == field_id:
                continue
            answers = _condition_answers(source, values.get(source["id"])) if visible(source["id"]) else []
            results.append(condition_rule_matches(source["field_type"], answers, rule.get("operator", ""), rule.get("value")))
        if not results:
            shown = True
        elif conditions.get("match") == "any":
            shown = any(results)
        else:
            shown = all(results)
        state[field_id] = shown
        return shown

    return {field_id for field_id in by_id if visible(field_id)}


def condition_builder_meta() -> dict[str, Any]:
    """Operator labels and per-type choices for the builder's condition editor."""
    return {
        "operators": dict(CONDITION_OPERATORS),
        "operators_by_type": {t: condition_operators_for(t) for t in sorted(CONDITION_SOURCE_TYPES)},
        "valueless": sorted(VALUELESS_OPERATORS),
        "checkbox_values": list(CHECKBOX_CONDITION_VALUES),
    }


def condition_summary(field: dict[str, Any], fields: list[dict[str, Any]]) -> str:
    """Describe a field's conditions in words for the builder, e.g. ``Shown when Urgent is Yes``."""
    by_id = {int(item["id"]): item for item in fields}
    conditions = field.get("conditions") or {}
    parts = []
    for rule in conditions.get("rules") or []:
        source = by_id.get(int(rule.get("field_id") or 0))
        if source is None:
            continue
        operator = CONDITION_OPERATORS.get(rule.get("operator", ""), rule.get("operator", ""))
        value = "" if rule.get("operator") in VALUELESS_OPERATORS else f" {rule.get('value', '')}"
        parts.append(f"{source['label']} {operator}{value}")
    if not parts:
        return ""
    joiner = " or " if conditions.get("match") == "any" else " and "
    return "Shown when " + joiner.join(parts)


def serialize_form(form: dict[str, Any], fields: list[dict[str, Any]]) -> dict[str, Any]:
    """Shape a form and its fields for the frontend."""
    return {
        "id": form["id"],
        "slug": form["slug"],
        "name": form["name"],
        "description": form.get("description"),
        "active": form.get("active", True),
        "fields": fields,
    }


def field_type_choices() -> list[dict[str, str]]:
    """Field type options for the builder UI dropdown."""
    return [{"value": value, "label": label} for value, label in FIELD_TYPES.items()]


def _is_valid_email(value: str) -> bool:
    return bool(_EMAIL_RE.match(value))


def _is_valid_number(value: str) -> bool:
    try:
        float(value)
    except ValueError:
        return False
    return True


def _is_valid_date(value: str) -> bool:
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        return False
    return True


def _is_valid_time(value: str) -> bool:
    return bool(_TIME_RE.match(value))


def _is_valid_url(value: str) -> bool:
    return value.startswith("http://") or value.startswith("https://")


def _truthy(raw: Any) -> bool:
    return raw in (True, 1, "1", "on", "true", "True")


def _display_value(field: dict[str, Any], value: Any) -> str:
    """Render a stored value as a short display string for the ticket body."""
    if value in (None, "", [], {}):
        return ""
    field_type = field.get("field_type")
    if isinstance(value, dict):
        # Matrix: {row_label: column_label}
        return "; ".join(f"{row}: {col}" for row, col in value.items())
    if isinstance(value, list):
        return ", ".join(str(item) for item in value)
    if field_type == "checkbox":
        return "Yes" if value else "No"
    if field_type == "rating":
        max_value = field.get("config", {}).get("max", MAX_RATING)
        return f"{value} / {max_value}"
    if field_type in SIGNATURE_TYPES:
        return "(signature image)"
    return str(value)


def validate_submission(
    fields: list[dict[str, Any]],
    values: dict[str, Any],
    files: dict[int, list[Any]] | None = None,
) -> tuple[dict[str, Any], list[str]]:
    """Validate submitted values against the field definitions.

    ``values`` holds non-file field values keyed by field id; ``files`` maps a
    file-field id to the list of uploaded files. Returns the cleaned data and
    any validation error messages.
    """
    files = files or {}
    clean: dict[str, Any] = {}
    errors: list[str] = []
    # Answers to fields hidden by conditional logic are neither required nor kept.
    visible = visible_field_ids(fields, values)

    for field in fields:
        field_id = field["id"]
        if field_id not in visible:
            continue
        field_type = field["field_type"]
        label = field["label"]
        required = field.get("required", False)
        options = field.get("options") or []
        config = field.get("config") or {}

        if field_type == "hidden":
            clean[field_id] = field.get("default_value") or ""
            continue

        if field_type in FILE_TYPES:
            uploaded = files.get(field_id) or []
            if required and not uploaded:
                errors.append(f"'{label}' is required.")
            if field_type in MULTI_FILE_TYPES:
                max_clips = int(config.get("max_clips", MAX_AUDIO_CLIPS))
                if len(uploaded) > max_clips:
                    errors.append(f"'{label}' allows at most {max_clips} clips.")
            clean[field_id] = [getattr(item, "filename", None) or str(item) for item in uploaded]
            continue

        raw = values.get(field_id)

        if field_type == "checkbox":
            checked = _truthy(raw)
            if required and not checked:
                errors.append(f"'{label}' is required.")
            clean[field_id] = checked
            continue

        if field_type in {"select", "radio", "multiselect"}:
            if field_type == "multiselect":
                items = raw if isinstance(raw, list) else ([raw] if raw not in (None, "") else [])
                items = [str(item).strip() for item in items if str(item).strip()]
                if required and not items:
                    errors.append(f"'{label}' is required.")
                if options:
                    for item in items:
                        if item not in options:
                            errors.append(f"Invalid option selected for '{label}'.")
                            break
                clean[field_id] = items
            else:
                value = "" if raw is None else str(raw).strip()
                if required and not value:
                    errors.append(f"'{label}' is required.")
                if value and options and value not in options:
                    errors.append(f"Invalid option selected for '{label}'.")
                clean[field_id] = value
            continue

        if field_type == "matrix":
            data = raw if isinstance(raw, dict) else {}
            data = {k: v for k, v in data.items() if v not in (None, "")}
            rows = config.get("rows") or []
            cols = config.get("columns") or []
            if required and not data:
                errors.append(f"'{label}' is required.")
            elif matrix_mode(config) == "table":
                if len(data) > 1:
                    errors.append(f"Choose only one answer in '{label}'.")
            elif required and rows and any(row not in data for row in rows):
                errors.append(f"Answer every row in '{label}'.")
            if rows:
                for row in data:
                    if row not in rows:
                        errors.append(f"Invalid row selected in '{label}'.")
                        break
            if cols:
                for col in data.values():
                    if col not in cols:
                        errors.append(f"Invalid column selected in '{label}'.")
                        break
            # Keep answers in the order the rows appear on the form.
            order = {str(row): index for index, row in enumerate(rows)}
            clean[field_id] = {
                str(k): str(v)
                for k, v in sorted(data.items(), key=lambda item: order.get(str(item[0]), len(order)))
            }
            continue

        if field_type == "rating":
            try:
                max_value = int(config.get("max", MAX_RATING))
            except (TypeError, ValueError):
                max_value = MAX_RATING
            rating = 0
            if raw not in (None, ""):
                try:
                    rating = int(str(raw).strip())
                except ValueError:
                    errors.append(f"'{label}' must be a whole number.")
            if rating < 0 or rating > max_value:
                errors.append(f"'{label}' must be between 0 and {max_value}.")
                rating = max(0, min(rating, max_value))
            if required and not rating:
                errors.append(f"'{label}' is required.")
            clean[field_id] = rating
            continue

        if field_type in SIGNATURE_TYPES:
            value = "" if raw is None else str(raw).strip()
            if required and not value:
                errors.append(f"'{label}' is required.")
            if value and not value.startswith("data:"):
                errors.append(f"'{label}' must be an image.")
            clean[field_id] = value
            continue

        value = "" if raw is None else str(raw).strip()
        if field_type == "email":
            if required and not value:
                errors.append(f"'{label}' is required.")
            elif value and not _is_valid_email(value):
                errors.append(f"'{label}' must be a valid email address.")
        elif field_type == "number":
            if required and not value:
                errors.append(f"'{label}' is required.")
            elif value and not _is_valid_number(value):
                errors.append(f"'{label}' must be a number.")
        elif field_type == "date":
            if required and not value:
                errors.append(f"'{label}' is required.")
            elif value and not _is_valid_date(value):
                errors.append(f"'{label}' must be a valid date (YYYY-MM-DD).")
        elif field_type == "time":
            if required and not value:
                errors.append(f"'{label}' is required.")
            elif value and not _is_valid_time(value):
                errors.append(f"'{label}' must be a valid time (HH:MM).")
        elif field_type == "url":
            if required and not value:
                errors.append(f"'{label}' is required.")
            elif value and not _is_valid_url(value):
                errors.append(f"'{label}' must be a valid URL.")
        else:
            if required and not value:
                errors.append(f"'{label}' is required.")
        clean[field_id] = value

    return clean, errors


def build_description(form_name: str, fields: list[dict[str, Any]], clean: dict[str, Any]) -> str:
    """Build a human-readable, HTML-safe ticket description from submission data."""
    lines = [f"<h2>Form submission: {html.escape(form_name)}</h2>"]
    for field in fields:
        if field["field_type"] == "hidden" or field["id"] not in clean:
            continue
        label = html.escape(field["label"])
        raw = clean.get(field["id"])
        if (
            field["field_type"] == "matrix"
            and isinstance(raw, dict)
            and raw
            and matrix_mode(field.get("config")) == "row"
        ):
            # One answer per row: list each row with its chosen column.
            items = "".join(
                f"<li>{html.escape(str(row))}: {html.escape(str(column))}</li>"
                for row, column in raw.items()
            )
            lines.append(f"<p><strong>{label}:</strong></p><ul>{items}</ul>")
            continue
        value = _display_value(field, raw)
        lines.append(f"<p><strong>{label}:</strong> {html.escape(value)}</p>")
    return "\n".join(lines)


class FormValidationError(Exception):
    """Raised when a submission fails field validation."""

    def __init__(self, errors: list[str]):
        super().__init__("; ".join(errors))
        self.errors = list(errors)


async def submit_form(
    *,
    form: dict[str, Any],
    fields: list[dict[str, Any]],
    values: dict[str, Any],
    files: dict[int, list[Any]],
    requester: dict[str, Any],
    ip_address: str | None = None,
    user_agent: str | None = None,
) -> dict[str, Any]:
    """Validate a submission and create the ticket with private file attachments.

    Returns a dict with ``ticket_id``, ``submission_id`` and ``attachments``.
    Raises :class:`FormValidationError` when validation fails.
    """
    clean, errors = validate_submission(fields, values, files)
    if errors:
        raise FormValidationError(errors)

    # Imported lazily to avoid an import cycle with the tickets service.
    from app.services import ticket_attachments as attachment_service
    from app.services import tickets as tickets_service

    requester_id = requester.get("id")
    company_id = requester.get("company_id")
    if not company_id:
        raise FormValidationError(["This account has no associated company."])

    description = build_description(form["name"], fields, clean)
    subject = f"Form submission: {form['name']}"

    ticket = await tickets_service.create_ticket(
        subject=subject,
        description=description,
        requester_id=requester_id,
        company_id=company_id,
        assigned_user_id=None,
        priority="normal",
        status="new",
        category=None,
        module_slug=None,
        external_reference=None,
        send_creation_notification=True,
        record_initial_reply=False,
    )
    ticket_id = int(ticket["id"]) if ticket and ticket.get("id") is not None else None
    if ticket_id is None:
        raise FormValidationError(["Failed to create a ticket for this submission."])

    attachments: list[dict[str, Any]] = []
    for field in fields:
        if field["field_type"] not in FILE_TYPES or field["id"] not in clean:
            continue
        for uploaded in (files.get(field["id"]) or []):
            try:
                saved = await attachment_service.save_uploaded_file(
                    ticket_id=ticket_id,
                    file=uploaded,
                    access_level="closed",
                    uploaded_by_user_id=requester_id,
                )
            except Exception as exc:  # noqa: BLE001 - keep the submission alive
                log_error(
                    "Custom form file attachment failed",
                    field_id=field["id"],
                    ticket_id=ticket_id,
                    error=str(exc),
                )
                continue
            attachments.append(
                {
                    "id": saved.get("id"),
                    "filename": saved.get("filename"),
                    "size": saved.get("size"),
                    "content_type": saved.get("content_type"),
                }
            )

    data_json = json.dumps(clean, ensure_ascii=False)
    submission = await forms_repo.create_submission(
        form_id=form["id"],
        ticket_id=ticket_id,
        data_json=data_json,
        ip_address=ip_address,
        user_agent=user_agent,
        submitted_by=requester_id,
    )

    log_info(
        "Custom form submitted",
        form_id=form["id"],
        ticket_id=ticket_id,
        attachments=len(attachments),
    )
    return {
        "ticket_id": ticket_id,
        "submission_id": submission.get("id"),
        "attachments": attachments,
    }