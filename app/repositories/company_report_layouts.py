"""Persistence for per-company row-based report layouts."""
from __future__ import annotations

import json
from typing import Any, Mapping

from app.core.database import db


async def get_layout(company_id: int) -> list[dict[str, Any]] | None:
    row = await db.fetch_one(
        "SELECT layout_json FROM company_report_layouts WHERE company_id = %s",
        (int(company_id),),
    )
    if not row:
        return None
    raw = row.get("layout_json") if isinstance(row, Mapping) else row["layout_json"]
    try:
        value = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, list) else None


async def save_layout(company_id: int, rows: list[dict[str, Any]]) -> None:
    payload = json.dumps(rows, separators=(",", ":"))
    await db.execute("DELETE FROM company_report_layouts WHERE company_id = %s", (int(company_id),))
    await db.execute(
        "INSERT INTO company_report_layouts (company_id, layout_json) VALUES (%s, %s)",
        (int(company_id), payload),
    )
