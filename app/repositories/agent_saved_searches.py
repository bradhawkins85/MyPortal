from __future__ import annotations

import json
from typing import Any, Mapping

from app.core.database import db


def _normalise_source_filters(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return []
    if not isinstance(value, (list, tuple)):
        return []
    cleaned: list[str] = []
    for item in value:
        text = str(item or "").strip().lower()
        if text and text not in cleaned:
            cleaned.append(text)
    return cleaned


def _row_to_item(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "id": int(row.get("id") or 0),
        "name": str(row.get("name") or "").strip(),
        "query": str(row.get("query_text") or "").strip(),
        "source_filters": _normalise_source_filters(row.get("source_filters")),
        "is_shared": bool(row.get("is_shared")),
        "created_by_user_id": (
            int(row["user_id"]) if row.get("user_id") is not None else None
        ),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


async def list_for_user(user_id: int) -> list[dict[str, Any]]:
    rows = await db.fetch_all(
        """
        SELECT id, user_id, name, query_text, source_filters, is_shared, created_at, updated_at
        FROM agent_saved_searches
        WHERE user_id = ? OR is_shared = 1
        ORDER BY is_shared DESC, updated_at DESC, id DESC
        """,
        (user_id,),
    )
    return [_row_to_item(row) for row in rows or []]


async def create_or_update(
    *,
    user_id: int,
    name: str,
    query_text: str,
    source_filters: list[str],
    is_shared: bool,
) -> dict[str, Any]:
    payload = json.dumps(_normalise_source_filters(source_filters), ensure_ascii=False)
    trimmed_name = str(name or "").strip()
    trimmed_query = str(query_text or "").strip()
    existing = await db.fetch_one(
        """
        SELECT id
        FROM agent_saved_searches
        WHERE user_id = ? AND name = ?
        LIMIT 1
        """,
        (user_id, trimmed_name),
    )
    if existing:
        await db.execute(
            """
            UPDATE agent_saved_searches
            SET query_text = ?,
                source_filters = ?,
                is_shared = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                trimmed_query,
                payload,
                1 if is_shared else 0,
                int(existing["id"]),
            ),
        )
        item_id = int(existing["id"])
    else:
        item_id = await db.execute_insert(
            """
            INSERT INTO agent_saved_searches (user_id, name, query_text, source_filters, is_shared)
            VALUES (?, ?, ?, ?, ?)
            """,
            (user_id, trimmed_name, trimmed_query, payload, 1 if is_shared else 0),
        )
    row = await db.fetch_one(
        """
        SELECT id, user_id, name, query_text, source_filters, is_shared, created_at, updated_at
        FROM agent_saved_searches
        WHERE id = ?
        """,
        (item_id,),
    )
    if not row:
        return {
            "id": int(item_id),
            "name": trimmed_name,
            "query": trimmed_query,
            "source_filters": _normalise_source_filters(source_filters),
            "is_shared": bool(is_shared),
            "created_by_user_id": user_id,
            "created_at": None,
            "updated_at": None,
        }
    return _row_to_item(row)


async def delete_for_user(*, saved_search_id: int, user_id: int, allow_shared: bool) -> bool:
    if allow_shared:
        deleted = await db.execute(
            """
            DELETE FROM agent_saved_searches
            WHERE id = ? AND (user_id = ? OR (is_shared = 1 AND user_id = ?))
            """,
            (saved_search_id, user_id, user_id),
        )
    else:
        deleted = await db.execute(
            """
            DELETE FROM agent_saved_searches
            WHERE id = ? AND user_id = ?
            """,
            (saved_search_id, user_id),
        )
    try:
        return int(deleted or 0) > 0
    except (TypeError, ValueError):
        return bool(deleted)
