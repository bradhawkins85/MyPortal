"""Storage for event log rules (:mod:`app.services.rmm_event_log_rules`)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from app.core.database import db

_SELECT = (
    "SELECT r.id, r.name, r.company_id, r.asset_id, r.action, r.target_rules_json, r.conditions_json, r.script_id, "
    "r.entries_encrypted, r.inputs_json, r.timeout_seconds, r.cooldown_minutes, r.is_enabled, r.match_count, "
    "r.last_matched_at, r.last_run_summary, r.created_by_user_id, r.updated_by_user_id, r.created_at, "
    "r.updated_at, r.schedule_mode, r.delay_minutes, r.run_time, r.schedule_timezone, s.name AS script_name, s.path AS script_path, s.is_active AS script_active, "
    "a.name AS asset_name, c.name AS company_name "
    "FROM rmm_event_log_rules r "
    "LEFT JOIN rmm_scripts s ON s.id = r.script_id "
    "LEFT JOIN assets a ON a.id = r.asset_id "
    "LEFT JOIN companies c ON c.id = r.company_id "
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


def _rule_row(row: dict[str, Any] | None, *, with_secrets: bool = False) -> dict[str, Any] | None:
    if not row:
        return None
    rule = dict(row)
    rule["conditions"] = _decode_json(rule.pop("conditions_json", None), [])
    rule["inputs"] = _decode_json(rule.pop("inputs_json", None), [])
    raw_rules = rule.pop("target_rules_json", None)
    rule["target_rules"] = None if raw_rules is None else (_decode_json(raw_rules, {}) or {})
    rule["is_enabled"] = bool(rule.get("is_enabled"))
    if not with_secrets:
        rule.pop("entries_encrypted", None)
    return rule


async def get_rule(rule_id: int, *, with_secrets: bool = False) -> dict[str, Any] | None:
    row = await db.fetch_one(_SELECT + "WHERE r.id = %s", (rule_id,))
    return _rule_row(row, with_secrets=with_secrets)


async def list_rules_for_company(company_id: int) -> list[dict[str, Any]]:
    """Every-company rules, then the company's own and its devices' rules."""

    rows = await db.fetch_all(
        _SELECT + "WHERE r.company_id IS NULL OR r.company_id = %s "
        "ORDER BY CASE WHEN r.company_id IS NULL THEN 0 WHEN r.asset_id IS NULL THEN 1 ELSE 2 END, r.name, r.id",
        (company_id,),
    )
    return [_rule_row(row) for row in rows or []]


async def list_rules_for_device(company_id: int | None, asset_id: int | None, *, enabled_only: bool = False,
                                with_secrets: bool = False) -> list[dict[str, Any]]:
    """Rules that apply to one device: every-company, its company's and its own."""

    clauses = ["(r.company_id IS NULL AND r.asset_id IS NULL)"]
    params: list[Any] = []
    if company_id is not None:
        clauses.append("(r.company_id = %s AND r.asset_id IS NULL)")
        params.append(company_id)
    if asset_id is not None:
        clauses.append("r.asset_id = %s")
        params.append(asset_id)
    sql = _SELECT + "WHERE (" + " OR ".join(clauses) + ")"  # nosec B608 - fixed clauses, bound values
    if enabled_only:
        sql += " AND r.is_enabled = 1"
    sql += " ORDER BY r.id"
    rows = await db.fetch_all(sql, tuple(params))
    return [_rule_row(row, with_secrets=with_secrets) for row in rows or []]


def _schedule_values(values: dict[str, Any]) -> tuple[Any, Any, Any, Any]:
    return (values.get("schedule_mode"), values.get("delay_minutes"), values.get("run_time"),
            values.get("schedule_timezone"))


