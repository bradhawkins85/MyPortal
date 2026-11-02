"""Storage for the latest Office 365 group cleanup scan per company."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from app.core.database import db


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def get_scan(company_id: int) -> dict[str, Any] | None:
    row = await db.fetch_one(
        "SELECT company_id, scanned_at, result, updated_at FROM m365_group_cleanup_scans WHERE company_id = %s",
        (company_id,),
    )
    if not row:
        return None
    item = dict(row)
    raw = item.get("result")
    try:
        item["result"] = json.loads(raw) if isinstance(raw, str) and raw else {}
    except json.JSONDecodeError:
        item["result"] = {}
    return item


async def save_scan(company_id: int, result: dict[str, Any], *, scanned_at: datetime | None = None) -> None:
    """Insert or replace the cached scan. ``scanned_at`` is kept when omitted."""
    now = _now()
    payload = json.dumps(result)
    if scanned_at is None:
        updated = await db.execute_rowcount(
            "UPDATE m365_group_cleanup_scans SET result = %s, updated_at = %s WHERE company_id = %s",
            (payload, now, company_id),
        )
    else:
        updated = await db.execute_rowcount(
            "UPDATE m365_group_cleanup_scans SET result = %s, scanned_at = %s, updated_at = %s WHERE company_id = %s",
            (payload, scanned_at, now, company_id),
        )
    if not updated:
        await db.execute(
            "INSERT INTO m365_group_cleanup_scans (company_id, scanned_at, result, updated_at) VALUES (%s, %s, %s, %s)",
            (company_id, scanned_at or now, payload, now),
        )
