"""Storage for RMM checks (:mod:`app.services.rmm_checks`)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Iterable

from app.core.database import db

_SELECT = (
    "SELECT c.id, c.name, c.company_id, c.asset_id, c.check_type, c.target, c.settings_json, c.duration_minutes, "
    "c.check_script_id, c.check_entries_encrypted, c.check_inputs_json, c.action, c.script_id, c.entries_encrypted, "
    "c.inputs_json, c.timeout_seconds, c.cooldown_minutes, c.schedule_mode, c.delay_minutes, c.run_time, "
    "c.schedule_timezone, c.is_enabled, c.auto_close_when_resolved, c.target_rules_json, c.actions_json, "
    "c.last_failed_at, c.last_summary, "
    "c.created_by_user_id, c.updated_by_user_id, c.created_at, c.updated_at, "
    "s.name AS script_name, cs.name AS check_script_name, a.name AS asset_name, co.name AS company_name "
    "FROM rmm_checks c "
    "LEFT JOIN rmm_scripts s ON s.id = c.script_id "
    "LEFT JOIN rmm_scripts cs ON cs.id = c.check_script_id "
    "LEFT JOIN assets a ON a.id = c.asset_id "
    "LEFT JOIN companies co ON co.id = c.company_id "
)

_STATE_COLUMNS = (
    "status", "value_text", "problem_since", "checked_at", "changed_at", "last_action_at", "ticket_id", "run_id",
    "next_run_at",
)
_STATE_SELECT = (
    "SELECT check_id, agent_id, status, value_text, problem_since, checked_at, changed_at, last_action_at, "
    "ticket_id, run_id, next_run_at, scheduled_at FROM rmm_check_states "
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _decode_json(value: Any, default: Any) -> Any:
    if value is None or value == "":
        return default
    if isinstance(value, (list, dict)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError, RecursionError):
        return default


def _as_datetime(value: Any) -> datetime | None:
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value)
        except ValueError:
            return None
    return value


def _check_row(row: dict[str, Any] | None, *, with_secrets: bool = False) -> dict[str, Any] | None:
    if not row:
        return None
    check = dict(row)
    settings = _decode_json(check.pop("settings_json", None), {})
    check["settings"] = settings if isinstance(settings, dict) else {}
    check["inputs"] = _decode_json(check.pop("inputs_json", None), [])
    check["check_inputs"] = _decode_json(check.pop("check_inputs_json", None), [])
    raw_rules = check.pop("target_rules_json", None)
    # Corrupt stored rules must never silently become unrestricted targeting.
    check["target_rules"] = None if raw_rules is None else (_decode_json(raw_rules, {}) or {})
    check["is_enabled"] = bool(check.get("is_enabled"))
    check["auto_close_when_resolved"] = bool(check.get("auto_close_when_resolved"))
    check["target"] = str(check.get("target") or "")
    # The ordered actions (app.services.rmm_action_steps); None keeps the older single action.
    raw_actions = check.pop("actions_json", None)
    actions = None if raw_actions is None else _decode_json(raw_actions, [])
    check["actions"] = actions if isinstance(actions, list) or actions is None else []
    if not with_secrets:
        check.pop("entries_encrypted", None)
        check.pop("check_entries_encrypted", None)
        if check["actions"]:
            check["actions"] = [{key: value for key, value in step.items() if key != "entries_encrypted"}
                                if isinstance(step, dict) else step for step in check["actions"]]
    return check


def _state_row(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    state = dict(row)
    for key in ("problem_since", "checked_at", "changed_at", "last_action_at", "next_run_at", "scheduled_at"):
        state[key] = _as_datetime(state.get(key))
    return state


async def get_check(check_id: int, *, with_secrets: bool = False) -> dict[str, Any] | None:
    row = await db.fetch_one(_SELECT + "WHERE c.id = %s", (check_id,))
    return _check_row(row, with_secrets=with_secrets)


async def list_checks_for_company(company_id: int) -> list[dict[str, Any]]:
    """Every-company checks, then the company's own and its devices' checks."""

    rows = await db.fetch_all(
        _SELECT + "WHERE c.company_id IS NULL OR c.company_id = %s "
        "ORDER BY CASE WHEN c.company_id IS NULL THEN 0 WHEN c.asset_id IS NULL THEN 1 ELSE 2 END, c.name, c.id",
        (company_id,),
    )
    return [_check_row(row) for row in rows or []]


async def list_checks_for_device(company_id: int | None, asset_id: int | None, *,
                                 with_secrets: bool = False) -> list[dict[str, Any]]:
    """Checks that reach one device, on or off: every-company, its company's and its own."""

    clauses = ["(c.company_id IS NULL AND c.asset_id IS NULL)"]
    params: list[Any] = []
    if company_id is not None:
        clauses.append("(c.company_id = %s AND c.asset_id IS NULL)")
        params.append(company_id)
    if asset_id is not None:
        clauses.append("c.asset_id = %s")
        params.append(asset_id)
    sql = _SELECT + "WHERE (" + " OR ".join(clauses) + ") ORDER BY c.id"  # nosec B608 - fixed clauses, bound values
    rows = await db.fetch_all(sql, tuple(params))
    return [_check_row(row, with_secrets=with_secrets) for row in rows or []]


