from __future__ import annotations

import json
from typing import Any

from app.core.database import db


def _tags(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    try:
        decoded = json.loads(value or "[]")
    except (TypeError, json.JSONDecodeError):
        return []
    return [str(item) for item in decoded if str(item).strip()] if isinstance(decoded, list) else []


async def list_entries(*, include_ignored: bool) -> list[dict[str, Any]]:
    ignored_clause = "" if include_ignored else "AND COALESCE(r.ignored, 0) = 0"
    rows = await db.fetch_all(
        """
        SELECT t.id AS ticket_id, t.subject, t.ai_tags, t.resolution_steps,
               c.name AS company, COALESCE(r.ignored, 0) AS ignored, r.article_id
        FROM tickets t
        LEFT JOIN companies c ON c.id = t.company_id
        LEFT JOIN knowledge_base_resolution_reviews r ON r.ticket_id = t.id
        WHERE t.resolution_steps IS NOT NULL AND TRIM(t.resolution_steps) <> ''
        """ + ignored_clause + " ORDER BY t.resolution_steps_updated_at DESC, t.id DESC",
    )
    entries = []
    for row in rows:
        item = dict(row)
        item["ticket_id"] = int(item["ticket_id"])
        item["ignored"] = bool(item.get("ignored"))
        item["ai_tags"] = _tags(item.get("ai_tags"))
        entries.append(item)
    return entries


async def get_entry(ticket_id: int) -> dict[str, Any] | None:
    rows = await db.fetch_all(
        """
        SELECT t.id AS ticket_id, t.subject, t.ai_tags, t.resolution_steps,
               c.name AS company, COALESCE(r.ignored, 0) AS ignored, r.article_id
        FROM tickets t
        LEFT JOIN companies c ON c.id = t.company_id
        LEFT JOIN knowledge_base_resolution_reviews r ON r.ticket_id = t.id
        WHERE t.id = %s AND t.resolution_steps IS NOT NULL AND TRIM(t.resolution_steps) <> ''
        """,
        (ticket_id,),
    )
    if not rows:
        return None
    item = dict(rows[0])
    item["ticket_id"] = int(item["ticket_id"])
    item["ignored"] = bool(item.get("ignored"))
    item["ai_tags"] = _tags(item.get("ai_tags"))
    return item


async def set_ignored(ticket_id: int, ignored: bool, user_id: int) -> None:
    if db.is_sqlite():
        sql = """
            INSERT INTO knowledge_base_resolution_reviews (ticket_id, ignored, updated_by)
            VALUES (%s, %s, %s)
            ON CONFLICT(ticket_id) DO UPDATE SET ignored = excluded.ignored,
              updated_by = excluded.updated_by, updated_at = CURRENT_TIMESTAMP
        """
    else:
        sql = """
            INSERT INTO knowledge_base_resolution_reviews (ticket_id, ignored, updated_by)
            VALUES (%s, %s, %s)
            ON DUPLICATE KEY UPDATE ignored = VALUES(ignored),
              updated_by = VALUES(updated_by), updated_at = CURRENT_TIMESTAMP
        """
    await db.execute(
        sql,
        (ticket_id, 1 if ignored else 0, user_id),
    )


async def link_article(ticket_id: int, article_id: int, user_id: int) -> None:
    if db.is_sqlite():
        sql = """
            INSERT INTO knowledge_base_resolution_reviews (ticket_id, ignored, article_id, updated_by)
            VALUES (%s, 0, %s, %s)
            ON CONFLICT(ticket_id) DO UPDATE SET article_id = excluded.article_id,
              updated_by = excluded.updated_by, updated_at = CURRENT_TIMESTAMP
        """
    else:
        sql = """
            INSERT INTO knowledge_base_resolution_reviews (ticket_id, ignored, article_id, updated_by)
            VALUES (%s, 0, %s, %s)
            ON DUPLICATE KEY UPDATE article_id = VALUES(article_id),
              updated_by = VALUES(updated_by), updated_at = CURRENT_TIMESTAMP
        """
    await db.execute(
        sql,
        (ticket_id, article_id, user_id),
    )
