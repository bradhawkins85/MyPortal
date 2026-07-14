from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from inspect import isawaitable

from app.core.database import db

AutomationRecord = dict[str, Any]
AutomationRunRecord = dict[str, Any]
AutomationHistoryRecord = dict[str, Any]


async def _ensure_connection() -> None:
    """Ensure the automation repository has an active database connection."""

    is_connected = getattr(db, "is_connected", None)
    if callable(is_connected):
        try:
            if is_connected():
                return
        except Exception:  # pragma: no cover - defensive guard
            pass
    connect = getattr(db, "connect", None)
    if not connect:
        return
    result = connect()
    if isawaitable(result):
        await result


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
    for key in ("id", "execution_order"):
        if key in record and record[key] is not None:
            record[key] = int(record[key])
    for key in ("created_at", "updated_at", "next_run_at", "last_run_at", "scheduled_time"):
        record[key] = _make_aware(record.get(key))
    for key in ("run_once",):
        if key in record:
            record[key] = bool(record[key])
    record["trigger_filters"] = _deserialise(record.get("trigger_filters"))
    record["action_payload"] = _deserialise(record.get("action_payload"))
    return record


def _normalise_history(row: dict[str, Any]) -> AutomationHistoryRecord:
    record = dict(row)
    for key in ("id", "automation_id", "automation_run_id", "ticket_id"):
        if key in record and record[key] is not None:
            record[key] = int(record[key])
    record["occurred_at"] = _make_aware(record.get("occurred_at"))
    record["previous_values"] = _deserialise(record.get("previous_values"))
    record["result_payload"] = _deserialise(record.get("result_payload"))
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
    execution_order: int = 0,
    cadence: str | None,
    cron_expression: str | None,
    scheduled_time: datetime | None,
    run_once: bool,
    trigger_event: str | None,
    trigger_filters: Any,
    action_module: str | None,
    action_payload: Any,
    status: str,
    next_run_at: datetime | None,
) -> AutomationRecord:
    await _ensure_connection()
    automation_id = await db.execute_returning_lastrowid(
        """
        INSERT INTO automations (
            name,
            description,
            kind,
            execution_order,
            cadence,
            cron_expression,
            trigger_event,
            trigger_filters,
            action_module,
            action_payload,
            status,
            next_run_at,
            scheduled_time,
            run_once
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            name,
            description,
            kind,
            execution_order,
            cadence,
            cron_expression,
            trigger_event,
            _serialise(trigger_filters),
            action_module,
            _serialise(action_payload),
            status,
            _prepare_for_storage(next_run_at),
            _prepare_for_storage(scheduled_time),
            run_once,
        ),
    )
    row = await db.fetch_one("SELECT * FROM automations WHERE id = %s", (automation_id,))
    if not row:
        row = {
            "id": automation_id,
            "name": name,
            "description": description,
            "kind": kind,
            "execution_order": execution_order,
            "cadence": cadence,
            "cron_expression": cron_expression,
            "trigger_event": trigger_event,
            "trigger_filters": trigger_filters,
            "action_module": action_module,
            "action_payload": action_payload,
            "status": status,
            "next_run_at": next_run_at,
            "scheduled_time": scheduled_time,
            "run_once": run_once,
            "last_run_at": None,
            "last_error": None,
            "created_at": None,
            "updated_at": None,
        }
    return _normalise_automation(row)


async def get_automation(automation_id: int) -> AutomationRecord | None:
    await _ensure_connection()
    row = await db.fetch_one(
        "SELECT * FROM automations WHERE id = %s",
        (automation_id,),
    )
    return _normalise_automation(row) if row else None


def _copy_name(base_name: Any, existing_names: set[str]) -> str:
    name = str(base_name or "Automation").strip() or "Automation"
    clone_name = f"{name} (copy)"
    suffix = 2
    while clone_name in existing_names:
        clone_name = f"{name} (copy {suffix})"
        suffix += 1
    return clone_name


async def clone_automation(automation_id: int, *, next_run_at: datetime | None = None) -> AutomationRecord | None:
    original = await get_automation(automation_id)
    if not original:
        return None

    existing = await list_automations(limit=1000)
    existing_names = {str(item.get("name")) for item in existing if item.get("name")}
    clone_name = _copy_name(original.get("name"), existing_names)

    return await create_automation(
        name=clone_name,
        description=original.get("description"),
        kind=str(original.get("kind") or "scheduled"),
        execution_order=int(original.get("execution_order") or 0),
        cadence=original.get("cadence"),
        cron_expression=original.get("cron_expression"),
        scheduled_time=original.get("scheduled_time"),
        run_once=bool(original.get("run_once", False)),
        trigger_event=original.get("trigger_event"),
        trigger_filters=original.get("trigger_filters"),
        action_module=original.get("action_module"),
        action_payload=original.get("action_payload"),
        status=str(original.get("status") or "inactive"),
        next_run_at=next_run_at,
    )


async def list_automations(
    *,
    status: str | None = None,
    kind: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> list[AutomationRecord]:
    await _ensure_connection()
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
        ORDER BY
            CASE WHEN execution_order = 0 THEN 1 ELSE 0 END ASC,
            CASE WHEN execution_order = 0 THEN NULL ELSE execution_order END ASC,
            CASE WHEN execution_order = 0 THEN updated_at ELSE NULL END DESC,
            id ASC
        LIMIT %s OFFSET %s
        """,
        tuple(params),
    )
    return [_normalise_automation(row) for row in rows]


