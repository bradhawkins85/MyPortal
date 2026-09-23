from __future__ import annotations

import json
import math
import re
from collections import defaultdict
from typing import Any, Mapping
from uuid import uuid4

from app.core.database import db
from app.core.config import get_settings

QUEUE_STATUSES = frozenset({"PENDING", "PROCESSING", "COMPLETED", "SKIPPED", "FAILED"})


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
    document_id: int,
    *,
    include_tickets: bool,
    limit: int = 24,
    ticket_limit: int = 5,
) -> list[dict[str, Any]]:
    """Return a deterministic, scoped top-K relationship candidate set.

    The database performs cheap security and source-type prefiltering first. A
    hybrid identifier/tag, lexical and persisted-vector score then ranks the
    remaining documents without making an LLM call.
    """
    source = await get_document(document_id)
    if not source or limit <= 0:
        return []
    compatible = _COMPATIBLE_SOURCES.get(str(source.get("source_type") or ""))
    if compatible is None:
        compatible = frozenset({str(source.get("source_type") or "")})
    if not include_tickets and str(source.get("source_type") or "") == "tickets":
        compatible = compatible - {"tickets"}
    if not compatible:
        return []

    placeholders = ",".join("?" for _ in compatible)
    params: list[Any] = [
        document_id,
        source.get("embedding_model"),
        *sorted(compatible),
    ]
    company_id = source.get("company_id")
    if company_id is None:
        scope_sql = "d.company_id IS NULL"
    else:
        # Null-company documents are only admitted below when their stored
        # permission scope explicitly marks them as globally visible.
        scope_sql = "(d.company_id = ? OR d.company_id IS NULL)"
        params.append(company_id)
    chunk_rows = await db.fetch_all(
        "SELECT d.*, c.chunk_text AS candidate_text, c.embedding_json AS candidate_embedding "
        "FROM rag_documents d LEFT JOIN rag_chunks c ON c.document_id = d.id AND c.is_active = 1 "
        "WHERE d.id <> ? AND d.is_active = 1 AND d.embedding_model = ? "
        "AND d.source_type IN (" + placeholders + ") AND " + scope_sql,
        tuple(params),
    )
    documents: dict[int, dict[str, Any]] = {}
    for chunk in chunk_rows:
        document = documents.setdefault(
            int(chunk["id"]), {**chunk, "candidate_text": "", "candidate_vectors": []}
        )
        document["candidate_text"] += " " + str(chunk.get("candidate_text") or "")
        vector = _json_vector(chunk.get("candidate_embedding"))
        if vector:
            document["candidate_vectors"].append(vector)
    source_chunks = await db.fetch_all(
        "SELECT chunk_text, embedding_json FROM rag_chunks WHERE document_id = ? AND is_active = 1",
        (document_id,),
    )
    source_text = " ".join(
        [str(source.get("title") or "")]
        + [str(chunk.get("chunk_text") or "") for chunk in source_chunks]
    )
    source_vectors = [
        _json_vector(chunk.get("embedding_json")) for chunk in source_chunks
    ]
    source_vectors = [vector for vector in source_vectors if vector]

    eligible: list[dict[str, Any]] = []
    for row in documents.values():
        if (
            row.get("company_id") is None
            and company_id is not None
            and not _is_authorised_global(row)
        ):
            continue
        row["candidate_score"] = _candidate_score(
            source, source_text, source_vectors, row
        )
        eligible.append(row)
    eligible.sort(key=lambda row: (-float(row["candidate_score"]), int(row["id"])))
    eligible_count = len(eligible)
    per_source: defaultdict[str, int] = defaultdict(int)
    selected: list[dict[str, Any]] = []
    for row in eligible:
        source_type = str(row.get("source_type") or "")
        cap = (
            ticket_limit
            if source_type == "tickets"
            else _SOURCE_CANDIDATE_LIMITS.get(source_type, 8)
        )
        if per_source[source_type] >= cap:
            continue
        per_source[source_type] += 1
        selected.append(row)
        if len(selected) >= limit:
            break
    for row in selected:
        row["eligible_documents"] = eligible_count
    return selected