async def list_checks_of_types(check_types: Iterable[str], *, with_secrets: bool = False) -> list[dict[str, Any]]:
    """Every check of these types, on or off, in every scope."""

    types = list(check_types)
    if not types:
        return []
    placeholders = ", ".join(["%s"] * len(types))
    # Only "%s" placeholders are interpolated; the types are bound parameters.
    rows = await db.fetch_all(
        _SELECT + f"WHERE c.check_type IN ({placeholders}) ORDER BY c.id",  # nosec B608
        tuple(types),
    )
    return [_check_row(row, with_secrets=with_secrets) for row in rows or []]


def _values(values: dict[str, Any]) -> tuple:
    return (
        values["name"], values["company_id"], values["asset_id"], values["check_type"], values["target"],
        json.dumps(values["settings"]), values["duration_minutes"], values["check_script_id"],
        values["check_entries_encrypted"], json.dumps(values["check_inputs"]), values["action"], values["script_id"],
        values["entries_encrypted"], json.dumps(values["inputs"]), values["timeout_seconds"],
        values["cooldown_minutes"], values.get("schedule_mode"), values.get("delay_minutes"), values.get("run_time"),
        values.get("schedule_timezone"), 1 if values["is_enabled"] else 0,
        1 if values.get("auto_close_when_resolved") else 0,
        json.dumps(values["target_rules"]) if values.get("target_rules") is not None else None,
        json.dumps(values["actions"]) if values.get("actions") is not None else None, values["user_id"],
    )


async def create_check(values: dict[str, Any]) -> int:
    now = _utcnow()
    return await db.execute_returning_lastrowid(
        "INSERT INTO rmm_checks (name, company_id, asset_id, check_type, target, settings_json, duration_minutes, "
        "check_script_id, check_entries_encrypted, check_inputs_json, action, script_id, entries_encrypted, "
        "inputs_json, timeout_seconds, cooldown_minutes, schedule_mode, delay_minutes, run_time, schedule_timezone, "
        "is_enabled, auto_close_when_resolved, target_rules_json, actions_json, created_by_user_id, updated_by_user_id, "
        "created_at, updated_at) VALUES (%s, %s, %s, %s, %s, %s, "
        "%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (*_values(values), values["user_id"], now, now),
    )


