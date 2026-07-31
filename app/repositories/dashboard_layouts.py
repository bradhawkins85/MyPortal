"""Persistence for personal and company dashboard layouts."""

from __future__ import annotations

import json
from typing import Any

from app.core.database import db

PERSONAL_KEY = "dashboard:layout:v2"


async def get_personal(user_id: int) -> Any:
    from app.repositories import user_preferences

    return await user_preferences.get_preference(user_id, PERSONAL_KEY)


async def set_personal(user_id: int, layout: dict[str, Any]) -> None:
    from app.repositories import user_preferences

    await user_preferences.set_preference(user_id, PERSONAL_KEY, layout)


async def delete_personal(user_id: int) -> None:
    from app.repositories import user_preferences

    await user_preferences.delete_preference(user_id, PERSONAL_KEY)


async def get_company(company_id: int) -> Any:
    row = await db.fetch_one(
        "SELECT layout_json FROM company_dashboard_layouts WHERE company_id = %s",
        (int(company_id),),
    )
    if not row:
        return None
    value = row.get("layout_json")
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return None
    return value


async def set_company(company_id: int, layout: dict[str, Any], updated_by: int) -> None:
    payload = json.dumps(layout, separators=(",", ":"), ensure_ascii=False)
    if db.is_sqlite():
        sql = """INSERT INTO company_dashboard_layouts(company_id, layout_json, updated_by)
                 VALUES (?, ?, ?) ON CONFLICT(company_id) DO UPDATE SET
                 layout_json=excluded.layout_json, updated_by=excluded.updated_by,
                 updated_at=datetime('now')"""
    else:
        sql = """INSERT INTO company_dashboard_layouts(company_id, layout_json, updated_by)
                 VALUES (%s, %s, %s) ON DUPLICATE KEY UPDATE
                 layout_json=VALUES(layout_json), updated_by=VALUES(updated_by),
                 updated_at=CURRENT_TIMESTAMP"""
    await db.execute(sql, (int(company_id), payload, int(updated_by)))
