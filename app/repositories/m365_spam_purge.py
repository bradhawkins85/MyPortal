"""Persistence for Microsoft 365 compliance search and purge requests."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from app.core.database import db


JSON_FIELDS = ("search_details", "purge_details")


def _normalise(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    result = dict(row)
    for key in JSON_FIELDS:
        value = result.get(key)
        if isinstance(value, str):
            try:
                result[key] = json.loads(value)
            except ValueError:
                result[key] = {"raw": value}
    return result


async def create_request(data: dict[str, Any]) -> dict[str, Any]:
    request_id = await db.execute_returning_lastrowid(
        """
        INSERT INTO m365_spam_purge_requests (
            company_id, created_by, search_name, action_name,
            content_match_query, sender, subject, received_from, received_to
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            data["company_id"], data["created_by"], data["search_name"],
            data["action_name"], data["content_match_query"], data.get("sender"),
            data.get("subject"), data.get("received_from"), data.get("received_to"),
        ),
    )
    result = await get_request(request_id)
    if not result:
        raise RuntimeError("Failed to persist spam purge request")
    return result


async def get_request(request_id: int) -> dict[str, Any] | None:
    return _normalise(await db.fetch_one(
        "SELECT * FROM m365_spam_purge_requests WHERE id = %s", (request_id,)
    ))


async def list_requests(company_id: int, *, limit: int = 200) -> list[dict[str, Any]]:
    rows = await db.fetch_all(
        """
        SELECT r.*, u.email AS requested_by_email
        FROM m365_spam_purge_requests r
        LEFT JOIN users u ON u.id = r.created_by
        WHERE r.company_id = %s
        ORDER BY r.created_at DESC
        LIMIT %s
        """,
        (company_id, max(1, min(limit, 500))),
    )
    return [_normalise(row) or {} for row in rows]


async def update_request(request_id: int, updates: dict[str, Any]) -> dict[str, Any] | None:
    allowed = {
        "search_status", "purge_status", "matched_items", "matched_size",
        "removed_items", "search_details", "purge_details", "error_message",
        "search_started_at", "search_completed_at", "purge_started_at",
        "purge_completed_at",
    }
    values: list[Any] = []
    assignments: list[str] = []
    for key, value in updates.items():
        if key not in allowed:
            continue
        if key in JSON_FIELDS and value is not None:
            value = json.dumps(value, ensure_ascii=True, default=str)
        assignments.append(key + " = %s")
        values.append(value)
    if not assignments:
        return await get_request(request_id)
    values.append(request_id)
    await db.execute(
        "UPDATE m365_spam_purge_requests SET " + ", ".join(assignments) + " WHERE id = %s",
        tuple(values),
    )
    return await get_request(request_id)


async def delete_request(request_id: int, company_id: int) -> bool:
    count = await db.execute_rowcount(
        """
        DELETE FROM m365_spam_purge_requests
        WHERE id = %s AND company_id = %s
          AND search_status IN ('draft', 'failed')
          AND purge_status = 'not_started'
        """,
        (request_id, company_id),
    )
    return count > 0