async def create_rule(values: dict[str, Any]) -> int:
    now = _utcnow()
    return await db.execute_returning_lastrowid(
        "INSERT INTO rmm_event_log_rules (name, company_id, asset_id, action, target_rules_json, conditions_json, script_id, "
        "entries_encrypted, inputs_json, timeout_seconds, cooldown_minutes, is_enabled, schedule_mode, delay_minutes, "
        "run_time, schedule_timezone, created_by_user_id, updated_by_user_id, created_at, updated_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (
            values["name"], values["company_id"], values["asset_id"], values["action"],
            json.dumps(values["target_rules"]) if values.get("target_rules") is not None else None,
            json.dumps(values["conditions"]), values["script_id"], values["entries_encrypted"],
            json.dumps(values["inputs"]), values["timeout_seconds"], values["cooldown_minutes"],
            1 if values["is_enabled"] else 0, *_schedule_values(values), values["user_id"], values["user_id"], now, now,
        ),
    )


async def update_rule(rule_id: int, values: dict[str, Any]) -> None:
    await db.execute(
        "UPDATE rmm_event_log_rules SET name = %s, company_id = %s, asset_id = %s, action = %s, "
        "target_rules_json = %s, conditions_json = %s, script_id = %s, entries_encrypted = %s, inputs_json = %s, timeout_seconds = %s, "
        "cooldown_minutes = %s, is_enabled = %s, schedule_mode = %s, delay_minutes = %s, run_time = %s, "
        "schedule_timezone = %s, updated_by_user_id = %s, updated_at = %s WHERE id = %s",
        (
            values["name"], values["company_id"], values["asset_id"], values["action"],
            json.dumps(values["target_rules"]) if values.get("target_rules") is not None else None,
            json.dumps(values["conditions"]), values["script_id"], values["entries_encrypted"],
            json.dumps(values["inputs"]), values["timeout_seconds"], values["cooldown_minutes"],
            1 if values["is_enabled"] else 0, *_schedule_values(values), values["user_id"], _utcnow(), rule_id,
        ),
    )


async def set_rule_enabled(rule_id: int, enabled: bool, user_id: int | None) -> None:
    await db.execute(
        "UPDATE rmm_event_log_rules SET is_enabled = %s, updated_by_user_id = %s, updated_at = %s WHERE id = %s",
        (1 if enabled else 0, user_id, _utcnow(), rule_id),
    )


async def delete_rule(rule_id: int) -> None:
    await db.execute("DELETE FROM rmm_event_log_rule_triggers WHERE rule_id = %s", (rule_id,))
    await db.execute("DELETE FROM rmm_event_log_rule_tickets WHERE rule_id = %s", (rule_id,))
    await db.execute("DELETE FROM rmm_event_log_rule_pending WHERE rule_id = %s", (rule_id,))
    await db.execute("DELETE FROM rmm_event_log_rules WHERE id = %s", (rule_id,))


async def record_matches(rule_id: int, count: int, *, summary: str | None = None) -> None:
    now = _utcnow()
    if summary is None:
        await db.execute(
            "UPDATE rmm_event_log_rules SET match_count = match_count + %s, last_matched_at = %s WHERE id = %s",
            (count, now, rule_id),
        )
        return
    await db.execute(
        "UPDATE rmm_event_log_rules SET match_count = match_count + %s, last_matched_at = %s, "
        "last_run_summary = %s WHERE id = %s",
        (count, now, summary[:512], rule_id),
    )


async def last_triggered(rule_id: int, agent_id: int) -> datetime | None:
    row = await db.fetch_one(
        "SELECT last_triggered_at FROM rmm_event_log_rule_triggers WHERE rule_id = %s AND agent_id = %s",
        (rule_id, agent_id),
    )
    value = row.get("last_triggered_at") if row else None
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return None
    return value


async def record_trigger(rule_id: int, agent_id: int, run_id: int | None) -> None:
    now = _utcnow()
    existing = await db.fetch_one(
        "SELECT rule_id FROM rmm_event_log_rule_triggers WHERE rule_id = %s AND agent_id = %s", (rule_id, agent_id)
    )
    if existing:
        await db.execute(
            "UPDATE rmm_event_log_rule_triggers SET last_run_id = %s, last_triggered_at = %s "
            "WHERE rule_id = %s AND agent_id = %s",
            (run_id, now, rule_id, agent_id),
        )
        return
    await db.execute(
        "INSERT INTO rmm_event_log_rule_triggers (rule_id, agent_id, last_run_id, last_triggered_at) "
        "VALUES (%s, %s, %s, %s)",
        (rule_id, agent_id, run_id, now),
    )


