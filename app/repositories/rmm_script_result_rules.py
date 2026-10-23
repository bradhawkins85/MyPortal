"""Storage for script result rules (:mod:`app.services.rmm_script_result_rules`)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from app.core.database import db

_SELECT = (
    "SELECT r.id, r.name, r.company_id, r.script_id, r.match_mode, r.conditions_json, r.action, r.next_script_id, "
    "r.entries_encrypted, r.inputs_json, r.timeout_seconds, r.pass_results, r.ticket_mode, r.ticket_subject, "
    "r.ticket_priority, r.is_enabled, r.match_count, r.last_matched_at, r.last_run_summary, r.created_by_user_id, "
    "r.updated_by_user_id, r.created_at, r.updated_at, s.name AS script_name, s.path AS script_path, "
    "n.name AS next_script_name, n.path AS next_script_path, n.is_active AS next_script_active, "
    "c.name AS company_name "
    "FROM rmm_script_result_rules r "
    "LEFT JOIN rmm_scripts s ON s.id = r.script_id "
    "LEFT JOIN rmm_scripts n ON n.id = r.next_script_id "
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
    rule["is_enabled"] = bool(rule.get("is_enabled"))
    rule["pass_results"] = bool(rule.get("pass_results"))
    if not with_secrets:
        rule.pop("entries_encrypted", None)
    return rule


async def get_rule(rule_id: int, *, with_secrets: bool = False) -> dict[str, Any] | None:
    row = await db.fetch_one(_SELECT + "WHERE r.id = %s", (rule_id,))
    return _rule_row(row, with_secrets=with_secrets)


async def list_rules_for_company(company_id: int, *, script_id: int | None = None) -> list[dict[str, Any]]:
    """Every-company rules, then the company's own; ``script_id`` keeps the
    rules that watch that script or any script."""

    sql = _SELECT + "WHERE (r.company_id IS NULL OR r.company_id = %s)"
    params: list[Any] = [company_id]
    if script_id is not None:
        sql += " AND (r.script_id IS NULL OR r.script_id = %s)"
        params.append(script_id)
    sql += " ORDER BY CASE WHEN r.company_id IS NULL THEN 0 ELSE 1 END, r.name, r.id"
    rows = await db.fetch_all(sql, tuple(params))
    return [_rule_row(row) for row in rows or []]


async def list_rules_for_run(company_id: int, script_id: int | None) -> list[dict[str, Any]]:
    """Enabled rules that watch a finished run of ``script_id`` in the company."""

    sql = _SELECT + "WHERE r.is_enabled = 1 AND (r.company_id IS NULL OR r.company_id = %s) AND "
    params: list[Any] = [company_id]
    if script_id is None:
        sql += "r.script_id IS NULL"
    else:
        sql += "(r.script_id IS NULL OR r.script_id = %s)"
        params.append(script_id)
    rows = await db.fetch_all(sql + " ORDER BY r.id", tuple(params))
    return [_rule_row(row, with_secrets=True) for row in rows or []]


def _values(values: dict[str, Any]) -> tuple[Any, ...]:
    return (
        values["name"], values["company_id"], values["script_id"], values["match_mode"],
        json.dumps(values["conditions"]), values["action"], values["next_script_id"], values["entries_encrypted"],
        json.dumps(values["inputs"]), values["timeout_seconds"], 1 if values["pass_results"] else 0,
        values["ticket_mode"], values["ticket_subject"], values["ticket_priority"], 1 if values["is_enabled"] else 0,
    )


async def create_rule(values: dict[str, Any]) -> int:
    now = _utcnow()
    return await db.execute_returning_lastrowid(
        "INSERT INTO rmm_script_result_rules (name, company_id, script_id, match_mode, conditions_json, action, "
        "next_script_id, entries_encrypted, inputs_json, timeout_seconds, pass_results, ticket_mode, ticket_subject, "
        "ticket_priority, is_enabled, created_by_user_id, updated_by_user_id, created_at, updated_at) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
        (*_values(values), values["user_id"], values["user_id"], now, now),
    )


async def update_rule(rule_id: int, values: dict[str, Any]) -> None:
    await db.execute(
        "UPDATE rmm_script_result_rules SET name = %s, company_id = %s, script_id = %s, match_mode = %s, "
        "conditions_json = %s, action = %s, next_script_id = %s, entries_encrypted = %s, inputs_json = %s, "
        "timeout_seconds = %s, pass_results = %s, ticket_mode = %s, ticket_subject = %s, ticket_priority = %s, "
        "is_enabled = %s, updated_by_user_id = %s, updated_at = %s WHERE id = %s",
        (*_values(values), values["user_id"], _utcnow(), rule_id),
    )


async def set_rule_enabled(rule_id: int, enabled: bool, user_id: int | None) -> None:
    await db.execute(
        "UPDATE rmm_script_result_rules SET is_enabled = %s, updated_by_user_id = %s, updated_at = %s WHERE id = %s",
        (1 if enabled else 0, user_id, _utcnow(), rule_id),
    )


async def delete_rule(rule_id: int) -> None:
    await db.execute("DELETE FROM rmm_script_result_rule_tickets WHERE rule_id = %s", (rule_id,))
    await db.execute("DELETE FROM rmm_script_result_rules WHERE id = %s", (rule_id,))


async def record_match(rule_id: int, summary: str) -> None:
    await db.execute(
        "UPDATE rmm_script_result_rules SET match_count = match_count + 1, last_matched_at = %s, "
        "last_run_summary = %s WHERE id = %s",
        (_utcnow(), summary[:512], rule_id),
    )


async def get_rule_ticket(rule_id: int, agent_id: int) -> int | None:
    """The ticket a create-ticket rule last raised for a device."""

    row = await db.fetch_one(
        "SELECT ticket_id FROM rmm_script_result_rule_tickets WHERE rule_id = %s AND agent_id = %s",
        (rule_id, agent_id),
    )
    return int(row["ticket_id"]) if row and row.get("ticket_id") is not None else None


async def set_rule_ticket(rule_id: int, agent_id: int, ticket_id: int) -> None:
    now = _utcnow()
    if await get_rule_ticket(rule_id, agent_id) is not None:
        await db.execute(
            "UPDATE rmm_script_result_rule_tickets SET ticket_id = %s, updated_at = %s "
            "WHERE rule_id = %s AND agent_id = %s",
            (ticket_id, now, rule_id, agent_id),
        )
        return
    await db.execute(
        "INSERT INTO rmm_script_result_rule_tickets (rule_id, agent_id, ticket_id, created_at, updated_at) "
        "VALUES (%s, %s, %s, %s, %s)",
        (rule_id, agent_id, ticket_id, now, now),
    )


async def chain_depth(run_id: int, *, limit: int) -> int:
    """How many result rule runs led to ``run_id`` (0 for a run started any
    other way), counting at most ``limit``."""

    depth = 0
    current: int | None = run_id
    while current is not None and depth < limit:
        row = await db.fetch_one("SELECT parent_run_id FROM rmm_script_runs WHERE id = %s", (current,))
        parent = row.get("parent_run_id") if row else None
        if parent is None:
            break
        depth += 1
        current = int(parent)
    return depth


async def set_run_origin(run_id: int, *, parent_run_id: int, rule_id: int) -> None:
    await db.execute(
        "UPDATE rmm_script_runs SET parent_run_id = %s, result_rule_id = %s WHERE id = %s",
        (parent_run_id, rule_id, run_id),
    )
