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
    "select": "Dropdown (single choice)",
    "radio": "Radio buttons (single choice)",
    "multiselect": "Multiple choice",
    "checkbox": "Check box (yes/no)",
    "date": "Date",
    "file": "File upload",
    "hidden": "Hidden field",
}

#: Field types that expose an ``options`` list to the submitter.
OPTION_TYPES: frozenset[str] = frozenset({"select", "radio", "multiselect"})

#: Field types that accept uploaded files.
FILE_TYPES: frozenset[str] = frozenset({"file"})

MAX_OPTIONS = 100
MAX_OPTION_LENGTH = 255

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
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


def _truthy(raw: Any) -> bool:
    return raw in (True, 1, "1", "on", "true", "True")


def _display_value(field: dict[str, Any], value: Any) -> str:
    """Render a stored value as a short display string for the ticket body."""
    if value in (None, ""):
        return ""
    if isinstance(value, list):
        return ", ".join(str(item) for item in value)
    if field["field_type"] == "checkbox":
        return "Yes" if value else "No"
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

        if field_type == "hidden":
            clean[field_id] = field.get("default_value") or ""
            continue

        if field_type in FILE_TYPES:
            uploaded = files.get(field_id) or []
            if required and not uploaded:
                errors.append(f"'{label}' is required.")
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