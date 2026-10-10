"""Storage for scheduled RMM scripts, onboarding steps and onboarding runs."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from app.core.database import db

ONBOARDING_ACTIVE = "running"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _decode_json(value: Any, default: Any) -> Any:
    if value is None or value == "":
        return default
    if isinstance(value, (list, dict)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default


def _schedule_row(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    schedule = dict(row)
    schedule["asset_ids"] = [int(item) for item in _decode_json(schedule.pop("asset_ids_json", None), [])]
    schedule["tag_ids"] = [int(item) for item in _decode_json(schedule.pop("tag_ids_json", None), [])]
    schedule["inputs"] = _decode_json(schedule.pop("inputs_json", None), [])
    schedule["is_enabled"] = bool(schedule.get("is_enabled"))
    return schedule


def _step_row(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    step = dict(row)
    step["inputs"] = _decode_json(step.pop("inputs_json", None), [])
    step["tag_ids"] = [int(item) for item in _decode_json(step.pop("tag_ids_json", None), [])]
    step["continue_on_failure"] = bool(step.get("continue_on_failure"))
    return step


def _onboarding_row(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    run = dict(row)
    run["plan"] = _decode_json(run.pop("plan_json", None), [])
    return run


# --------------------------------------------------------------------------- #
# Schedules
# --------------------------------------------------------------------------- #

_SCHEDULE_COLUMNS = (
    "s.id, s.name, s.company_id, s.script_id, s.cron, s.timezone, s.target_mode, s.asset_ids_json, "
    "s.tag_ids_json, s.inputs_json, s.timeout_seconds, s.is_enabled, s.next_run_at, s.last_run_at, "
    "s.last_run_summary, s.created_by_user_id, s.created_at, s.updated_at, sc.name AS script_name, "
    "sc.path AS script_path, sc.language AS script_language, sc.is_active AS script_active"
)


async def list_schedules(*, company_id: int) -> list[dict[str, Any]]:
    """Schedules that reach ``company_id``'s devices: every-company ones and its own."""

    rows = await db.fetch_all(
        f"SELECT {_SCHEDULE_COLUMNS} FROM rmm_schedules s JOIN rmm_scripts sc ON sc.id = s.script_id "  # nosec B608 - fixed columns
        "WHERE s.company_id IS NULL OR s.company_id = %s ORDER BY s.name, s.id",
        (company_id,),
    )
    return [_schedule_row(row) for row in rows or []]


async def get_schedule(schedule_id: int, *, with_secrets: bool = False) -> dict[str, Any] | None:
    extra = ", s.entries_encrypted" if with_secrets else ""
    row = await db.fetch_one(
        f"SELECT {_SCHEDULE_COLUMNS}{extra} FROM rmm_schedules s "  # nosec B608 - fixed columns
        "JOIN rmm_scripts sc ON sc.id = s.script_id WHERE s.id = %s",
        (schedule_id,),
    )
    return _schedule_row(row)


async def create_schedule(values: dict[str, Any]) -> int:
    now = _utcnow()
    return await db.execute_returning_lastrowid(
        "INSERT INTO rmm_schedules (name, company_id, script_id, cron, timezone, target_mode, asset_ids_json, "
        "tag_ids_json, entries_encrypted, inputs_json, timeout_seconds, is_enabled, next_run_at, "
        "created_by_user_id, created_at, updated_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (
            values["name"], values["company_id"], values["script_id"], values["cron"], values["timezone"],
            values["target_mode"], json.dumps(values["asset_ids"]), json.dumps(values["tag_ids"]),
            values["entries_encrypted"], json.dumps(values["inputs"]), values["timeout_seconds"],
            1 if values["is_enabled"] else 0, values["next_run_at"], values.get("created_by_user_id"), now, now,
        ),
    )


async def update_schedule(schedule_id: int, values: dict[str, Any]) -> None:
    await db.execute(
        "UPDATE rmm_schedules SET name = %s, company_id = %s, script_id = %s, cron = %s, timezone = %s, "
        "target_mode = %s, asset_ids_json = %s, tag_ids_json = %s, entries_encrypted = %s, inputs_json = %s, "
        "timeout_seconds = %s, is_enabled = %s, next_run_at = %s, updated_at = %s WHERE id = %s",
        (
            values["name"], values["company_id"], values["script_id"], values["cron"], values["timezone"],
            values["target_mode"], json.dumps(values["asset_ids"]), json.dumps(values["tag_ids"]),
            values["entries_encrypted"], json.dumps(values["inputs"]), values["timeout_seconds"],
            1 if values["is_enabled"] else 0, values["next_run_at"], _utcnow(), schedule_id,
        ),
    )


async def set_schedule_enabled(schedule_id: int, enabled: bool, next_run_at: datetime | None) -> None:
    await db.execute(
        "UPDATE rmm_schedules SET is_enabled = %s, next_run_at = %s, updated_at = %s WHERE id = %s",
        (1 if enabled else 0, next_run_at, _utcnow(), schedule_id),
    )


async def delete_schedule(schedule_id: int) -> None:
    await db.execute("DELETE FROM rmm_schedules WHERE id = %s", (schedule_id,))