async def update_check(check_id: int, values: dict[str, Any]) -> None:
    await db.execute(
        "UPDATE rmm_checks SET name = %s, company_id = %s, asset_id = %s, check_type = %s, target = %s, "
        "settings_json = %s, duration_minutes = %s, check_script_id = %s, check_entries_encrypted = %s, "
        "check_inputs_json = %s, action = %s, script_id = %s, entries_encrypted = %s, inputs_json = %s, "
        "timeout_seconds = %s, cooldown_minutes = %s, schedule_mode = %s, delay_minutes = %s, run_time = %s, "
        "schedule_timezone = %s, is_enabled = %s, auto_close_when_resolved = %s, target_rules_json = %s, "
        "actions_json = %s, updated_by_user_id = %s, updated_at = %s WHERE id = %s",
        (*_values(values), _utcnow(), check_id),
    )
    # A changed check starts again on every device.
    await db.execute(
        "UPDATE rmm_check_states SET status = 'pending', value_text = NULL, problem_since = NULL, run_id = NULL, "
        "next_run_at = NULL, scheduled_at = NULL WHERE check_id = %s",
        (check_id,),
    )
    await db.execute("DELETE FROM rmm_check_action_schedules WHERE check_id = %s", (check_id,))


async def set_check_enabled(check_id: int, enabled: bool, user_id: int | None) -> None:
    await db.execute(
        "UPDATE rmm_checks SET is_enabled = %s, updated_by_user_id = %s, updated_at = %s WHERE id = %s",
        (1 if enabled else 0, user_id, _utcnow(), check_id),
    )


async def delete_check(check_id: int) -> None:
    await db.execute("DELETE FROM rmm_check_action_schedules WHERE check_id = %s", (check_id,))
    await db.execute("DELETE FROM rmm_check_states WHERE check_id = %s", (check_id,))
    await db.execute("DELETE FROM rmm_checks WHERE id = %s", (check_id,))


async def record_failure(check_id: int, summary: str) -> None:
    await db.execute(
        "UPDATE rmm_checks SET last_failed_at = %s, last_summary = %s WHERE id = %s",
        (_utcnow(), summary[:512], check_id),
    )


async def set_summary(check_id: int, summary: str) -> None:
    await db.execute("UPDATE rmm_checks SET last_summary = %s WHERE id = %s", (summary[:512], check_id))


# --------------------------------------------------------------------------- #
# Results per device
# --------------------------------------------------------------------------- #


def _earliest(*values: Any) -> datetime | None:
    times = [value for value in (_as_datetime(item) for item in values) if value is not None]
    return min(times) if times else None


async def _next_scheduled(agent_id: int, check_id: int | None = None) -> dict[int, datetime]:
    """When each check's next scheduled script runs on a device (one per action)."""

    sql = "SELECT check_id, MIN(run_at) AS run_at FROM rmm_check_action_schedules WHERE agent_id = %s"
    params: list[Any] = [agent_id]
    if check_id is not None:
        sql += " AND check_id = %s"
        params.append(check_id)
    rows = await db.fetch_all(sql + " GROUP BY check_id", tuple(params))
    return {int(row["check_id"]): _as_datetime(row["run_at"]) for row in rows or [] if row.get("run_at") is not None}


async def get_state(check_id: int, agent_id: int) -> dict[str, Any] | None:
    """A check's result on a device; ``scheduled_at`` is when its next scheduled script runs."""

    state = _state_row(await db.fetch_one(_STATE_SELECT + "WHERE check_id = %s AND agent_id = %s", (check_id, agent_id)))
    if state:
        state["scheduled_at"] = _earliest(state.get("scheduled_at"),
                                          (await _next_scheduled(agent_id, check_id)).get(check_id))
    return state


async def list_states_for_agent(agent_id: int, *, with_schedules: bool = False) -> dict[int, dict[str, Any]]:
    """Each check's result on a device; ``with_schedules`` fills in ``scheduled_at`` from every action."""

    rows = await db.fetch_all(_STATE_SELECT + "WHERE agent_id = %s", (agent_id,))
    states = {int(row["check_id"]): _state_row(row) for row in rows or []}
    if with_schedules and states:
        upcoming = await _next_scheduled(agent_id)
        for check_id, state in states.items():
            state["scheduled_at"] = _earliest(state.get("scheduled_at"), upcoming.get(check_id))
    return states


