from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from app.core.database import db


async def list_rules(*, active_only: bool = False) -> list[dict[str, Any]]:
    """Return all rules ordered by priority descending (highest first), then by id."""
    if active_only:
        rows = await db.fetch_all(
            """SELECT r.*, u.first_name, u.last_name, u.email AS tech_email,
                      u.matrix_user_id AS tech_matrix_user_id
               FROM chat_auto_assign_rules r
               LEFT JOIN users u ON u.id = r.assigned_tech_user_id
               WHERE r.is_active = 1
               ORDER BY r.is_default ASC, r.priority DESC, r.id ASC""",
        )
    else:
        rows = await db.fetch_all(
            """SELECT r.*, u.first_name, u.last_name, u.email AS tech_email,
                      u.matrix_user_id AS tech_matrix_user_id
               FROM chat_auto_assign_rules r
               LEFT JOIN users u ON u.id = r.assigned_tech_user_id
               ORDER BY r.is_default ASC, r.priority DESC, r.id ASC""",
        )
    return [_decode_row(dict(r)) for r in rows]


async def get_rule(rule_id: int) -> dict[str, Any] | None:
    row = await db.fetch_one(
        """SELECT r.*, u.first_name, u.last_name, u.email AS tech_email,
                  u.matrix_user_id AS tech_matrix_user_id
           FROM chat_auto_assign_rules r
           LEFT JOIN users u ON u.id = r.assigned_tech_user_id
           WHERE r.id = %s""",
        (rule_id,),
    )
    return _decode_row(dict(row)) if row else None


async def create_rule(
    *,
    name: str,
    priority: int,
    is_default: bool,
    assigned_tech_user_id: int | None,
    conditions: list[dict[str, Any]],
    is_active: bool = True,
) -> dict[str, Any]:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    conditions_json = json.dumps(conditions)
    rule_id = await db.execute_returning_lastrowid(
        """INSERT INTO chat_auto_assign_rules
           (name, priority, is_default, assigned_tech_user_id, conditions, is_active, created_at, updated_at)
           VALUES (%s, %s, %s, %s, %s, %s, %s, %s)""",
        (
            name,
            priority,
            1 if is_default else 0,
            assigned_tech_user_id,
            conditions_json,
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
    assigned_tech_user_id: int | None,
    conditions: list[dict[str, Any]],
    is_active: bool,
) -> dict[str, Any]:
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    conditions_json = json.dumps(conditions)
    await db.execute(
        """UPDATE chat_auto_assign_rules
           SET name = %s, priority = %s, is_default = %s, assigned_tech_user_id = %s,
               conditions = %s, is_active = %s, updated_at = %s
           WHERE id = %s""",
        (
            name,
            priority,
            1 if is_default else 0,
            assigned_tech_user_id,
            conditions_json,
            1 if is_active else 0,
            now,
            rule_id,
        ),
    )
    rule = await get_rule(rule_id)
    return rule or {}


async def delete_rule(rule_id: int) -> None:
    await db.execute(
        "DELETE FROM chat_auto_assign_rules WHERE id = %s",
        (rule_id,),
    )


async def list_technicians_with_matrix_id() -> list[dict[str, Any]]:
    """Return staff users (super admin or helpdesk) who have a matrix_user_id set."""
    rows = await db.fetch_all(
        """SELECT id, email, first_name, last_name, matrix_user_id
           FROM users
           WHERE (is_super_admin = 1 OR is_helpdesk_technician = 1)
             AND matrix_user_id IS NOT NULL AND matrix_user_id != ''
           ORDER BY first_name, last_name, email""",
    )
    return [dict(r) for r in rows]


async def list_all_technicians() -> list[dict[str, Any]]:
    """Return all staff users (super admin or helpdesk)."""
    rows = await db.fetch_all(
        """SELECT id, email, first_name, last_name, matrix_user_id
           FROM users
           WHERE is_super_admin = 1 OR is_helpdesk_technician = 1
           ORDER BY first_name, last_name, email""",
    )
    return [dict(r) for r in rows]


def _decode_row(row: dict[str, Any]) -> dict[str, Any]:
    """Parse the JSON conditions field and normalize boolean columns."""
    raw = row.get("conditions")
    if isinstance(raw, str):
        try:
            row["conditions"] = json.loads(raw)
        except (ValueError, TypeError):
            row["conditions"] = []
    elif raw is None:
        row["conditions"] = []
    row["is_default"] = bool(row.get("is_default"))
    row["is_active"] = bool(row.get("is_active"))
    return row
