from __future__ import annotations

from datetime import datetime
from typing import Any

from app.core.database import db
from app.core.logging import log_error, log_info
from app.repositories import knowledge_base as kb_repo
from app.repositories import rag_index as rag_repo
from app.repositories import tickets as tickets_repo
from app.services import rag_index

SUPPORTED_SOURCES = frozenset({"tickets", "knowledge_base"})
MAX_ATTEMPTS = 8


async def enqueue(
    source_type: str,
    source_id: int | str,
    *,
    action: str = "upsert",
    source_updated_at: datetime | None = None,
) -> None:
    """Durably coalesce a source change without doing indexing in the request."""
    if source_type not in SUPPORTED_SOURCES or action not in {"upsert", "delete"}:
        raise ValueError("Unsupported RAG outbox event")
    # Service unit tests commonly replace only the source repository. In a real
    # request the source mutation itself cannot succeed without this connection.
    if not db.is_connected():
        return
    if db.is_sqlite():
        sql = """
        INSERT INTO rag_index_outbox
            (source_type, source_id, action, source_updated_at, status, available_at)
        VALUES (?, ?, ?, ?, 'pending', CURRENT_TIMESTAMP)
        ON CONFLICT(source_type, source_id) DO UPDATE SET action = excluded.action,
            source_updated_at = excluded.source_updated_at, status = 'pending',
            attempt_count = 0, available_at = CURRENT_TIMESTAMP,
            claimed_at = NULL, completed_at = NULL, last_error = NULL,
            updated_at = CURRENT_TIMESTAMP
        """
    else:
        sql = """
        INSERT INTO rag_index_outbox
            (source_type, source_id, action, source_updated_at, status, available_at)
        VALUES (?, ?, ?, ?, 'pending', CURRENT_TIMESTAMP)
        ON DUPLICATE KEY UPDATE action = VALUES(action),
            source_updated_at = VALUES(source_updated_at), status = 'pending',
            attempt_count = 0, available_at = CURRENT_TIMESTAMP,
            claimed_at = NULL, completed_at = NULL, last_error = NULL,
            updated_at = CURRENT_TIMESTAMP
        """
    await db.execute(
        sql,
        (source_type, str(source_id), action, source_updated_at),
    )


async def _load_source(source_type: str, source_id: str) -> dict[str, Any] | None:
    numeric_id = int(source_id)
    if source_type == "tickets":
        item = await tickets_repo.get_ticket(numeric_id)
        if item:
            item = dict(item)
            item["replies"] = await tickets_repo.list_replies(numeric_id)
        return item
    item = await kb_repo.get_article_by_id(numeric_id)
    if item:
        item = dict(item)
        if not item.get("is_published"):
            return None
        item["article_permission_scope"] = item.get("permission_scope")
        item["allowed_company_ids"] = item.get("company_ids") or []
    return item


