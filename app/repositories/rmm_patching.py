"""Patch policies. Execution and output use the existing RMM run history."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from app.core.database import db


def decode(row: dict[str, Any]) -> dict[str, Any]:
    result = dict(row)
    for column, key, default in (("settings_json", "settings", {}), ("target_rules_json", "target_rules", None)):
        raw = result.pop(column, None)
        try:
            result[key] = json.loads(raw) if isinstance(raw, str) else raw
        except (ValueError, TypeError, RecursionError):
            result[key] = {}  # invalid rules never become unrestricted
        if raw is None:
            result[key] = default
    result["is_enabled"] = bool(result.get("is_enabled"))
    return result


async def list_policies(company_id: int | None = None) -> list[dict[str, Any]]:
    if company_id is None:
        rows = await db.fetch_all("SELECT * FROM rmm_patch_policies ORDER BY id")
    else:
        rows = await db.fetch_all(
            "SELECT * FROM rmm_patch_policies WHERE company_id IS NULL OR company_id = %s ORDER BY id",
            (company_id,))
    return [decode(row) for row in rows or []]


async def get_policy(policy_id: int) -> dict[str, Any] | None:
    row = await db.fetch_one("SELECT * FROM rmm_patch_policies WHERE id = %s", (policy_id,))
    return decode(row) if row else None


async def save(policy_id: int | None, values: dict[str, Any]) -> int:
    columns = ("name", "company_id", "kind", "package_id", "settings_json", "target_rules_json", "cron",
               "timezone", "is_enabled", "next_run_at", "updated_by_user_id")
    # The literal SQL below must keep the same column order as ``columns``.
    data = {**values, "settings_json": json.dumps(values["settings"]),
            "target_rules_json": json.dumps(values["target_rules"]) if values["target_rules"] is not None else None}
    params = tuple(data[column] for column in columns)
    if policy_id is not None:
        await db.execute(
            "UPDATE rmm_patch_policies SET name = %s, company_id = %s, kind = %s, package_id = %s, "
            "settings_json = %s, target_rules_json = %s, cron = %s, timezone = %s, is_enabled = %s, "
            "next_run_at = %s, updated_by_user_id = %s, revision = revision + 1, updated_at = %s "
            "WHERE id = %s",
            (*params, datetime.now(timezone.utc).replace(tzinfo=None), policy_id))
        return policy_id
    return await db.execute_returning_lastrowid(
        "INSERT INTO rmm_patch_policies (name, company_id, kind, package_id, settings_json, "
        "target_rules_json, cron, timezone, is_enabled, next_run_at, updated_by_user_id) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)", params)


async def delete(policy_id: int) -> None:
    await db.execute("DELETE FROM rmm_patch_policies WHERE id = %s", (policy_id,))


async def claim(policy: dict[str, Any], next_at: Any) -> bool:
    return await db.execute_rowcount(
        "UPDATE rmm_patch_policies SET next_run_at = %s WHERE id = %s AND revision = %s "
        "AND is_enabled = 1 AND next_run_at = %s", (next_at, policy["id"], policy["revision"], policy["next_run_at"])) > 0


async def has_active_run(agent_id: int) -> bool:
    return bool(await db.fetch_one("SELECT id FROM rmm_script_runs WHERE agent_id = %s AND run_source = 'patch' "
                                   "AND status IN ('queued', 'dispatched', 'running') LIMIT 1", (agent_id,)))
