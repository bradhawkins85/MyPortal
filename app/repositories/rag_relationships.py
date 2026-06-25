from __future__ import annotations

from typing import Any, Mapping

from app.core.database import db


def _pair(source_id: int, target_id: int) -> tuple[int, int]:
    return (source_id, target_id) if source_id <= target_id else (target_id, source_id)


async def get_document(document_id: int) -> dict[str, Any] | None:
    return await db.fetch_one(
        "SELECT * FROM rag_documents WHERE id = ? AND is_active = 1", (document_id,)
    )


async def get_document_with_content(document_id: int) -> dict[str, Any] | None:
    row = await db.fetch_one(
        """
        SELECT d.*, GROUP_CONCAT(c.chunk_text, '\n') AS content
        FROM rag_documents d
        LEFT JOIN rag_chunks c ON c.document_id = d.id AND c.is_active = 1
        WHERE d.id = ? AND d.is_active = 1
        GROUP BY d.id
        """,
        (document_id,),
    )
    return row


async def list_compatible_targets(
    document_id: int, *, include_tickets: bool
) -> list[dict[str, Any]]:
    if include_tickets:
        return await db.fetch_all(
            "SELECT * FROM rag_documents WHERE id <> ? AND is_active = 1",
            (document_id,),
        )
    return await db.fetch_all(
        "SELECT * FROM rag_documents WHERE id <> ? AND is_active = 1 AND source_type <> 'tickets'",
        (document_id,),
    )


async def mark_relationships_stale(document_id: int) -> None:
    await db.execute(
        "UPDATE rag_relationships SET match_status = 'STALE' WHERE source_document_id = ? OR target_document_id = ?",
        (document_id, document_id),
    )


async def relationship_current(source_id: int, target_id: int) -> bool:
    left, right = _pair(source_id, target_id)
    source = await get_document(left)
    target = await get_document(right)
    if not source or not target:
        return False
    row = await db.fetch_one(
        """
        SELECT id FROM rag_relationships
        WHERE source_document_id = ? AND target_document_id = ?
          AND source_hash = ? AND target_hash = ?
          AND match_status IN ('MATCH', 'NO_MATCH')
        """,
        (left, right, source.get("content_hash"), target.get("content_hash")),
    )
    return bool(row)


async def enqueue(source_id: int, target_id: int, *, priority: int) -> bool:
    left, right = _pair(source_id, target_id)
    existing = await db.fetch_one(
        """
        SELECT id FROM rag_relationship_queue
        WHERE source_document_id = ? AND target_document_id = ? AND status IN ('PENDING','PROCESSING')
        """,
        (left, right),
    )
    if existing:
        return False
    await db.execute(
        """
        INSERT INTO rag_relationship_queue (source_document_id, target_document_id, priority, status)
        VALUES (?, ?, ?, 'PENDING')
        """,
        (left, right, priority),
    )
    return True


async def claim_jobs(limit: int) -> list[dict[str, Any]]:
    jobs = await db.fetch_all(
        """
        SELECT * FROM rag_relationship_queue
        WHERE status = 'PENDING' AND retry_count < 5
        ORDER BY priority DESC, created_at ASC, id ASC
        LIMIT ?
        """,
        (limit,),
    )
    for job in jobs:
        await db.execute(
            "UPDATE rag_relationship_queue SET status = 'PROCESSING', started_at = CURRENT_TIMESTAMP WHERE id = ?",
            (job["id"],),
        )
    return jobs


async def store_relationship(
    source_id: int,
    target_id: int,
    parsed: Mapping[str, Any],
    *,
    evaluated_model: str,
    source_hash: str,
    target_hash: str,
    duration_ms: int,
) -> None:
    left, right = _pair(source_id, target_id)
    left_hash, right_hash = (
        (source_hash, target_hash) if left == source_id else (target_hash, source_hash)
    )
    params = (
        left,
        right,
        parsed["relationship_type"],
        parsed["match_status"],
        parsed["relevance_score"],
        parsed["confidence"],
        parsed.get("reason"),
        parsed.get("supporting_excerpt"),
        evaluated_model,
        left_hash,
        right_hash,
        duration_ms,
    )
    if db.is_sqlite():
        await db.execute(
            """
            INSERT INTO rag_relationships
                (source_document_id, target_document_id, relationship_type, match_status,
                 relevance_score, confidence, reason, supporting_excerpt, evaluated_model,
                 source_hash, target_hash, evaluation_duration_ms)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source_document_id, target_document_id) DO UPDATE SET
                relationship_type = excluded.relationship_type, match_status = excluded.match_status,
                relevance_score = excluded.relevance_score, confidence = excluded.confidence,
                reason = excluded.reason, supporting_excerpt = excluded.supporting_excerpt,
                evaluated_model = excluded.evaluated_model, evaluated_at = datetime('now'),
                source_hash = excluded.source_hash, target_hash = excluded.target_hash,
                evaluation_duration_ms = excluded.evaluation_duration_ms
            """,
            params,
        )
        return
    await db.execute(
        """
        INSERT INTO rag_relationships
            (source_document_id, target_document_id, relationship_type, match_status,
             relevance_score, confidence, reason, supporting_excerpt, evaluated_model,
             source_hash, target_hash, evaluation_duration_ms)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON DUPLICATE KEY UPDATE relationship_type = VALUES(relationship_type), match_status = VALUES(match_status),
            relevance_score = VALUES(relevance_score), confidence = VALUES(confidence), reason = VALUES(reason),
            supporting_excerpt = VALUES(supporting_excerpt), evaluated_model = VALUES(evaluated_model),
            evaluated_at = CURRENT_TIMESTAMP(6), source_hash = VALUES(source_hash), target_hash = VALUES(target_hash),
            evaluation_duration_ms = VALUES(evaluation_duration_ms)
        """,
        params,
    )