async def process_pending(*, limit: int = 100) -> dict[str, int]:
    """Process persisted work with bounded exponential retry and stale-lease repair."""
    stale_lease = (
        "datetime(CURRENT_TIMESTAMP, '-15 minutes')"
        if db.is_sqlite()
        else "CURRENT_TIMESTAMP - INTERVAL 15 MINUTE"
    )
    await db.execute(
        "UPDATE rag_index_outbox SET status = 'pending', claimed_at = NULL "
        "WHERE status = 'processing' AND claimed_at < " + stale_lease
    )
    rows = await db.fetch_all(
        """SELECT * FROM rag_index_outbox
           WHERE status = 'pending' AND available_at <= CURRENT_TIMESTAMP
           ORDER BY available_at, id LIMIT ?""",
        (max(1, min(limit, 500)),),
    )
    result = {"processed": 0, "failed": 0, "retried": 0}
    for row in rows:
        claimed = await db.execute_rowcount(
            """UPDATE rag_index_outbox SET status = 'processing', claimed_at = CURRENT_TIMESTAMP
               WHERE id = ? AND status = 'pending'""",
            (row["id"],),
        )
        if not claimed:
            continue
        try:
            item = None if row["action"] == "delete" else await _load_source(row["source_type"], row["source_id"])
            if item is None:
                await rag_repo.deactivate_document(row["source_type"], row["source_id"])
            else:
                document = rag_index.document_from_source(row["source_type"], item)
                if document is None:
                    await rag_repo.deactivate_document(row["source_type"], row["source_id"])
                else:
                    await rag_index.index_document(document, source_updated_at=row.get("source_updated_at") or item.get("updated_at"))
            await db.execute(
                """UPDATE rag_index_outbox SET status = 'completed', completed_at = CURRENT_TIMESTAMP,
                   last_error = NULL, updated_at = CURRENT_TIMESTAMP WHERE id = ?""",
                (row["id"],),
            )
            result["processed"] += 1
        except Exception as exc:
            attempts = int(row.get("attempt_count") or 0) + 1
            terminal = attempts >= MAX_ATTEMPTS
            delay = min(3600, 2 ** attempts * 15)
            availability = (
                "datetime(CURRENT_TIMESTAMP, '+' || ? || ' seconds')"
                if db.is_sqlite()
                else "CURRENT_TIMESTAMP + INTERVAL ? SECOND"
            )
            await db.execute(
                "UPDATE rag_index_outbox SET status = ?, attempt_count = ?, "
                "available_at = " + availability + ", last_error = ?, claimed_at = NULL, "
                "updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                ("failed" if terminal else "pending", attempts, delay, str(exc)[:2000], row["id"]),
            )
            result["failed" if terminal else "retried"] += 1
            log_error("RAG incremental indexing failed", source_type=row["source_type"], source_id=row["source_id"], attempts=attempts)
    return result


async def reconcile(*, page_size: int = 250) -> dict[str, int]:
    """Paginate source-native repositories; never truncate at an arbitrary cap."""
    counts = {"indexed": 0, "skipped": 0, "failed": 0, "deactivated": 0}
    active: dict[str, set[str]] = {"tickets": set(), "knowledge_base": set()}
    offset = 0
    while True:
        page = await tickets_repo.list_tickets(limit=page_size, offset=offset)
        if not page:
            break
        for item in page:
            source_id = str(item["id"])
            active["tickets"].add(source_id)
            try:
                item["replies"] = await tickets_repo.list_replies(int(item["id"]))
                document = rag_index.document_from_source("tickets", item)
                if document:
                    await rag_index.index_document(document, source_updated_at=item.get("updated_at"))
                    counts["indexed"] += 1
                else:
                    counts["skipped"] += 1
            except Exception as exc:
                counts["failed"] += 1
                log_error("RAG reconciliation record failed", source_type="tickets", source_id=source_id, error=str(exc))
        offset += len(page)
    articles = await kb_repo.list_articles(include_unpublished=True)
    for item in articles:
        source_id = str(item["id"])
        active["knowledge_base"].add(source_id)
        if not item.get("is_published"):
            await rag_repo.deactivate_document("knowledge_base", source_id)
            counts["skipped"] += 1
            continue
        try:
            item["article_permission_scope"] = item.get("permission_scope")
            item["allowed_company_ids"] = item.get("company_ids") or []
            document = rag_index.document_from_source("knowledge_base", item)
            if document:
                await rag_index.index_document(document, source_updated_at=item.get("updated_at"))
                counts["indexed"] += 1
            else:
                counts["skipped"] += 1
        except Exception as exc:
            counts["failed"] += 1
            log_error("RAG reconciliation record failed", source_type="knowledge_base", source_id=source_id, error=str(exc))
    counts["deactivated"] = await rag_repo.cleanup_missing_documents(active, embedding_model=rag_index.embedding_model())
    log_info("RAG reconciliation completed", **counts)
    return counts
