from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from app.core.database import db
from app.repositories import company_memberships as membership_repo

_HELPDESK_PERMISSION_KEY = "helpdesk.technician"


_LIST_RULES_SELECT = """
    SELECT r.*, u.first_name, u.last_name, u.email AS tech_email,
                  u.matrix_user_id AS tech_matrix_user_id
           FROM chat_auto_assign_rules r
           LEFT JOIN users u ON u.id = r.assigned_tech_user_id
"""


async def list_rules(*, active_only: bool = False) -> list[dict[str, Any]]:
    """Return all rules ordered by priority descending (highest first), then by id."""
    where = "WHERE r.is_active = 1 " if active_only else ""
    rows = await db.fetch_all(
        _LIST_RULES_SELECT + where
        + "ORDER BY r.is_default ASC, r.priority DESC, r.id ASC",
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
    return await _list_technicians(require_matrix_id=True)


async def list_all_technicians() -> list[dict[str, Any]]:
    """Return all staff users (super admin or helpdesk)."""
    return await _list_technicians(require_matrix_id=False)


async def _list_technicians(*, require_matrix_id: bool) -> list[dict[str, Any]]:
    query = """
        SELECT id, email, first_name, last_name, matrix_user_id, is_super_admin
        FROM users
    """
    if require_matrix_id:
        query += "WHERE matrix_user_id IS NOT NULL AND matrix_user_id != ''"
    rows = await db.fetch_all(
        query,
    )
    users = [dict(row) for row in rows]

    users_with_permission = await membership_repo.list_users_with_permission(_HELPDESK_PERMISSION_KEY)
    permission_user_ids = set()
    for user in users_with_permission:
        try:
            permission_user_ids.add(int(user.get("id")))
        except (TypeError, ValueError):
            continue

    technicians: list[dict[str, Any]] = []
    for user in users:
        try:
            user_id = int(user.get("id"))
        except (TypeError, ValueError):
            continue
        if bool(user.get("is_super_admin")):
            technicians.append(user)
            continue
        if user_id in permission_user_ids:
            technicians.append(user)
            continue
        if await membership_repo.user_has_permission(user_id, _HELPDESK_PERMISSION_KEY):
            technicians.append(user)

    def _sort_key(record: dict[str, Any]) -> tuple[str, str, str]:
        first_name = (record.get("first_name") or "").lower()
        last_name = (record.get("last_name") or "").lower()
        email = (record.get("email") or "").lower()
        return (first_name, last_name, email)

    return sorted(technicians, key=_sort_key)


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
