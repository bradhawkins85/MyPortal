"""Repository for custom ticket details.

Definitions are global; values are stored per ticket as text (multi-choice
answers are a JSON array). Queries use the portable ``?`` placeholder, which
:mod:`app.core.database` adapts for MySQL.
"""
from __future__ import annotations

import json
from typing import Any, Iterable, Sequence

from app.core.database import db

_DEFINITION_COLUMNS = (
    "id, field_key, label, help_text, field_type, options, config, visibility, "
    "editable, show_in_list, is_active, sort_order, created_at, updated_at"
)


def _decode_json(value: Any, default: Any) -> Any:
    if value is None or value == "":
        return default
    if isinstance(value, type(default)):
        return value
    try:
        parsed = json.loads(value)
    except (TypeError, ValueError):
        return default
    return parsed if isinstance(parsed, type(default)) else default


def _normalise_definition(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    record = dict(row)
    record["id"] = int(record["id"])
    record["options"] = [str(item) for item in _decode_json(record.get("options"), [])]
    record["config"] = _decode_json(record.get("config"), {})
    for flag in ("editable", "show_in_list", "is_active"):
        record[flag] = bool(int(record.get(flag) or 0))
    record["sort_order"] = int(record.get("sort_order") or 0)
    record["visibility"] = str(record.get("visibility") or "private")
    return record


async def list_definitions(*, include_inactive: bool = True) -> list[dict[str, Any]]:
    query = "SELECT " + _DEFINITION_COLUMNS + " FROM ticket_custom_field_definitions"
    if not include_inactive:
        query += " WHERE is_active = 1"
    query += " ORDER BY sort_order ASC, label ASC, id ASC"
    rows = await db.fetch_all(query)
    return [_normalise_definition(row) for row in rows or []]


async def get_definition(definition_id: int) -> dict[str, Any] | None:
    row = await db.fetch_one(
        "SELECT " + _DEFINITION_COLUMNS + " FROM ticket_custom_field_definitions WHERE id = ?",
        (definition_id,),
    )
    return _normalise_definition(row)


async def get_definition_by_key(field_key: str) -> dict[str, Any] | None:
    row = await db.fetch_one(
        "SELECT " + _DEFINITION_COLUMNS + " FROM ticket_custom_field_definitions WHERE field_key = ?",
        (field_key,),
    )
    return _normalise_definition(row)


def _definition_params(values: dict[str, Any]) -> tuple[Any, ...]:
    return (
        values["field_key"],
        values["label"],
        values.get("help_text") or None,
        values["field_type"],
        json.dumps(list(values.get("options") or [])),
        json.dumps(dict(values.get("config") or {})),
        values["visibility"],
        1 if values.get("editable") else 0,
        1 if values.get("show_in_list") else 0,
        1 if values.get("is_active", True) else 0,
        int(values.get("sort_order") or 0),
    )


async def create_definition(values: dict[str, Any]) -> int | None:
    result = await db.execute_returning_lastrowid(
        """
        INSERT INTO ticket_custom_field_definitions
            (field_key, label, help_text, field_type, options, config, visibility,
             editable, show_in_list, is_active, sort_order)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        _definition_params(values),
    )
    if isinstance(result, int) and result > 0:
        return result
    created = await get_definition_by_key(values["field_key"])
    return created["id"] if created else None


async def update_definition(definition_id: int, values: dict[str, Any]) -> None:
    await db.execute(
        """
        UPDATE ticket_custom_field_definitions
        SET field_key = ?, label = ?, help_text = ?, field_type = ?, options = ?, config = ?,
            visibility = ?, editable = ?, show_in_list = ?, is_active = ?, sort_order = ?
        WHERE id = ?
        """,
        (*_definition_params(values), definition_id),
    )


async def delete_definition(definition_id: int) -> None:
    await db.execute("DELETE FROM ticket_custom_field_values WHERE field_id = ?", (definition_id,))
    await db.execute("DELETE FROM ticket_custom_field_definitions WHERE id = ?", (definition_id,))


async def count_values_by_field() -> dict[int, int]:
    rows = await db.fetch_all(
        "SELECT field_id, COUNT(*) AS total FROM ticket_custom_field_values "
        "WHERE value_text IS NOT NULL GROUP BY field_id"
    )
    return {int(row["field_id"]): int(row.get("total") or 0) for row in rows or []}


async def get_ticket_values(ticket_id: int) -> dict[int, dict[str, Any]]:
    """Return ``{field_id: value_row}`` for one ticket."""
    rows = await db.fetch_all(
        "SELECT field_id, value_text, updated_by, updated_source, updated_at "
        "FROM ticket_custom_field_values WHERE ticket_id = ?",
        (ticket_id,),
    )
    return {int(row["field_id"]): dict(row) for row in rows or []}


async def get_values_for_tickets(
    ticket_ids: Iterable[int], field_ids: Sequence[int]
) -> dict[int, dict[int, str | None]]:
    """Return ``{ticket_id: {field_id: value_text}}`` for the given tickets and fields."""
    tickets = sorted({int(ticket_id) for ticket_id in ticket_ids})
    fields = sorted({int(field_id) for field_id in field_ids})
    if not tickets or not fields:
        return {}
    ticket_placeholders = ", ".join("?" for _ in tickets)
    field_placeholders = ", ".join("?" for _ in fields)
    query = (
        "SELECT ticket_id, field_id, value_text FROM ticket_custom_field_values "
        "WHERE ticket_id IN (" + ticket_placeholders + ") "
        "AND field_id IN (" + field_placeholders + ")"
    )
    rows = await db.fetch_all(query, (*tickets, *fields))
    result: dict[int, dict[int, str | None]] = {}
    for row in rows or []:
        result.setdefault(int(row["ticket_id"]), {})[int(row["field_id"])] = row.get("value_text")
    return result


async def set_value(
    ticket_id: int,
    field_id: int,
    value_text: str | None,
    *,
    updated_by: int | None = None,
    source: str = "user",
) -> None:
    """Insert or replace one ticket's value; ``None`` clears it."""
    if value_text is None:
        await db.execute(
            "DELETE FROM ticket_custom_field_values WHERE ticket_id = ? AND field_id = ?",
            (ticket_id, field_id),
        )
        return
    existing = await db.fetch_one(
        "SELECT id FROM ticket_custom_field_values WHERE ticket_id = ? AND field_id = ?",
        (ticket_id, field_id),
    )
    if existing:
        await db.execute(
            "UPDATE ticket_custom_field_values SET value_text = ?, updated_by = ?, updated_source = ? "
            "WHERE ticket_id = ? AND field_id = ?",
            (value_text, updated_by, source, ticket_id, field_id),
        )
    else:
        await db.execute(
            "INSERT INTO ticket_custom_field_values (ticket_id, field_id, value_text, updated_by, updated_source) "
            "VALUES (?, ?, ?, ?, ?)",
            (ticket_id, field_id, value_text, updated_by, source),
        )