async def update_automation_order(ordered_ids: list[int]) -> list[AutomationRecord]:
    """Update display order for ordered automations without changing legacy order-0 rows."""

    await _ensure_connection()
    unique_ids: list[int] = []
    seen: set[int] = set()
    for automation_id in ordered_ids:
        try:
            normalised_id = int(automation_id)
        except (TypeError, ValueError):
            continue
        if normalised_id > 0 and normalised_id not in seen:
            unique_ids.append(normalised_id)
            seen.add(normalised_id)

    if not unique_ids:
        return []

    placeholders = ", ".join(["%s"] * len(unique_ids))
    rows = await db.fetch_all(
        f"SELECT id, execution_order FROM automations WHERE id IN ({placeholders})",
        tuple(unique_ids),
    )
    existing_orders = {
        int(row_dict["id"]): int(row_dict.get("execution_order") or 0)
        for row_dict in (dict(row) for row in rows)
    }
    ordered_existing_ids = [automation_id for automation_id in unique_ids if automation_id in existing_orders]
    reorderable_ids = [automation_id for automation_id in ordered_existing_ids if existing_orders[automation_id] > 0]

    for order, automation_id in enumerate(reorderable_ids, start=1):
        if existing_orders.get(automation_id) == order:
            continue
        await db.execute(
            "UPDATE automations SET execution_order = %s, updated_at = UTC_TIMESTAMP(6) WHERE id = %s AND execution_order <> 0",
            (order, automation_id),
        )

    refreshed_rows = await db.fetch_all(
        f"SELECT * FROM automations WHERE id IN ({placeholders})",
        tuple(unique_ids),
    )
    by_id = {int(row["id"]): _normalise_automation(row) for row in refreshed_rows}
    return [by_id[automation_id] for automation_id in unique_ids if automation_id in by_id]


async def update_automation(automation_id: int, **fields: Any) -> AutomationRecord | None:
    await _ensure_connection()
    if not fields:
        return await get_automation(automation_id)
    assignments: list[str] = []
    params: list[Any] = []
    for key, value in fields.items():
        if key in {"trigger_filters", "action_payload"}:
            assignments.append(f"{key} = %s")
            params.append(_serialise(value))
        elif key in {"next_run_at", "scheduled_time"}:
            assignments.append(f"{key} = %s")
            params.append(_prepare_for_storage(value))
        else:
            assignments.append(f"{key} = %s")
            params.append(value)
    assignments.append("updated_at = UTC_TIMESTAMP(6)")
    params.append(automation_id)
    query = f"UPDATE automations SET {', '.join(assignments)} WHERE id = %s"
    await db.execute(query, tuple(params))
    return await get_automation(automation_id)


