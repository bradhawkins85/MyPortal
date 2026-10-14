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

    for field in fields:
        field_id = field["id"]
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
            rows = config.get("rows") or []
            cols = config.get("columns") or []
            if required and not data:
                errors.append(f"'{label}' is required.")
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
            clean[field_id] = {str(k): str(v) for k, v in data.items()}
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
        if field["field_type"] == "hidden":
            continue
        label = html.escape(field["label"])
        value = _display_value(field, clean.get(field["id"]))
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
        if field["field_type"] not in FILE_TYPES:
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