async def list_states_for_agents(agent_ids: Iterable[int]) -> dict[int, dict[int, dict[str, Any]]]:
    """Read background-check state in bounded batches instead of per device."""
    identifiers = sorted(set(int(agent_id) for agent_id in agent_ids))
    states: dict[int, dict[int, dict[str, Any]]] = {agent_id: {} for agent_id in identifiers}
    for start in range(0, len(identifiers), 400):
        batch = tuple(identifiers[start:start + 400])
        placeholders = ", ".join(["%s"] * len(batch))
        rows = await db.fetch_all(
            _STATE_SELECT + "WHERE agent_id IN (" + placeholders + ")", batch,
        )
        for row in rows or []:
            states[int(row["agent_id"])][int(row["check_id"])] = _state_row(row)
    return states


async def list_states_for_run(run_id: int) -> list[dict[str, Any]]:
    rows = await db.fetch_all(_STATE_SELECT + "WHERE run_id = %s", (run_id,))
    return [_state_row(row) for row in rows or []]


async def count_states(check_ids: Iterable[int], *, company_id: int | None = None) -> dict[int, dict[str, int]]:
    """How many devices each check is passing and failing on, optionally only the company's devices."""

    ids = [int(check_id) for check_id in check_ids]
    if not ids:
        return {}
    placeholders = ", ".join(["%s"] * len(ids))
    params: list[Any] = list(ids)
    company_clause = ""
    if company_id is not None:
        company_clause = " AND g.company_id = %s"
        params.append(company_id)
    # Only "%s" placeholders and the fixed company clause are interpolated; the values are bound parameters.
    rows = await db.fetch_all(
        "SELECT s.check_id, s.status, COUNT(*) AS total FROM rmm_check_states s "  # nosec B608
        f"JOIN rmm_agents g ON g.id = s.agent_id WHERE s.check_id IN ({placeholders}){company_clause} "
        "GROUP BY s.check_id, s.status",
        tuple(params),
    )
    counts: dict[int, dict[str, int]] = {}
    for row in rows or []:
        counts.setdefault(int(row["check_id"]), {})[str(row["status"])] = int(row["total"])
    return counts


async def list_check_devices(check_id: int, company_id: int, statuses: Iterable[str]) -> list[dict[str, Any]]:
    """The company's devices with one of these results for a check, with their latest values."""

    wanted = [str(value) for value in statuses]
    if not wanted:
        return []
    placeholders = ", ".join(["%s"] * len(wanted))
    # Only "%s" placeholders are interpolated; the statuses are bound parameters.
    rows = await db.fetch_all(
        "SELECT s.check_id, s.agent_id, s.status, s.value_text, s.problem_since, s.checked_at, s.changed_at, "  # nosec B608
        "s.last_action_at, s.ticket_id, s.run_id, s.next_run_at, s.scheduled_at, "
        "(SELECT MIN(x.run_at) FROM rmm_check_action_schedules x WHERE x.check_id = s.check_id "
        "AND x.agent_id = s.agent_id) AS action_scheduled_at, "
        "g.asset_id, g.hostname, a.name AS asset_name "
        "FROM rmm_check_states s JOIN rmm_agents g ON g.id = s.agent_id LEFT JOIN assets a ON a.id = g.asset_id "
        f"WHERE s.check_id = %s AND g.company_id = %s AND s.status IN ({placeholders}) "
        "ORDER BY s.status, COALESCE(a.name, g.hostname), s.agent_id",
        (check_id, company_id, *wanted),
    )
    states = []
    for row in rows or []:
        state = _state_row(row)
        state["scheduled_at"] = _earliest(state.get("scheduled_at"), state.pop("action_scheduled_at", None))
        states.append(state)
    return states


async def save_state(check_id: int, agent_id: int, state: dict[str, Any]) -> None:
    values = tuple(state.get(column) for column in _STATE_COLUMNS)
    existing = await db.fetch_one(
        "SELECT check_id FROM rmm_check_states WHERE check_id = %s AND agent_id = %s", (check_id, agent_id)
    )
    if existing:
        await db.execute(
            "UPDATE rmm_check_states SET status = %s, value_text = %s, problem_since = %s, checked_at = %s, "
            "changed_at = %s, last_action_at = %s, ticket_id = %s, run_id = %s, next_run_at = %s "
            "WHERE check_id = %s AND agent_id = %s",
            (*values, check_id, agent_id),
        )
        return
    await db.execute(
        "INSERT INTO rmm_check_states (status, value_text, problem_since, checked_at, changed_at, last_action_at, "
        "ticket_id, run_id, next_run_at, check_id, agent_id) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (*values, check_id, agent_id),
    )


