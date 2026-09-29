from __future__ import annotations

import json
from datetime import date, datetime, timezone
from typing import Any

from app.core.database import db


def _deserialise(value: Any) -> Any:
    if value is None or isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return None


def _normalise_schedule(row: dict[str, Any]) -> dict[str, Any]:
    record = dict(row)
    record["weekly_hours"] = _deserialise(record.get("weekly_hours")) or {}
    record["include_global_closures"] = bool(record.get("include_global_closures", True))
    if record.get("company_id") is not None:
        record["company_id"] = int(record["company_id"])
    return record


def _coerce_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


async def get_schedule(company_id: int | None) -> dict[str, Any] | None:
    if company_id is None:
        row = await db.fetch_one(
            "SELECT * FROM business_hours_schedules WHERE company_id IS NULL LIMIT 1", ()
        )
    else:
        row = await db.fetch_one(
            "SELECT * FROM business_hours_schedules WHERE company_id = %s LIMIT 1",
            (company_id,),
        )
    return _normalise_schedule(row) if row else None


async def save_schedule(
    company_id: int | None,
    *,
    timezone_name: str,
    weekly_hours: dict[str, Any],
    include_global_closures: bool = True,
) -> None:
    existing = await get_schedule(company_id)
    payload = json.dumps(weekly_hours)
    if existing:
        await db.execute(
            "UPDATE business_hours_schedules SET timezone = %s, weekly_hours = %s, "
            "include_global_closures = %s WHERE id = %s",
            (timezone_name, payload, 1 if include_global_closures else 0, int(existing["id"])),
        )
        return
    await db.execute(
        "INSERT INTO business_hours_schedules (company_id, timezone, weekly_hours, include_global_closures) "
        "VALUES (%s, %s, %s, %s)",
        (company_id, timezone_name, payload, 1 if include_global_closures else 0),
    )


async def delete_schedule(company_id: int) -> None:
    await db.execute(
        "DELETE FROM business_hours_schedules WHERE company_id = %s", (company_id,)
    )


async def list_company_schedule_ids() -> set[int]:
    rows = await db.fetch_all(
        "SELECT company_id FROM business_hours_schedules WHERE company_id IS NOT NULL", ()
    )
    return {int(row["company_id"]) for row in rows}


async def list_closures(company_id: int | None) -> list[dict[str, Any]]:
    if company_id is None:
        rows = await db.fetch_all(
            "SELECT * FROM business_hours_closures WHERE company_id IS NULL ORDER BY closure_date",
            (),
        )
    else:
        rows = await db.fetch_all(
            "SELECT * FROM business_hours_closures WHERE company_id = %s ORDER BY closure_date",
            (company_id,),
        )
    records = []
    for row in rows:
        record = dict(row)
        record["closure_date"] = _coerce_date(record.get("closure_date"))
        records.append(record)
    return records


async def add_closure(company_id: int | None, closure_date: date, name: str | None) -> None:
    await db.execute(
        "INSERT INTO business_hours_closures (company_id, closure_date, name) VALUES (%s, %s, %s)",
        (company_id, closure_date.isoformat(), name or None),
    )


async def delete_closure(closure_id: int, company_id: int | None) -> None:
    if company_id is None:
        await db.execute(
            "DELETE FROM business_hours_closures WHERE id = %s AND company_id IS NULL",
            (closure_id,),
        )
    else:
        await db.execute(
            "DELETE FROM business_hours_closures WHERE id = %s AND company_id = %s",
            (closure_id, company_id),
        )


def _storage_datetime(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def _aware(value: Any) -> datetime | None:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


async def create_deferred_run(
    *,
    automation_id: int,
    event_name: str | None,
    company_id: int | None,
    ticket_id: int | None,
    context: Any,
    run_after: datetime,
) -> None:
    await db.execute(
        "INSERT INTO automation_deferred_runs "
        "(automation_id, event_name, company_id, ticket_id, context, run_after, status) "
        "VALUES (%s, %s, %s, %s, %s, %s, 'pending')",
        (
            automation_id,
            event_name,
            company_id,
            ticket_id,
            json.dumps(context, default=str) if context is not None else None,
            _storage_datetime(run_after),
        ),
    )


async def list_due_deferred_runs(now: datetime, *, limit: int = 50) -> list[dict[str, Any]]:
    rows = await db.fetch_all(
        "SELECT * FROM automation_deferred_runs WHERE status = 'pending' AND run_after <= %s "
        "ORDER BY run_after, id LIMIT %s",
        (_storage_datetime(now), int(limit)),
    )
    records = []
    for row in rows:
        record = dict(row)
        record["context"] = _deserialise(record.get("context"))
        record["run_after"] = _aware(record.get("run_after"))
        records.append(record)
    return records


async def claim_deferred_run(run_id: int) -> bool:
    return (
        await db.execute_rowcount(
            "UPDATE automation_deferred_runs SET status = 'processing' "
            "WHERE id = %s AND status = 'pending'",
            (run_id,),
        )
        > 0
    )


async def reschedule_deferred_run(run_id: int, run_after: datetime) -> None:
    await db.execute(
        "UPDATE automation_deferred_runs SET status = 'pending', run_after = %s WHERE id = %s",
        (_storage_datetime(run_after), run_id),
    )


async def finish_deferred_run(run_id: int, *, status: str, error_message: str | None = None) -> None:
    await db.execute(
        "UPDATE automation_deferred_runs SET status = %s, error_message = %s, processed_at = %s "
        "WHERE id = %s",
        (status, error_message, _storage_datetime(datetime.now(timezone.utc)), run_id),
    )


async def list_pending_deferred_runs_for_ticket(ticket_id: int) -> list[dict[str, Any]]:
    rows = await db.fetch_all(
        "SELECT d.id, d.automation_id, d.event_name, d.run_after, a.name AS automation_name "
        "FROM automation_deferred_runs d JOIN automations a ON a.id = d.automation_id "
        "WHERE d.ticket_id = %s AND d.status = 'pending' ORDER BY d.run_after",
        (ticket_id,),
    )
    records = []
    for row in rows:
        record = dict(row)
        record["run_after"] = _aware(record.get("run_after"))
        records.append(record)
    return records