async def get_rule_ticket(rule_id: int, agent_id: int) -> int | None:
    """The ticket a create-ticket rule last raised for a device."""

    row = await db.fetch_one(
        "SELECT ticket_id FROM rmm_event_log_rule_tickets WHERE rule_id = %s AND agent_id = %s", (rule_id, agent_id)
    )
    return int(row["ticket_id"]) if row and row.get("ticket_id") is not None else None


async def set_rule_ticket(rule_id: int, agent_id: int, ticket_id: int) -> None:
    now = _utcnow()
    existing = await get_rule_ticket(rule_id, agent_id)
    if existing is not None:
        await db.execute(
            "UPDATE rmm_event_log_rule_tickets SET ticket_id = %s, updated_at = %s WHERE rule_id = %s AND agent_id = %s",
            (ticket_id, now, rule_id, agent_id),
        )
        return
    await db.execute(
        "INSERT INTO rmm_event_log_rule_tickets (rule_id, agent_id, ticket_id, created_at, updated_at) "
        "VALUES (%s, %s, %s, %s, %s)",
        (rule_id, agent_id, ticket_id, now, now),
    )


async def get_pending(rule_id: int, agent_id: int) -> dict[str, Any] | None:
    return await db.fetch_one(
        "SELECT rule_id, agent_id, run_at, event_encrypted, created_at FROM rmm_event_log_rule_pending "
        "WHERE rule_id = %s AND agent_id = %s",
        (rule_id, agent_id),
    )


async def add_pending(rule_id: int, agent_id: int, run_at: datetime, event_encrypted: str | None) -> None:
    await db.execute(
        "INSERT INTO rmm_event_log_rule_pending (rule_id, agent_id, run_at, event_encrypted, created_at) "
        "VALUES (%s, %s, %s, %s, %s)",
        (rule_id, agent_id, run_at, event_encrypted, _utcnow()),
    )


async def due_pending(now: datetime, limit: int = 100) -> list[dict[str, Any]]:
    rows = await db.fetch_all(
        "SELECT rule_id, agent_id, run_at, event_encrypted FROM rmm_event_log_rule_pending "
        "WHERE run_at <= %s ORDER BY run_at LIMIT %s",
        (now, int(limit)),
    )
    return [dict(row) for row in rows or []]


async def claim_pending(rule_id: int, agent_id: int) -> bool:
    """Remove a pending run; only the instance that removes it runs it."""

    count = await db.execute_rowcount(
        "DELETE FROM rmm_event_log_rule_pending WHERE rule_id = %s AND agent_id = %s", (rule_id, agent_id)
    )
    return count > 0


async def list_pending_for_rules(rule_ids: list[int]) -> dict[int, list[dict[str, Any]]]:
    if not rule_ids:
        return {}
    placeholders = ", ".join(["%s"] * len(rule_ids))
    # Only "%s" placeholders are interpolated; the ids are bound parameters.
    rows = await db.fetch_all(
        f"SELECT rule_id, agent_id, run_at FROM rmm_event_log_rule_pending WHERE rule_id IN ({placeholders}) "  # nosec B608
        "ORDER BY run_at",
        tuple(int(rule_id) for rule_id in rule_ids),
    )
    pending: dict[int, list[dict[str, Any]]] = {}
    for row in rows or []:
        pending.setdefault(int(row["rule_id"]), []).append(dict(row))
    return pending


async def set_summary(rule_id: int, summary: str) -> None:
    await db.execute(
        "UPDATE rmm_event_log_rules SET last_run_summary = %s WHERE id = %s", (summary[:512], rule_id)
    )