async def delete_automation(automation_id: int) -> None:
    await _ensure_connection()
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
    await _ensure_connection()
    run_id = await db.execute_returning_lastrowid(
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
    row = await db.fetch_one("SELECT * FROM automation_runs WHERE id = %s", (run_id,))
    if not row:
        row = {
            "id": run_id,
            "automation_id": automation_id,
            "status": status,
            "started_at": started_at,
            "finished_at": finished_at,
            "duration_ms": duration_ms,
            "result_payload": result_payload,
            "error_message": error_message,
        }
    return _normalise_run(row)


async def list_runs(automation_id: int, *, limit: int = 50) -> list[AutomationRunRecord]:
    await _ensure_connection()
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
    await _ensure_connection()
    await db.execute(
        """
        UPDATE automations
        SET last_run_at = UTC_TIMESTAMP(6), last_error = NULL
        WHERE id = %s
        """,
        (automation_id,),
    )


async def set_next_run(automation_id: int, next_run_at: datetime | None) -> None:
    await _ensure_connection()
    await db.execute(
        """
        UPDATE automations
        SET next_run_at = %s, updated_at = UTC_TIMESTAMP(6)
        WHERE id = %s
        """,
        (_prepare_for_storage(next_run_at), automation_id),
    )


async def list_due_automations(limit: int = 20) -> list[AutomationRecord]:
    await _ensure_connection()
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
    await _ensure_connection()
    await db.execute(
        """
        UPDATE automations
        SET last_error = %s, updated_at = UTC_TIMESTAMP(6)
        WHERE id = %s
        """,
        (message, automation_id),
    )


async def list_event_automations(
    trigger_event: str,
    *,
    limit: int | None = None,
) -> list[AutomationRecord]:
    """Return active event-driven automations matching the provided trigger."""

    await _ensure_connection()
    query = """
        SELECT *
        FROM automations
        WHERE status = 'active'
          AND kind = 'event'
          AND trigger_event = %s
        ORDER BY execution_order ASC, id ASC
    """
    params: list[Any] = [trigger_event]
    if limit is not None:
        query += " LIMIT %s"
        params.append(limit)
    rows = await db.fetch_all(query, tuple(params))
    return [_normalise_automation(row) for row in rows]


async def record_history(
    *,
    automation_id: int,
    automation_run_id: int | None = None,
    occurred_at: datetime | None = None,
    action_name: str,
    action_module: str | None,
    ticket_id: int | None,
    ticket_number: str | None,
    status: str,
    previous_values: Any = None,
    result_payload: Any = None,
    error_message: str | None = None,
) -> AutomationHistoryRecord:
    await _ensure_connection()
    history_id = await db.execute_returning_lastrowid(
        """
        INSERT INTO automation_history (
            automation_id, automation_run_id, occurred_at, action_name, action_module,
            ticket_id, ticket_number, status, previous_values, result_payload, error_message
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            automation_id,
            automation_run_id,
            _prepare_for_storage(occurred_at or datetime.now(timezone.utc)),
            action_name,
            action_module,
            ticket_id,
            ticket_number,
            status,
            _serialise(previous_values),
            _serialise(result_payload),
            error_message,
        ),
    )
    row = await db.fetch_one("SELECT * FROM automation_history WHERE id = %s", (history_id,))
    if not row:
        row = {
            "id": history_id, "automation_id": automation_id, "automation_run_id": automation_run_id,
            "occurred_at": occurred_at or datetime.now(timezone.utc), "action_name": action_name,
            "action_module": action_module, "ticket_id": ticket_id, "ticket_number": ticket_number,
            "status": status, "previous_values": previous_values, "result_payload": result_payload,
            "error_message": error_message,
        }
    return _normalise_history(row)


async def list_history(automation_id: int, *, limit: int = 200) -> list[AutomationHistoryRecord]:
    await _ensure_connection()
    rows = await db.fetch_all(
        """
        SELECT *
        FROM automation_history
        WHERE automation_id = %s
        ORDER BY occurred_at DESC, id DESC
        LIMIT %s
        """,
        (automation_id, limit),
    )
    return [_normalise_history(row) for row in rows]


async def list_history_for_ticket(ticket_id: int, *, limit: int = 200) -> list[AutomationHistoryRecord]:
    await _ensure_connection()
    rows = await db.fetch_all(
        """
        SELECT history.*, automations.name AS automation_name
        FROM automation_history AS history
        LEFT JOIN automations ON automations.id = history.automation_id
        WHERE history.ticket_id = %s
        ORDER BY history.occurred_at DESC, history.id DESC
        LIMIT %s
        """,
        (ticket_id, limit),
    )
    return [_normalise_history(row) for row in rows]