async def due_schedule_ids(now: datetime) -> list[dict[str, Any]]:
    rows = await db.fetch_all(
        "SELECT id, next_run_at FROM rmm_schedules WHERE is_enabled = 1 AND next_run_at IS NOT NULL "
        "AND next_run_at <= %s ORDER BY next_run_at, id",
        (now,),
    )
    return [dict(row) for row in rows or []]


async def claim_schedule(schedule_id: int, previous_next: Any, new_next: datetime | None) -> bool:
    """Move a due schedule to its next time. ``False`` when another worker already did."""

    claimed = await db.execute_rowcount(
        "UPDATE rmm_schedules SET next_run_at = %s WHERE id = %s AND next_run_at = %s AND is_enabled = 1",
        (new_next, schedule_id, previous_next),
    )
    return claimed > 0


async def record_schedule_run(schedule_id: int, summary: str) -> None:
    now = _utcnow()
    await db.execute(
        "UPDATE rmm_schedules SET last_run_at = %s, last_run_summary = %s WHERE id = %s",
        (now, summary[:512], schedule_id),
    )


async def pending_schedule_agents(schedule_id: int) -> set[int]:
    """Agents still holding an unfinished run from this schedule."""

    rows = await db.fetch_all(
        "SELECT DISTINCT agent_id FROM rmm_script_runs WHERE schedule_id = %s "
        "AND status IN ('queued', 'dispatched', 'running') AND agent_id IS NOT NULL",
        (schedule_id,),
    )
    return {int(row["agent_id"]) for row in rows or []}


async def list_active_agents(*, company_id: int | None) -> list[dict[str, Any]]:
    """Active agents of one company, or of every company that is not archived."""

    if company_id is None:
        rows = await db.fetch_all(
            "SELECT g.* FROM rmm_agents g JOIN companies c ON c.id = g.company_id "
            "WHERE g.status = 'active' AND COALESCE(c.archived, 0) = 0 ORDER BY g.id"
        )
    else:
        rows = await db.fetch_all(
            "SELECT * FROM rmm_agents WHERE status = 'active' AND company_id = %s ORDER BY id", (company_id,)
        )
    return [dict(row) for row in rows or []]


# --------------------------------------------------------------------------- #
# Onboarding steps
# --------------------------------------------------------------------------- #

_STEP_COLUMNS = (
    "o.id, o.company_id, o.position, o.script_id, o.tag_ids_json, o.inputs_json, o.timeout_seconds, "
    "o.continue_on_failure, "
    "o.created_by_user_id, o.created_at, o.updated_at, sc.name AS script_name, sc.path AS script_path, "
    "sc.language AS script_language, sc.is_active AS script_active"
)


async def list_steps(*, company_id: int) -> list[dict[str, Any]]:
    """A company's onboarding steps, in order."""

    rows = await db.fetch_all(
        f"SELECT {_STEP_COLUMNS} FROM rmm_onboarding_steps o JOIN rmm_scripts sc ON sc.id = o.script_id "  # nosec B608 - fixed columns
        "WHERE o.company_id = %s ORDER BY o.position, o.id",
        (company_id,),
    )
    return [_step_row(row) for row in rows or []]


async def get_step(step_id: int, *, with_secrets: bool = False) -> dict[str, Any] | None:
    extra = ", o.entries_encrypted" if with_secrets else ""
    row = await db.fetch_one(
        f"SELECT {_STEP_COLUMNS}{extra} FROM rmm_onboarding_steps o "  # nosec B608 - fixed columns
        "JOIN rmm_scripts sc ON sc.id = o.script_id WHERE o.id = %s",
        (step_id,),
    )
    return _step_row(row)


async def create_step(values: dict[str, Any]) -> int:
    now = _utcnow()
    company_id = values["company_id"]
    row = await db.fetch_one(
        "SELECT MAX(position) AS top FROM rmm_onboarding_steps WHERE company_id = %s", (company_id,)
    )
    position = int((row or {}).get("top") or 0) + 1
    return await db.execute_returning_lastrowid(
        "INSERT INTO rmm_onboarding_steps (company_id, position, script_id, tag_ids_json, entries_encrypted, "
        "inputs_json, timeout_seconds, continue_on_failure, created_by_user_id, created_at, updated_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (
            company_id, position, values["script_id"], json.dumps(values["tag_ids"]), values["entries_encrypted"],
            json.dumps(values["inputs"]),
            values["timeout_seconds"], 1 if values["continue_on_failure"] else 0,
            values.get("created_by_user_id"), now, now,
        ),
    )


async def update_step(step_id: int, values: dict[str, Any]) -> None:
    await db.execute(
        "UPDATE rmm_onboarding_steps SET script_id = %s, tag_ids_json = %s, entries_encrypted = %s, "
        "inputs_json = %s, timeout_seconds = %s, continue_on_failure = %s, updated_at = %s WHERE id = %s",
        (
            values["script_id"], json.dumps(values["tag_ids"]), values["entries_encrypted"], json.dumps(values["inputs"]),
            values["timeout_seconds"], 1 if values["continue_on_failure"] else 0, _utcnow(), step_id,
        ),
    )