_COMPATIBLE_SOURCES: dict[str, frozenset[str]] = {
    "tickets": frozenset({"knowledge_base", "assets", "tickets"}),
    "ticket_comments": frozenset({"tickets", "knowledge_base"}),
    "knowledge_base": frozenset({"tickets", "knowledge_base", "assets"}),
    "assets": frozenset({"tickets", "knowledge_base", "assets"}),
    "issues": frozenset({"tickets", "knowledge_base", "issues"}),
    "best_practices": frozenset({"knowledge_base", "best_practices"}),
}
_SOURCE_CANDIDATE_LIMITS = {"knowledge_base": 10, "assets": 8, "tickets": 5}
_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9_.:@#-]+", re.IGNORECASE)
_IDENTIFIER_RE = re.compile(
    r"(?:#[0-9]{3,}|\b[a-z]{2,}[-_]?[0-9]{2,}\b|\b[0-9a-f]{8,}\b)", re.IGNORECASE
)


def _json(value: Any, fallback: Any) -> Any:
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value or ""))
    except (TypeError, ValueError):
        return fallback


def _json_vector(value: Any) -> list[float]:
    parsed = _json(value, [])
    try:
        return [float(item) for item in parsed] if isinstance(parsed, list) else []
    except (TypeError, ValueError):
        return []


def _is_authorised_global(document: Mapping[str, Any]) -> bool:
    scope = _json(document.get("permission_scope_json"), {})
    return str(scope.get("visibility") or "") in {"anonymous", "authenticated"}


def company_scope_compatible(
    source: Mapping[str, Any], target: Mapping[str, Any]
) -> bool:
    """Apply the same fail-closed company boundary used during prefiltering."""
    source_company = source.get("company_id")
    target_company = target.get("company_id")
    if source_company is None:
        return target_company is None
    if target_company is None:
        return _is_authorised_global(target)
    return int(source_company) == int(target_company)


def _flatten_metadata(value: Any) -> set[str]:
    metadata = _json(value, {})
    if not isinstance(metadata, Mapping):
        return set()
    result: set[str] = set()
    for key, item in metadata.items():
        if (
            "id" in str(key).casefold()
            or "tag" in str(key).casefold()
            or "serial" in str(key).casefold()
        ):
            values = item if isinstance(item, (list, tuple, set)) else [item]
            result.update(str(v).casefold() for v in values if v not in (None, ""))
    return result


def _cosine(left: list[float], right: list[float]) -> float:
    if not left or len(left) != len(right):
        return 0.0
    denominator = math.sqrt(sum(v * v for v in left)) * math.sqrt(
        sum(v * v for v in right)
    )
    return sum(a * b for a, b in zip(left, right)) / denominator if denominator else 0.0


def _candidate_score(
    source: Mapping[str, Any],
    source_text: str,
    source_vectors: list[list[float]],
    target: Mapping[str, Any],
) -> float:
    target_text = " ".join(
        (str(target.get("title") or ""), str(target.get("candidate_text") or ""))
    )
    source_tokens = set(_TOKEN_RE.findall(source_text.casefold()))
    target_tokens = set(_TOKEN_RE.findall(target_text.casefold()))
    lexical = len(source_tokens & target_tokens) / max(
        1, len(source_tokens | target_tokens)
    )
    identifiers = set(_IDENTIFIER_RE.findall(source_text)) & set(
        _IDENTIFIER_RE.findall(target_text)
    )
    metadata = _flatten_metadata(source.get("metadata_json")) & _flatten_metadata(
        target.get("metadata_json")
    )
    target_vectors = target.get("candidate_vectors") or []
    semantic = max(
        (_cosine(a, b) for a in source_vectors for b in target_vectors if b),
        default=0.0,
    )
    exact = 1.0 if identifiers or metadata else 0.0
    return (0.50 * max(0.0, semantic)) + (0.30 * lexical) + (0.20 * exact)


