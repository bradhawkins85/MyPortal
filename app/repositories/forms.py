"""Repository for the Custom Forms feature.

All queries use the portable ``?`` placeholder (native SQLite, adapted to
``%s`` for MySQL by :mod:`app.core.database`).
"""
from __future__ import annotations

import json
from typing import Any

from app.core.database import db


def _decode_options(value: Any) -> list[str]:
    """Decode the ``options`` column (stored as a JSON array of strings)."""
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value]
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return []
    if isinstance(parsed, list):
        return [str(item) for item in parsed]
    return []


def _decode_config(value: Any) -> dict[str, Any]:
    """Decode the ``config`` column (stored as a JSON object)."""
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if isinstance(value, str) and value.strip():
        try:
            parsed = json.loads(value)
        except (TypeError, ValueError):
            return {}
        if isinstance(parsed, dict):
            return parsed
    return {}


def _normalise_form(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    normalised = dict(row)
    if normalised.get("id") is not None:
        normalised["id"] = int(normalised["id"])
    normalised["active"] = bool(int(normalised.get("active") or 0))
    if normalised.get("created_by") is not None:
        normalised["created_by"] = int(normalised["created_by"])
    return normalised


def _normalise_field(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    normalised = dict(row)
    if normalised.get("id") is not None:
        normalised["id"] = int(normalised["id"])
    if normalised.get("form_id") is not None:
        normalised["form_id"] = int(normalised["form_id"])
    normalised["required"] = bool(int(normalised.get("required") or 0))
    normalised["sort_order"] = int(normalised.get("sort_order") or 0)
    normalised["options"] = _decode_options(normalised.get("options"))
    normalised["config"] = _decode_config(normalised.get("config"))
    normalised["conditions"] = _decode_config(normalised.get("conditions")) or None
    return normalised


def _normalise_submission(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    normalised = dict(row)
    if normalised.get("id") is not None:
        normalised["id"] = int(normalised["id"])
    if normalised.get("form_id") is not None:
        normalised["form_id"] = int(normalised["form_id"])
    if normalised.get("ticket_id") is not None:
        normalised["ticket_id"] = int(normalised["ticket_id"])
    if normalised.get("submitted_by") is not None:
        normalised["submitted_by"] = int(normalised["submitted_by"])
    return normalised


# ---------------------------------------------------------------------------
# Forms
# ---------------------------------------------------------------------------


async def list_forms() -> list[dict[str, Any]]:
    rows = await db.fetch_all("SELECT * FROM custom_forms ORDER BY name")
    return [_normalise_form(row) for row in rows]


async def get_form(form_id: int) -> dict[str, Any] | None:
    row = await db.fetch_one("SELECT * FROM custom_forms WHERE id = ?", (form_id,))
    return _normalise_form(row)


async def get_form_by_slug(slug: str) -> dict[str, Any] | None:
    row = await db.fetch_one("SELECT * FROM custom_forms WHERE slug = ?", (slug,))
    return _normalise_form(row)


async def list_forms_for_user(user_id: int, *, is_super_admin: bool = False) -> list[dict[str, Any]]:
    """Return active forms the user may view and submit.

    A form is available when it is active and either it has no access rows
    (visible to everyone) or the user is explicitly granted access.
    """
    if is_super_admin:
        rows = await db.fetch_all("SELECT * FROM custom_forms WHERE active = 1 ORDER BY name")
    else:
        rows = await db.fetch_all(
            """
            SELECT f.*
            FROM custom_forms AS f
            WHERE f.active = 1
              AND (
                NOT EXISTS (
                    SELECT 1 FROM custom_form_access WHERE form_id = f.id
                )
                OR EXISTS (
                    SELECT 1 FROM custom_form_access
                    WHERE form_id = f.id AND user_id = ?
                )
              )
            ORDER BY f.name
            """,
            (user_id,),
        )
    return [_normalise_form(row) for row in rows]


async def create_form(
    *,
    slug: str,
    name: str,
    description: str | None,
    created_by: int | None,
) -> dict[str, Any]:
    form_id = await db.execute_returning_lastrowid(
        """
        INSERT INTO custom_forms (slug, name, description, active, created_by)
        VALUES (?, ?, ?, 1, ?)
        """,
        (slug, name, description, created_by),
    )
    form = await get_form(int(form_id))
    if not form:
        raise RuntimeError("Failed to create custom form")
    return form


async def update_form(
    form_id: int,
    *,
    name: str,
    description: str | None,
    active: bool,
) -> dict[str, Any]:
    await db.execute(
        """
        UPDATE custom_forms
        SET name = ?, description = ?, active = ?
        WHERE id = ?
        """,
        (name, description, 1 if active else 0, form_id),
    )
    form = await get_form(form_id)
    if not form:
        raise ValueError("Custom form not found after update")
    return form


async def delete_form(form_id: int) -> None:
    await db.execute("DELETE FROM custom_forms WHERE id = ?", (form_id,))


# ---------------------------------------------------------------------------
# Fields
# ---------------------------------------------------------------------------


async def list_fields(form_id: int) -> list[dict[str, Any]]:
    rows = await db.fetch_all(
        "SELECT * FROM custom_form_fields WHERE form_id = ? ORDER BY sort_order, id",
        (form_id,),
    )
    return [_normalise_field(row) for row in rows]


async def get_field(field_id: int) -> dict[str, Any] | None:
    row = await db.fetch_one("SELECT * FROM custom_form_fields WHERE id = ?", (field_id,))
    return _normalise_field(row)


async def create_field(
    *,
    form_id: int,
    field_type: str,
    label: str,
    placeholder: str | None,
    help_text: str | None,
    required: bool,
    options: list[str] | None,
    default_value: str | None,
    sort_order: int,
    config: dict[str, Any] | None = None,
    conditions: dict[str, Any] | None = None,
) -> dict[str, Any]:
    options_json = json.dumps(options) if options else None
    config_json = json.dumps(config) if config else None
    conditions_json = json.dumps(conditions) if conditions else None
    field_id = await db.execute_returning_lastrowid(
        """
        INSERT INTO custom_form_fields
        (form_id, field_type, label, placeholder, help_text, required, options, default_value, config, conditions, sort_order)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            form_id,
            field_type,
            label,
            placeholder,
            help_text,
            1 if required else 0,
            options_json,
            default_value,
            config_json,
            conditions_json,
            sort_order,
        ),
    )
    field = await get_field(int(field_id))
    if not field:
        raise RuntimeError("Failed to create custom form field")
    return field


async def update_field(
    field_id: int,
    *,
    field_type: str,
    label: str,
    placeholder: str | None,
    help_text: str | None,
    required: bool,
    options: list[str] | None,
    default_value: str | None,
    config: dict[str, Any] | None = None,
    conditions: dict[str, Any] | None = None,
) -> dict[str, Any]:
    options_json = json.dumps(options) if options else None
    config_json = json.dumps(config) if config else None
    conditions_json = json.dumps(conditions) if conditions else None
    await db.execute(
        """
        UPDATE custom_form_fields
        SET field_type = ?, label = ?, placeholder = ?, help_text = ?,
            required = ?, options = ?, default_value = ?, config = ?, conditions = ?
        WHERE id = ?
        """,
        (
            field_type,
            label,
            placeholder,
            help_text,
            1 if required else 0,
            options_json,
            default_value,
            config_json,
            conditions_json,
            field_id,
        ),
    )
    field = await get_field(field_id)
    if not field:
        raise ValueError("Custom form field not found after update")
    return field


async def delete_field(field_id: int) -> None:
    await db.execute("DELETE FROM custom_form_fields WHERE id = ?", (field_id,))


async def reorder_fields(form_id: int, ordered_field_ids: list[int]) -> None:
    """Re-sequence ``sort_order`` for the given field ordering."""
    for index, field_id in enumerate(ordered_field_ids):
        await db.execute(
            """
            UPDATE custom_form_fields
            SET sort_order = ?
            WHERE id = ? AND form_id = ?
            """,
            (index, int(field_id), form_id),
        )


# ---------------------------------------------------------------------------
# Submissions
# ---------------------------------------------------------------------------


async def create_submission(
    *,
    form_id: int,
    ticket_id: int | None,
    data_json: str,
    ip_address: str | None,
    user_agent: str | None,
    submitted_by: int | None,
) -> dict[str, Any]:
    submission_id = await db.execute_returning_lastrowid(
        """
        INSERT INTO custom_form_submissions
        (form_id, ticket_id, data, ip_address, user_agent, submitted_by)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (form_id, ticket_id, data_json, ip_address, user_agent, submitted_by),
    )
    row = await db.fetch_one(
        "SELECT * FROM custom_form_submissions WHERE id = ?", (int(submission_id),)
    )
    return _normalise_submission(row) or {"id": int(submission_id)}


async def list_submissions(form_id: int) -> list[dict[str, Any]]:
    rows = await db.fetch_all(
        "SELECT * FROM custom_form_submissions WHERE form_id = ? ORDER BY created_at DESC, id DESC",
        (form_id,),
    )
    return [_normalise_submission(row) for row in rows]


async def get_submission(submission_id: int) -> dict[str, Any] | None:
    row = await db.fetch_one(
        "SELECT * FROM custom_form_submissions WHERE id = ?", (submission_id,)
    )
    return _normalise_submission(row)


# ---------------------------------------------------------------------------
# Access
# ---------------------------------------------------------------------------


async def list_access_user_ids(form_id: int) -> list[int]:
    rows = await db.fetch_all(
        "SELECT user_id FROM custom_form_access WHERE form_id = ? ORDER BY user_id",
        (form_id,),
    )
    return [int(row["user_id"]) for row in rows]


async def has_access_rows(form_id: int) -> bool:
    row = await db.fetch_one(
        "SELECT 1 AS present FROM custom_form_access WHERE form_id = ? LIMIT 1",
        (form_id,),
    )
    return row is not None


async def can_access_form(form_id: int, user_id: int, *, is_super_admin: bool = False) -> bool:
    """Return whether the user may view/submit the form.

    Super admins may access any form. Otherwise a form with no access rows is
    open to every authenticated user, and a form with access rows is limited to
    the explicitly granted users.
    """
    if is_super_admin:
        return True
    if not await has_access_rows(form_id):
        return True
    row = await db.fetch_one(
        "SELECT 1 AS present FROM custom_form_access WHERE form_id = ? AND user_id = ?",
        (form_id, user_id),
    )
    return row is not None


async def grant_access(form_id: int, user_id: int) -> None:
    await db.execute(
        """
        INSERT INTO custom_form_access (form_id, user_id) VALUES (?, ?)
        ON DUPLICATE KEY UPDATE form_id = form_id
        """,
        (form_id, user_id),
    )


async def revoke_access(form_id: int, user_id: int) -> None:
    await db.execute(
        "DELETE FROM custom_form_access WHERE form_id = ? AND user_id = ?",
        (form_id, user_id),
    )

# Lookups use a digest of each bearer token; an encrypted copy lets admins
# reveal the link (and its QR code) again later.


async def create_share_link(*, form_id: int, company_id: int, token_hash: str,
                            token_encrypted: str | None, expires_at: Any,
                            created_by: int | None) -> dict[str, Any]:
    link_id = await db.execute_returning_lastrowid(
        "INSERT INTO custom_form_share_links (form_id, company_id, token_hash, token_encrypted, expires_at, created_by) VALUES (?, ?, ?, ?, ?, ?)",
        (form_id, company_id, token_hash, token_encrypted, expires_at, created_by),
    )
    return await db.fetch_one(
        "SELECT id, form_id, company_id, token_encrypted, expires_at, revoked_at, created_at FROM custom_form_share_links WHERE id = ?",
        (link_id,),
    )


async def list_share_links(form_id: int) -> list[dict[str, Any]]:
    return await db.fetch_all(
        "SELECT l.id, l.company_id, c.name AS company_name, l.token_encrypted, l.expires_at, l.revoked_at, l.created_at FROM custom_form_share_links l JOIN companies c ON c.id = l.company_id WHERE l.form_id = ? ORDER BY l.id DESC",
        (form_id,),
    )


async def get_share_link(form_id: int, link_id: int) -> dict[str, Any] | None:
    return await db.fetch_one(
        "SELECT l.id, l.form_id, l.company_id, c.name AS company_name, l.token_encrypted, l.expires_at, l.revoked_at, l.created_at FROM custom_form_share_links l JOIN companies c ON c.id = l.company_id WHERE l.id = ? AND l.form_id = ?",
        (link_id, form_id),
    )


async def get_share_link_by_hash(token_hash: str) -> dict[str, Any] | None:
    return await db.fetch_one("SELECT * FROM custom_form_share_links WHERE token_hash = ?", (token_hash,))


async def revoke_share_link(form_id: int, link_id: int) -> None:
    await db.execute(
        "UPDATE custom_form_share_links SET revoked_at = CURRENT_TIMESTAMP WHERE id = ? AND form_id = ? AND revoked_at IS NULL",
        (link_id, form_id),
    )
