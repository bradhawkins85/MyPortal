from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from app.core.database import db

AutomationRecord = dict[str, Any]
AutomationRunRecord = dict[str, Any]


def _serialise(value: Any) -> str | None:
    if value is None:
        return None
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


def _make_aware(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    return None


def _prepare_for_storage(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _normalise_automation(row: dict[str, Any]) -> AutomationRecord:
    record = dict(row)
    for key in ("id",):
        if key in record and record[key] is not None:
            record[key] = int(record[key])
    for key in ("created_at", "updated_at", "next_run_at", "last_run_at"):
        record[key] = _make_aware(record.get(key))
    record["trigger_filters"] = _deserialise(record.get("trigger_filters"))
    record["action_payload"] = _deserialise(record.get("action_payload"))
    return record


def _normalise_run(row: dict[str, Any]) -> AutomationRunRecord:
    record = dict(row)
    for key in ("id", "automation_id", "duration_ms"):
        if key in record and record[key] is not None:
            record[key] = int(record[key])
    for key in ("started_at", "finished_at"):
        record[key] = _make_aware(record.get(key))
    record["result_payload"] = _deserialise(record.get("result_payload"))
    return record


async def create_automation(
    *,
    name: str,
    description: str | None,
    kind: str,
    cadence: str | None,
    cron_expression: str | None,
    trigger_event: str | None,
    trigger_filters: Any,
    action_module: str | None,
    action_payload: Any,
    status: str,
    next_run_at: datetime | None,
) -> AutomationRecord:
    await db.execute(
        """
        INSERT INTO automations
            (name, description, kind, cadence, cron_expression, trigger_event, trigger_filters, action_module, action_payload, status, next_run_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            name,
            description,
            kind,
            cadence,
            cron_expression,
            trigger_event,
            _serialise(trigger_filters),
            action_module,
            _serialise(action_payload),
            status,
            _prepare_for_storage(next_run_at),
        ),
    )
    row = await db.fetch_one("SELECT * FROM automations WHERE id = LAST_INSERT_ID()")
    return _normalise_automation(row) if row else {}


async def get_automation(automation_id: int) -> AutomationRecord | None:
    row = await db.fetch_one(
        "SELECT * FROM automations WHERE id = %s",
        (automation_id,),
    )
    return _normalise_automation(row) if row else None


async def list_automations(
    *,
    status: str | None = None,
    kind: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[AutomationRecord]:
    where: list[str] = []
    params: list[Any] = []
    if status:
        where.append("status = %s")
        params.append(status)
    if kind:
        where.append("kind = %s")
        params.append(kind)
    where_clause = " WHERE " + " AND ".join(where) if where else ""
    params.extend([limit, offset])
    rows = await db.fetch_all(
        f"""
        SELECT *
        FROM automations
        {where_clause}
        ORDER BY updated_at DESC
        LIMIT %s OFFSET %s
        """,
        tuple(params),
    )
    return [_normalise_automation(row) for row in rows]


async def update_automation(automation_id: int, **fields: Any) -> AutomationRecord | None:
    if not fields:
        return await get_automation(automation_id)
    assignments: list[str] = []
    params: list[Any] = []
    for key, value in fields.items():
        if key in {"trigger_filters", "action_payload"}:
            assignments.append(f"{key} = %s")
            params.append(_serialise(value))
        else:
            assignments.append(f"{key} = %s")
            params.append(value)
    assignments.append("updated_at = UTC_TIMESTAMP(6)")
    params.append(automation_id)
    query = f"UPDATE automations SET {', '.join(assignments)} WHERE id = %s"
    await db.execute(query, tuple(params))
    return await get_automation(automation_id)


async def delete_automation(automation_id: int) -> None:
    await db.execute("DELETE FROM automations WHERE id = %s", (automation_id,))


async def record_run(
    *,
    automation_id: int,
    status: str,
    started_at: datetime,
    finished_at: datetime | None,
    duration_ms: int | None,
    result_payload: Any,
    error_message: str | None,
) -> AutomationRunRecord:
    await db.execute(
        """
        INSERT INTO automation_runs
            (automation_id, status, started_at, finished_at, duration_ms, result_payload, error_message)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        """,
        (
            automation_id,
            status,
            _prepare_for_storage(started_at),
            _prepare_for_storage(finished_at),
            duration_ms,
            _serialise(result_payload),
            error_message,
        ),
    )
    row = await db.fetch_one("SELECT * FROM automation_runs WHERE id = LAST_INSERT_ID()")
    return _normalise_run(row) if row else {}


async def list_runs(automation_id: int, *, limit: int = 50) -> list[AutomationRunRecord]:
    rows = await db.fetch_all(
        """
        SELECT *
        FROM automation_runs
        WHERE automation_id = %s
        ORDER BY started_at DESC
        LIMIT %s
        """,
        (automation_id, limit),
    )
    return [_normalise_run(row) for row in rows]


async def mark_started(automation_id: int) -> None:
    await db.execute(
        """
        UPDATE automations
        SET last_run_at = UTC_TIMESTAMP(6), last_error = NULL
        WHERE id = %s
        """,
        (automation_id,),
    )


async def set_next_run(automation_id: int, next_run_at: datetime | None) -> None:
    await db.execute(
        """
        UPDATE automations
        SET next_run_at = %s, updated_at = UTC_TIMESTAMP(6)
        WHERE id = %s
        """,
        (_prepare_for_storage(next_run_at), automation_id),
    )


async def list_due_automations(limit: int = 20) -> list[AutomationRecord]:
    rows = await db.fetch_all(
        """
        SELECT *
        FROM automations
        WHERE status = 'active'
          AND next_run_at IS NOT NULL
          AND next_run_at <= UTC_TIMESTAMP(6)
        ORDER BY next_run_at ASC
        LIMIT %s
        """,
        (limit,),
    )
    return [_normalise_automation(row) for row in rows]


async def set_last_error(automation_id: int, message: str | None) -> None:
    await db.execute(
        """
        UPDATE automations
        SET last_error = %s, updated_at = UTC_TIMESTAMP(6)
        WHERE id = %s
        """,
        (message, automation_id),
    )