async def mark_relationships_stale(document_id: int) -> None:
    await db.execute(
        "UPDATE rag_relationships SET match_status = 'STALE' WHERE source_document_id = ? OR target_document_id = ?",
        (document_id, document_id),
    )


async def record_candidate_funnel(
    document_id: int,
    *,
    eligible_documents: int,
    prefiltered_pairs: int,
    queued_evaluations: int,
) -> None:
    await db.execute(
        """
        INSERT INTO rag_relationship_candidate_runs
            (document_id, eligible_documents, prefiltered_pairs, queued_evaluations)
        VALUES (?, ?, ?, ?)
        """,
        (document_id, eligible_documents, prefiltered_pairs, queued_evaluations),
    )


async def record_candidate_match(source_id: int, target_id: int) -> None:
    """Attribute a positive result to the newest funnel run for either endpoint."""
    row = await db.fetch_one(
        """
        SELECT id FROM rag_relationship_candidate_runs
        WHERE document_id IN (?, ?)
        ORDER BY created_at DESC, id DESC LIMIT 1
        """,
        (source_id, target_id),
    )
    if row:
        await db.execute(
            "UPDATE rag_relationship_candidate_runs SET positive_matches = positive_matches + 1 WHERE id = ?",
            (row["id"],),
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
    source = await get_document(left)
    target = await get_document(right)
    if not source or not target:
        return False
    source_hash = str(source.get("content_hash") or "")
    target_hash = str(target.get("content_hash") or "")
    if db.is_sqlite():
        changed = await db.execute_rowcount(
            """
            INSERT INTO rag_relationship_queue
                (source_document_id, target_document_id, priority, status, source_hash, target_hash)
            VALUES (?, ?, ?, 'PENDING', ?, ?)
            ON CONFLICT(source_document_id, target_document_id) DO UPDATE SET
                priority = MAX(rag_relationship_queue.priority, excluded.priority),
                status = 'PENDING', retry_count = 0, started_at = NULL,
                completed_at = NULL, last_error = NULL, claim_token = NULL,
                lease_expires_at = NULL, heartbeat_at = NULL,
                source_hash = excluded.source_hash, target_hash = excluded.target_hash
            WHERE rag_relationship_queue.status NOT IN ('PENDING', 'PROCESSING')
               OR rag_relationship_queue.source_hash <> excluded.source_hash
               OR rag_relationship_queue.target_hash <> excluded.target_hash
            """,
            (left, right, priority, source_hash, target_hash),
        )
    else:
        changed = await db.execute_rowcount(
            """
            INSERT INTO rag_relationship_queue
                (source_document_id, target_document_id, priority, status, source_hash, target_hash)
            VALUES (?, ?, ?, 'PENDING', ?, ?)
            ON DUPLICATE KEY UPDATE
                priority = GREATEST(priority, VALUES(priority)),
                retry_count = IF(status IN ('PENDING','PROCESSING') AND source_hash <=> VALUES(source_hash) AND target_hash <=> VALUES(target_hash), retry_count, 0),
                started_at = IF(status IN ('PENDING','PROCESSING') AND source_hash <=> VALUES(source_hash) AND target_hash <=> VALUES(target_hash), started_at, NULL),
                completed_at = IF(status IN ('PENDING','PROCESSING') AND source_hash <=> VALUES(source_hash) AND target_hash <=> VALUES(target_hash), completed_at, NULL),
                last_error = IF(status IN ('PENDING','PROCESSING') AND source_hash <=> VALUES(source_hash) AND target_hash <=> VALUES(target_hash), last_error, NULL),
                claim_token = IF(status IN ('PENDING','PROCESSING') AND source_hash <=> VALUES(source_hash) AND target_hash <=> VALUES(target_hash), claim_token, NULL),
                lease_expires_at = IF(status IN ('PENDING','PROCESSING') AND source_hash <=> VALUES(source_hash) AND target_hash <=> VALUES(target_hash), lease_expires_at, NULL),
                heartbeat_at = IF(status IN ('PENDING','PROCESSING') AND source_hash <=> VALUES(source_hash) AND target_hash <=> VALUES(target_hash), heartbeat_at, NULL),
                status = IF(status IN ('PENDING','PROCESSING') AND source_hash <=> VALUES(source_hash) AND target_hash <=> VALUES(target_hash), status, 'PENDING'),
                source_hash = VALUES(source_hash), target_hash = VALUES(target_hash)
            """,
            (left, right, priority, source_hash, target_hash),
        )
    return changed > 0


async def claim_jobs(
    limit: int, *, lease_seconds: int | None = None
) -> list[dict[str, Any]]:
    """Atomically claim pending jobs and return only claims won by this caller."""

    if limit <= 0:
        return []
    lease_seconds = int(lease_seconds or get_settings().rag_relationship_lease_seconds)
    claimed: list[dict[str, Any]] = []
    if db.is_sqlite():
        candidates = await db.fetch_all(
            """
            SELECT * FROM rag_relationship_queue
            WHERE status = 'PENDING' AND retry_count < 5
            ORDER BY priority DESC, created_at ASC, id ASC LIMIT ?
            """,
            (limit,),
        )
        for job in candidates:
            token = str(uuid4())
            won = await db.execute_rowcount(
                """
                UPDATE rag_relationship_queue
                SET status = 'PROCESSING', started_at = CURRENT_TIMESTAMP,
                    heartbeat_at = CURRENT_TIMESTAMP,
                    lease_expires_at = datetime('now', '+' || ? || ' seconds'),
                    claim_token = ?, completed_at = NULL
                WHERE id = ? AND status = 'PENDING' AND retry_count < 5
                """,
                (lease_seconds, token, job["id"]),
            )
            if won:
                job.update({"status": "PROCESSING", "claim_token": token})
                claimed.append(job)
        return claimed

    mysql = db._require_aiomysql()
    async with db.acquire() as conn:
        await conn.begin()
        try:
            async with conn.cursor(mysql.DictCursor) as cursor:
                await cursor.execute(
                    """
                    SELECT * FROM rag_relationship_queue
                    WHERE status = 'PENDING' AND retry_count < 5
                    ORDER BY priority DESC, created_at ASC, id ASC
                    LIMIT %s FOR UPDATE SKIP LOCKED
                    """,
                    (limit,),
                )
                rows = list(await cursor.fetchall())
                for job in rows:
                    token = str(uuid4())
                    await cursor.execute(
                        """
                        UPDATE rag_relationship_queue
                        SET status = 'PROCESSING', started_at = UTC_TIMESTAMP(6),
                            heartbeat_at = UTC_TIMESTAMP(6),
                            lease_expires_at = DATE_ADD(UTC_TIMESTAMP(6), INTERVAL %s SECOND),
                            claim_token = %s, completed_at = NULL
                        WHERE id = %s AND status = 'PENDING'
                        """,
                        (lease_seconds, token, job["id"]),
                    )
                    job.update({"status": "PROCESSING", "claim_token": token})
                    claimed.append(job)
            await conn.commit()
            return claimed
        except Exception:
            await conn.rollback()
            raise


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


def _queue_status(status: str) -> str:
    canonical = status.upper()
    if canonical not in QUEUE_STATUSES:
        raise ValueError(f"Invalid relationship queue status: {status}")
    return canonical


async def heartbeat_queue_item(
    queue_id: int, claim_token: str, *, lease_seconds: int | None = None
) -> bool:
    lease_seconds = int(lease_seconds or get_settings().rag_relationship_lease_seconds)
    if db.is_sqlite():
        sql = """
            UPDATE rag_relationship_queue SET heartbeat_at = CURRENT_TIMESTAMP,
                lease_expires_at = datetime('now', '+' || ? || ' seconds')
            WHERE id = ? AND status = 'PROCESSING' AND claim_token = ?
        """
    else:
        sql = """
            UPDATE rag_relationship_queue SET heartbeat_at = UTC_TIMESTAMP(6),
                lease_expires_at = DATE_ADD(UTC_TIMESTAMP(6), INTERVAL ? SECOND)
            WHERE id = ? AND status = 'PROCESSING' AND claim_token = ?
        """
    return bool(await db.execute_rowcount(sql, (lease_seconds, queue_id, claim_token)))


async def complete_queue_item(queue_id: int, status: str, claim_token: str) -> bool:
    canonical = _queue_status(status)
    if canonical not in {"COMPLETED", "SKIPPED"}:
        raise ValueError("Completion status must be COMPLETED or SKIPPED")
    return bool(
        await db.execute_rowcount(
            """
            UPDATE rag_relationship_queue
            SET status = ?, completed_at = CURRENT_TIMESTAMP, claim_token = NULL,
                lease_expires_at = NULL, heartbeat_at = NULL
            WHERE id = ? AND status = 'PROCESSING' AND claim_token = ?
            """,
            (canonical, queue_id, claim_token),
        )
    )


async def reset_queue_item(
    queue_id: int, claim_token: str, note: str | None = None
) -> bool:
    return bool(
        await db.execute_rowcount(
            """
            UPDATE rag_relationship_queue SET status = 'PENDING', started_at = NULL,
                completed_at = NULL, last_error = ?, claim_token = NULL,
                lease_expires_at = NULL, heartbeat_at = NULL
            WHERE id = ? AND status = 'PROCESSING' AND claim_token = ?
            """,
            ((note or "")[:2000], queue_id, claim_token),
        )
    )


async def fail_queue_item(
    queue_id: int, claim_token: str, error: str, *, max_retries: int
) -> bool:
    return bool(
        await db.execute_rowcount(
            """
            UPDATE rag_relationship_queue
            SET retry_count = retry_count + 1,
                status = CASE WHEN retry_count + 1 >= ? THEN 'FAILED' ELSE 'PENDING' END,
                last_error = ?,
                completed_at = CASE WHEN retry_count + 1 >= ? THEN CURRENT_TIMESTAMP ELSE NULL END,
                started_at = CASE WHEN retry_count + 1 >= ? THEN started_at ELSE NULL END,
                claim_token = NULL, lease_expires_at = NULL, heartbeat_at = NULL
            WHERE id = ? AND status = 'PROCESSING' AND claim_token = ?
            """,
            (
                max_retries,
                error[:2000],
                max_retries,
                max_retries,
                queue_id,
                claim_token,
            ),
        )
    )


async def list_relationship_evidence(
    document_id: int, *, limit: int
) -> list[dict[str, Any]]:
    return await db.fetch_all(
        """
        SELECT r.*, d.source_type, d.source_id, d.title, d.url,
               d.permission_scope_json, d.metadata_json,
               CASE WHEN d.id IS NOT NULL AND d.is_active = 1 THEN 1 ELSE 0 END AS target_available,
               GROUP_CONCAT(c.chunk_text, '\n') AS content
        FROM rag_relationships r
        LEFT JOIN rag_documents d ON d.id = CASE WHEN r.source_document_id = ? THEN r.target_document_id ELSE r.source_document_id END
        LEFT JOIN rag_chunks c ON c.document_id = d.id AND c.is_active = 1
        WHERE (r.source_document_id = ? OR r.target_document_id = ?)
          AND r.match_status = 'MATCH'
          AND r.relationship_type IN ('DIRECT_MATCH','RELATED','SUPPORTING','DUPLICATE','FOLLOW_UP','KNOWN_ISSUE','PARENT_CHILD')
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
    funnel = await db.fetch_one(
        """
        SELECT COALESCE(SUM(eligible_documents), 0) AS eligible_documents,
               COALESCE(SUM(prefiltered_pairs), 0) AS prefiltered_pairs,
               COALESCE(SUM(queued_evaluations), 0) AS queued_evaluations,
               COALESCE(SUM(positive_matches), 0) AS positive_matches
        FROM rag_relationship_candidate_runs
        """
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
        "candidate_funnel": {
            key: int((funnel or {}).get(key) or 0)
            for key in (
                "eligible_documents",
                "prefiltered_pairs",
                "queued_evaluations",
                "positive_matches",
            )
        },
        "matching_paused": await matching_paused(),
    }


async def matching_paused() -> bool:
    row = await db.fetch_one(
        "SELECT value FROM rag_matching_state WHERE `key` = 'paused'",
        (),
    )
    return str((row or {}).get("value") or "0").lower() in {"1", "true", "yes"}


async def set_matching_paused(paused: bool) -> None:
    value = "1" if paused else "0"
    if db.is_sqlite():
        await db.execute(
            """
            INSERT INTO rag_matching_state (`key`, value, updated_at)
            VALUES ('paused', ?, CURRENT_TIMESTAMP)
            ON CONFLICT(`key`) DO UPDATE SET value = excluded.value, updated_at = CURRENT_TIMESTAMP
            """,
            (value,),
        )
        return
    await db.execute(
        """
        INSERT INTO rag_matching_state (`key`, value, updated_at)
        VALUES ('paused', ?, CURRENT_TIMESTAMP(6))
        ON DUPLICATE KEY UPDATE value = VALUES(value), updated_at = CURRENT_TIMESTAMP(6)
        """,
        (value,),
    )


async def cleanup_stale_matches_and_decisions() -> dict[str, int]:
    stale_relationships = await db.execute_rowcount(
        (
            """
        DELETE r FROM rag_relationships r
        LEFT JOIN rag_documents s ON s.id = r.source_document_id
        LEFT JOIN rag_documents t ON t.id = r.target_document_id
        WHERE r.match_status = 'STALE'
           OR s.id IS NULL OR t.id IS NULL
           OR s.is_active = 0 OR t.is_active = 0
           OR r.source_hash <> s.content_hash
           OR r.target_hash <> t.content_hash
        """
            if not db.is_sqlite()
            else """
        DELETE FROM rag_relationships
        WHERE id IN (
            SELECT r.id FROM rag_relationships r
            LEFT JOIN rag_documents s ON s.id = r.source_document_id
            LEFT JOIN rag_documents t ON t.id = r.target_document_id
            WHERE r.match_status = 'STALE'
               OR s.id IS NULL OR t.id IS NULL
               OR s.is_active = 0 OR t.is_active = 0
               OR r.source_hash <> s.content_hash
               OR r.target_hash <> t.content_hash
        )
        """
        ),
        (),
    )
    stale_queue = await db.execute_rowcount(
        (
            """
        DELETE q FROM rag_relationship_queue q
        LEFT JOIN rag_documents s ON s.id = q.source_document_id
        LEFT JOIN rag_documents t ON t.id = q.target_document_id
        WHERE s.id IS NULL OR t.id IS NULL OR s.is_active = 0 OR t.is_active = 0
        """
            if not db.is_sqlite()
            else """
        DELETE FROM rag_relationship_queue
        WHERE id IN (
            SELECT q.id FROM rag_relationship_queue q
            LEFT JOIN rag_documents s ON s.id = q.source_document_id
            LEFT JOIN rag_documents t ON t.id = q.target_document_id
            WHERE s.id IS NULL OR t.id IS NULL OR s.is_active = 0 OR t.is_active = 0
        )
        """
        ),
        (),
    )
    reset_processing = await db.execute_rowcount(
        """
        UPDATE rag_relationship_queue
        SET status = 'PENDING', started_at = NULL, claim_token = NULL,
            lease_expires_at = NULL, heartbeat_at = NULL,
            last_error = 'Processing lease expired; job reclaimed'
        WHERE status = 'PROCESSING'
          AND lease_expires_at IS NOT NULL
          AND lease_expires_at < CURRENT_TIMESTAMP
        """,
        (),
    )
    return {
        "relationships_deleted": stale_relationships,
        "queue_deleted": stale_queue,
        "processing_reset": reset_processing,
    }
