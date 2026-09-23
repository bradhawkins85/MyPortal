from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from app.core.database import db


def _utc_naive(value: datetime | None = None) -> datetime:
    value = value or datetime.now(timezone.utc)
    return value.astimezone(timezone.utc).replace(tzinfo=None) if value.tzinfo else value


def _normalise(row: dict[str, Any]) -> dict[str, Any]:
    result = dict(row)
    raw = result.get("conditions")
    if isinstance(raw, str):
        try:
            result["conditions"] = json.loads(raw)
        except json.JSONDecodeError:
            result["conditions"] = []
    result["enabled"] = bool(result.get("enabled"))
    return result


async def list_rules(*, enabled_only: bool = False) -> list[dict[str, Any]]:
    where = " WHERE enabled = 1" if enabled_only else ""
    rows = await db.fetch_all("SELECT * FROM webhook_deletion_rules" + where + " ORDER BY id")
    return [_normalise(row) for row in rows]


async def get_rule(rule_id: int) -> dict[str, Any] | None:
    row = await db.fetch_one("SELECT * FROM webhook_deletion_rules WHERE id = %s", (rule_id,))
    return _normalise(row) if row else None


async def create_rule(data: dict[str, Any]) -> dict[str, Any]:
    rule_id = await db.execute_returning_lastrowid(
        """INSERT INTO webhook_deletion_rules
        (name, execution_type, cron_expression, conditions, enabled, next_run_at)
        VALUES (%s, %s, %s, %s, %s, %s)""",
        (data["name"], data["execution_type"], data.get("cron_expression"),
         json.dumps(data["conditions"]), int(data.get("enabled", True)), data.get("next_run_at")),
    )
    return await get_rule(int(rule_id)) or {}


async def update_rule(rule_id: int, data: dict[str, Any]) -> dict[str, Any] | None:
    await db.execute(
        """UPDATE webhook_deletion_rules SET name=%s, execution_type=%s,
        cron_expression=%s, conditions=%s, enabled=%s, next_run_at=%s WHERE id=%s""",
        (data["name"], data["execution_type"], data.get("cron_expression"),
         json.dumps(data["conditions"]), int(data.get("enabled", True)), data.get("next_run_at"), rule_id),
    )
    return await get_rule(rule_id)


async def delete_rule(rule_id: int) -> None:
    await db.execute("DELETE FROM webhook_deletion_rules WHERE id = %s", (rule_id,))


async def mark_run(rule_id: int, *, next_run_at: datetime | None) -> None:
    await db.execute(
        "UPDATE webhook_deletion_rules SET last_run_at=%s, next_run_at=%s WHERE id=%s",
        (_utc_naive(), _utc_naive(next_run_at) if next_run_at else None, rule_id),
    )


async def get_retention() -> dict[str, Any]:
    row = await db.fetch_one("SELECT * FROM webhook_retention_settings WHERE id = %s", (1,))
    return dict(row) if row else {"id": 1, "enabled": False, "retention_days": 30}


async def set_retention(*, enabled: bool, retention_days: int) -> dict[str, Any]:
    if await db.fetch_one("SELECT id FROM webhook_retention_settings WHERE id = %s", (1,)):
        await db.execute(
            "UPDATE webhook_retention_settings SET enabled=%s, retention_days=%s WHERE id=%s",
            (int(enabled), retention_days, 1),
        )
    else:
        await db.execute(
            "INSERT INTO webhook_retention_settings (id, enabled, retention_days) VALUES (%s, %s, %s)",
            (1, int(enabled), retention_days),
        )
    return await get_retention()
