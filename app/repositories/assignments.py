from __future__ import annotations

import json
from typing import Any, Sequence

from app.core.database import db

AssignmentRecord = dict[str, Any]

_JSON_FIELDS = (
    "repeat_pattern",
    "point_overrides",
    "category_point_overrides",
    "rotation_state",
)


def _serialise(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return json.dumps(value, default=str)


def _deserialise(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return value


def _normalise_assignment(row: dict[str, Any]) -> AssignmentRecord:
    record = dict(row)
    for field in _JSON_FIELDS:
        record[field] = _deserialise(record.get(field))
    return record


async def replace_assignments(
    tenant_id: str, assignments: Sequence[dict[str, Any]]
) -> None:
    """Replace all assignments for a tenant with the provided list.

    Each assignment dict must contain the keys matching the
    ``tenant_assignments_v2`` table columns.  JSON fields
    (``repeat_pattern``, ``point_overrides``, ``category_point_overrides``,
    ``rotation_state``) are serialised to JSON strings before insertion so
    that the column count always matches the placeholder count.
    """
    await db.execute(
        "DELETE FROM tenant_assignments_v2 WHERE tenant_id = %s",
        (tenant_id,),
    )
    for assignment in assignments:
        await db.execute(
            """
            INSERT INTO tenant_assignments_v2
            (id, tenant_id, chore_id, child_id, assigned_at, assigned_for,
             start_date, end_date, days_of_week, repeat_pattern, time_of_day,
             time_window, point_overrides, category_point_overrides,
             rotation_state, status, points, sort_order)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (
                assignment.get("id"),
                tenant_id,
                assignment.get("chore_id"),
                assignment.get("child_id"),
                assignment.get("assigned_at"),
                assignment.get("assigned_for"),
                assignment.get("start_date"),
                assignment.get("end_date"),
                assignment.get("days_of_week"),
                _serialise(assignment.get("repeat_pattern")),
                assignment.get("time_of_day"),
                assignment.get("time_window"),
                _serialise(assignment.get("point_overrides")),
                _serialise(assignment.get("category_point_overrides")),
                _serialise(assignment.get("rotation_state")),
                assignment.get("status", 0),
                assignment.get("points", 0),
                assignment.get("sort_order", 0),
            ),
        )


async def get_assignments(tenant_id: str) -> list[AssignmentRecord]:
    """Return all assignments for a tenant."""
    rows = await db.fetch_all(
        "SELECT * FROM tenant_assignments_v2 WHERE tenant_id = %s ORDER BY sort_order ASC",
        (tenant_id,),
    )
    return [_normalise_assignment(row) for row in rows]