async def delete_step(step_id: int) -> None:
    await db.execute("DELETE FROM rmm_onboarding_steps WHERE id = %s", (step_id,))


async def set_step_positions(step_ids: list[int]) -> None:
    now = _utcnow()
    for position, step_id in enumerate(step_ids, start=1):
        await db.execute(
            "UPDATE rmm_onboarding_steps SET position = %s, updated_at = %s WHERE id = %s",
            (position, now, step_id),
        )


# --------------------------------------------------------------------------- #
# Onboarding runs
# --------------------------------------------------------------------------- #

_ONBOARDING_COLUMNS = (
    "b.id, b.agent_id, b.company_id, b.asset_id, b.status, b.plan_json, b.current_index, b.current_run_id, "
    "b.failed_steps, b.error_message, b.started_by_user_id, b.started_at, b.completed_at, "
    "a.name AS asset_name, g.hostname AS agent_hostname, u.email AS started_by_email"
)
_ONBOARDING_JOINS = (
    "LEFT JOIN assets a ON a.id = b.asset_id LEFT JOIN rmm_agents g ON g.id = b.agent_id "
    "LEFT JOIN users u ON u.id = b.started_by_user_id"
)


async def create_onboarding_run(
    *, agent_id: int, company_id: int, asset_id: int | None, plan: list[dict[str, Any]],
    started_by_user_id: int | None,
) -> int:
    return await db.execute_returning_lastrowid(
        "INSERT INTO rmm_onboarding_runs (agent_id, company_id, asset_id, status, plan_json, current_index, "
        "started_by_user_id, started_at) VALUES (%s, %s, %s, 'running', %s, -1, %s, %s)",
        (agent_id, company_id, asset_id, json.dumps(plan), started_by_user_id, _utcnow()),
    )


async def get_onboarding_run(onboarding_run_id: int) -> dict[str, Any] | None:
    row = await db.fetch_one(
        f"SELECT {_ONBOARDING_COLUMNS} FROM rmm_onboarding_runs b {_ONBOARDING_JOINS} "  # nosec B608 - fixed SQL
        "WHERE b.id = %s",
        (onboarding_run_id,),
    )
    return _onboarding_row(row)


async def list_onboarding_runs(*, company_id: int, limit: int = 50) -> list[dict[str, Any]]:
    rows = await db.fetch_all(
        f"SELECT {_ONBOARDING_COLUMNS} FROM rmm_onboarding_runs b {_ONBOARDING_JOINS} "  # nosec B608 - fixed SQL
        "WHERE b.company_id = %s ORDER BY b.started_at DESC, b.id DESC LIMIT %s",
        (company_id, max(1, min(int(limit), 200))),
    )
    return [_onboarding_row(row) for row in rows or []]


async def running_onboarding_ids() -> list[int]:
    rows = await db.fetch_all("SELECT id FROM rmm_onboarding_runs WHERE status = 'running' ORDER BY id")
    return [int(row["id"]) for row in rows or []]


async def agent_has_running_onboarding(agent_id: int) -> bool:
    row = await db.fetch_one(
        "SELECT id FROM rmm_onboarding_runs WHERE agent_id = %s AND status = 'running' LIMIT 1", (agent_id,)
    )
    return bool(row)


async def advance_onboarding_run(
    onboarding_run_id: int,
    *,
    expected_index: int,
    expected_run_id: int | None,
    new_index: int,
    new_run_id: int | None,
    plan: list[dict[str, Any]],
    failed_steps: int,
) -> bool:
    """Move to the next step unless another worker already moved this run on."""

    if expected_run_id is None:
        run_condition = "current_run_id IS NULL"
        params: tuple[Any, ...] = (new_index, new_run_id, json.dumps(plan), failed_steps, onboarding_run_id, expected_index)
    else:
        run_condition = "current_run_id = %s"
        params = (new_index, new_run_id, json.dumps(plan), failed_steps, onboarding_run_id, expected_index, expected_run_id)
    moved = await db.execute_rowcount(
        "UPDATE rmm_onboarding_runs SET current_index = %s, current_run_id = %s, plan_json = %s, "
        "failed_steps = %s WHERE id = %s AND status = 'running' AND current_index = %s "
        f"AND {run_condition}",  # nosec B608 - fixed condition
        params,
    )
    return moved > 0


async def finish_onboarding_run(
    onboarding_run_id: int,
    *,
    status: str,
    plan: list[dict[str, Any]],
    failed_steps: int,
    error_message: str | None,
    expected_index: int | None = None,
) -> bool:
    if expected_index is None:
        condition, params = "", ()
    else:
        condition, params = " AND current_index = %s", (expected_index,)
    finished = await db.execute_rowcount(
        "UPDATE rmm_onboarding_runs SET status = %s, plan_json = %s, failed_steps = %s, error_message = %s, "
        f"completed_at = %s WHERE id = %s AND status = 'running'{condition}",  # nosec B608 - fixed condition
        (status, json.dumps(plan), failed_steps, error_message, _utcnow(), onboarding_run_id, *params),
    )
    return finished > 0