async def complete_queue_item(queue_id: int, status: str) -> None:
    await db.execute(
        "UPDATE rag_relationship_queue SET status = ?, completed_at = CURRENT_TIMESTAMP WHERE id = ?",
        (status, queue_id),
    )


async def fail_queue_item(queue_id: int, error: str, *, max_retries: int) -> None:
    row = (
        await db.fetch_one(
            "SELECT retry_count FROM rag_relationship_queue WHERE id = ?", (queue_id,)
        )
        or {}
    )
    retry_count = int(row.get("retry_count") or 0) + 1
    status = "FAILED" if retry_count >= max_retries else "PENDING"
    await db.execute(
        "UPDATE rag_relationship_queue SET status = ?, retry_count = ?, last_error = ?, completed_at = CASE WHEN ? = 'FAILED' THEN CURRENT_TIMESTAMP ELSE completed_at END WHERE id = ?",
        (status, retry_count, error[:2000], status, queue_id),
    )


async def list_relationship_evidence(
    document_id: int, *, limit: int
) -> list[dict[str, Any]]:
    return await db.fetch_all(
        """
        SELECT r.*, d.source_type, d.source_id, d.title, d.url, GROUP_CONCAT(c.chunk_text, '\n') AS content
        FROM rag_relationships r
        JOIN rag_documents d ON d.id = CASE WHEN r.source_document_id = ? THEN r.target_document_id ELSE r.source_document_id END
        LEFT JOIN rag_chunks c ON c.document_id = d.id AND c.is_active = 1
        WHERE (r.source_document_id = ? OR r.target_document_id = ?)
          AND r.match_status = 'MATCH'
          AND r.relationship_type IN ('DIRECT_MATCH','RELATED','SUPPORTING')
        GROUP BY r.id, d.id
        ORDER BY r.relevance_score DESC, r.confidence DESC
        LIMIT ?
        """,
        (document_id, document_id, document_id, limit),
    )


async def metrics() -> dict[str, Any]:
    queue = await db.fetch_all(
        "SELECT status, COUNT(*) AS count FROM rag_relationship_queue GROUP BY status"
    )
    rels = await db.fetch_all(
        "SELECT match_status, COUNT(*) AS count FROM rag_relationships GROUP BY match_status"
    )
    avg = await db.fetch_one(
        "SELECT COALESCE(AVG(evaluation_duration_ms),0) AS avg_ms FROM rag_relationships"
    )
    total = await db.fetch_one("SELECT COUNT(*) AS count FROM rag_relationships")
    matched_documents = await db.fetch_one("""
        SELECT COUNT(DISTINCT document_id) AS count
        FROM (
            SELECT source_document_id AS document_id
            FROM rag_relationships
            WHERE match_status = 'MATCH'
            UNION
            SELECT target_document_id AS document_id
            FROM rag_relationships
            WHERE match_status = 'MATCH'
        ) matched
        """)
    positive_matches = await db.fetch_one(
        "SELECT COUNT(*) AS count FROM rag_relationships WHERE match_status = 'MATCH'"
    )
    stale_matches = await db.fetch_one(
        "SELECT COUNT(*) AS count FROM rag_relationships WHERE match_status = 'STALE'"
    )
    pending_matches = await db.fetch_one("""
        SELECT COUNT(*) AS count
        FROM rag_relationship_queue
        WHERE status IN ('PENDING', 'PROCESSING')
        """)
    failed_lookups = await db.fetch_one(
        "SELECT COUNT(*) AS count FROM rag_relationship_queue WHERE status = 'FAILED'"
    )
    return {
        "queue": queue,
        "relationships": rels,
        "average_evaluation_duration_ms": float((avg or {}).get("avg_ms") or 0),
        "total_stored_relationships": int((total or {}).get("count") or 0),
        "matched_documents": int((matched_documents or {}).get("count") or 0),
        "positive_matches": int((positive_matches or {}).get("count") or 0),
        "pending_matches": int((pending_matches or {}).get("count") or 0),
        "failed_lookups": int((failed_lookups or {}).get("count") or 0),
        "stale_matches": int((stale_matches or {}).get("count") or 0),
    }