async def schedule_step(check_id: int, agent_id: int, action_key: str, run_at: datetime) -> bool:
    """Schedule one of the check's scripts on a device unless that one is already waiting there."""

    existing = await db.fetch_one(
        "SELECT run_at FROM rmm_check_action_schedules WHERE check_id = %s AND agent_id = %s AND action_key = %s",
        (check_id, agent_id, action_key),
    )
    if existing:
        return False
    await db.execute(
        "INSERT INTO rmm_check_action_schedules (check_id, agent_id, action_key, run_at, created_at) "
        "VALUES (%s, %s, %s, %s, %s)",
        (check_id, agent_id, action_key, run_at, _utcnow()),
    )
    return True


async def clear_scheduled(check_id: int, agent_id: int) -> int:
    """Drop the scripts waiting to run on a device. Returns how many were waiting."""

    legacy = await db.execute_rowcount(
        "UPDATE rmm_check_states SET scheduled_at = NULL WHERE check_id = %s AND agent_id = %s "
        "AND scheduled_at IS NOT NULL",
        (check_id, agent_id),
    )
    listed = await db.execute_rowcount(
        "DELETE FROM rmm_check_action_schedules WHERE check_id = %s AND agent_id = %s", (check_id, agent_id)
    )
    return max(0, legacy) + max(0, listed)


async def due_scheduled(now: datetime, limit: int = 100) -> list[dict[str, Any]]:
    """Scheduled scripts due by ``now``, with the device's result for the check.

    ``action_key`` names the action; None is a run scheduled by an earlier
    release, which belongs to the check's first scheduled script."""

    legacy = await db.fetch_all(
        _STATE_SELECT + "WHERE scheduled_at IS NOT NULL AND scheduled_at <= %s ORDER BY scheduled_at LIMIT %s",
        (now, limit),
    )
    listed = await db.fetch_all(
        "SELECT x.check_id, x.agent_id, x.action_key, x.run_at, s.status, s.value_text "
        "FROM rmm_check_action_schedules x LEFT JOIN rmm_check_states s "
        "ON s.check_id = x.check_id AND s.agent_id = x.agent_id "
        "WHERE x.run_at <= %s ORDER BY x.run_at LIMIT %s",
        (now, limit),
    )
    due = [{**_state_row(row), "action_key": None, "run_at": _as_datetime(row.get("scheduled_at"))} for row in legacy or []]
    due += [{**dict(row), "run_at": _as_datetime(row.get("run_at"))} for row in listed or []]
    due.sort(key=lambda item: item["run_at"] or now)
    return due[:limit]


async def claim_scheduled(check_id: int, agent_id: int, now: datetime, action_key: str | None = None) -> bool:
    """Take a due scheduled script; only the instance that clears it runs it."""

    if action_key is None:
        return await db.execute_rowcount(
            "UPDATE rmm_check_states SET scheduled_at = NULL WHERE check_id = %s AND agent_id = %s "
            "AND scheduled_at IS NOT NULL AND scheduled_at <= %s",
            (check_id, agent_id, now),
        ) > 0
    return await db.execute_rowcount(
        "DELETE FROM rmm_check_action_schedules WHERE check_id = %s AND agent_id = %s AND action_key = %s "
        "AND run_at <= %s",
        (check_id, agent_id, action_key, now),
    ) > 0


async def list_active_agents() -> list[dict[str, Any]]:
    rows = await db.fetch_all(
        "SELECT id, company_id, asset_id, hostname, os, last_seen_utc, status FROM rmm_agents WHERE status = 'active'"
    )
    agents = []
    for row in rows or []:
        agent = dict(row)
        agent["last_seen_utc"] = _as_datetime(agent.get("last_seen_utc"))
        agents.append(agent)
    return agents
