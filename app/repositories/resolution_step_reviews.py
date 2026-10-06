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
        """ + ignored_clause + " ORDER BY t.resolution_steps_updated_at DESC, t.id DESC",  # nosec B608
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


async def list_recurring_issue_links() -> list[tuple[int, int]]:
    """Return ticket id pairs the relationship engine matched as DUPLICATE or KNOWN_ISSUE."""
    rows = await db.fetch_all(
        """
        SELECT s.source_id AS source_ticket_id, t.source_id AS target_ticket_id
        FROM rag_relationships r
        JOIN rag_documents s ON s.id = r.source_document_id
        JOIN rag_documents t ON t.id = r.target_document_id
        WHERE r.match_status = 'MATCH'
          AND r.relationship_type IN ('DUPLICATE', 'KNOWN_ISSUE')
          AND s.source_type = 'tickets' AND t.source_type = 'tickets'
          AND s.is_active = 1 AND t.is_active = 1
        """,
    )
    pairs = []
    for row in rows or []:
        try:
            source, target = int(row["source_ticket_id"]), int(row["target_ticket_id"])
        except (TypeError, ValueError):
            continue
        if source != target:
            pairs.append((source, target))
    return pairs


def _ticket_ids(value: Any) -> list[int]:
    try:
        decoded = json.loads(value or "[]")
    except (TypeError, json.JSONDecodeError):
        return []
    if not isinstance(decoded, list):
        return []
    return [int(item) for item in decoded if isinstance(item, int) or str(item).isdigit()]


async def list_recurring_reviews() -> dict[int, dict[str, Any]]:
    rows = await db.fetch_all(
        "SELECT anchor_ticket_id, ignored, article_id, ticket_ids FROM knowledge_base_recurring_issue_reviews",
    )
    reviews = {}
    for row in rows or []:
        anchor = int(row["anchor_ticket_id"])
        reviews[anchor] = {
            "ignored": bool(row.get("ignored")),
            "article_id": row.get("article_id"),
            "ticket_ids": _ticket_ids(row.get("ticket_ids")),
        }
    return reviews


async def set_recurring_ignored(anchor_ticket_id: int, ignored: bool, user_id: int) -> None:
    if db.is_sqlite():
        sql = """
            INSERT INTO knowledge_base_recurring_issue_reviews (anchor_ticket_id, ignored, updated_by)
            VALUES (%s, %s, %s)
            ON CONFLICT(anchor_ticket_id) DO UPDATE SET ignored = excluded.ignored,
              updated_by = excluded.updated_by, updated_at = CURRENT_TIMESTAMP
        """
    else:
        sql = """
            INSERT INTO knowledge_base_recurring_issue_reviews (anchor_ticket_id, ignored, updated_by)
            VALUES (%s, %s, %s)
            ON DUPLICATE KEY UPDATE ignored = VALUES(ignored),
              updated_by = VALUES(updated_by), updated_at = CURRENT_TIMESTAMP
        """
    await db.execute(sql, (anchor_ticket_id, 1 if ignored else 0, user_id))


async def link_recurring_article(
    anchor_ticket_id: int, article_id: int, ticket_ids: list[int], user_id: int
) -> None:
    if db.is_sqlite():
        sql = """
            INSERT INTO knowledge_base_recurring_issue_reviews
              (anchor_ticket_id, ignored, article_id, ticket_ids, updated_by)
            VALUES (%s, 0, %s, %s, %s)
            ON CONFLICT(anchor_ticket_id) DO UPDATE SET article_id = excluded.article_id,
              ticket_ids = excluded.ticket_ids, updated_by = excluded.updated_by,
              updated_at = CURRENT_TIMESTAMP
        """
    else:
        sql = """
            INSERT INTO knowledge_base_recurring_issue_reviews
              (anchor_ticket_id, ignored, article_id, ticket_ids, updated_by)
            VALUES (%s, 0, %s, %s, %s)
            ON DUPLICATE KEY UPDATE article_id = VALUES(article_id),
              ticket_ids = VALUES(ticket_ids), updated_by = VALUES(updated_by),
              updated_at = CURRENT_TIMESTAMP
        """
    await db.execute(sql, (anchor_ticket_id, article_id, json.dumps(sorted(ticket_ids)), user_id))
