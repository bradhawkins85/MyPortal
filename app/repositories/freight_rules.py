from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from app.core.database import db


async def list_rules(*, active_only: bool = False) -> list[dict[str, Any]]:
    where = "WHERE is_active = 1 " if active_only else ""
    rows = await db.fetch_all(
        "SELECT * FROM shop_freight_rules "
        + where
        + "ORDER BY is_default ASC, priority DESC, id ASC",
    )
    return [_decode_row(dict(row)) for row in rows]


async def get_rule(rule_id: int) -> dict[str, Any] | None:
    row = await db.fetch_one(
        "SELECT * FROM shop_freight_rules WHERE id = %s",
        (rule_id,),
    )
    return _decode_row(dict(row)) if row else None


async def create_rule(
    *,
    name: str,
    priority: int,
    is_default: bool,
    stop_processing: bool,
    freight_amount: Decimal,
    conditions: list[dict[str, Any]],
    is_active: bool,
) -> dict[str, Any]:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    rule_id = await db.execute_returning_lastrowid(
        """
        INSERT INTO shop_freight_rules
            (name, priority, is_default, stop_processing, freight_amount, conditions, is_active, created_at, updated_at)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            name,
            priority,
            1 if is_default else 0,
            1 if stop_processing else 0,
            freight_amount,
            json.dumps(conditions),
            1 if is_active else 0,
            now,
            now,
        ),
    )
    rule = await get_rule(rule_id)
    return rule or {}


async def update_rule(
    rule_id: int,
    *,
    name: str,
    priority: int,
    is_default: bool,
    stop_processing: bool,
    freight_amount: Decimal,
    conditions: list[dict[str, Any]],
    is_active: bool,
) -> dict[str, Any]:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    await db.execute(
        """
        UPDATE shop_freight_rules
        SET name = %s,
            priority = %s,
            is_default = %s,
            stop_processing = %s,
            freight_amount = %s,
            conditions = %s,
            is_active = %s,
            updated_at = %s
        WHERE id = %s
        """,
        (
            name,
            priority,
            1 if is_default else 0,
            1 if stop_processing else 0,
            freight_amount,
            json.dumps(conditions),
            1 if is_active else 0,
            now,
            rule_id,
        ),
    )
    rule = await get_rule(rule_id)
    return rule or {}


async def delete_rule(rule_id: int) -> None:
    await db.execute(
        "DELETE FROM shop_freight_rules WHERE id = %s",
        (rule_id,),
    )


def _decode_row(row: dict[str, Any]) -> dict[str, Any]:
    raw_conditions = row.get("conditions")
    if isinstance(raw_conditions, str):
        try:
            row["conditions"] = json.loads(raw_conditions)
        except (ValueError, TypeError):
            row["conditions"] = []
    elif raw_conditions is None:
        row["conditions"] = []

    raw_amount = row.get("freight_amount")
    row["freight_amount"] = (
        raw_amount if isinstance(raw_amount, Decimal) else Decimal(str(raw_amount or 0))
    )

    row["is_default"] = bool(row.get("is_default"))
    row["stop_processing"] = bool(row.get("stop_processing"))
    row["is_active"] = bool(row.get("is_active"))
    return row
