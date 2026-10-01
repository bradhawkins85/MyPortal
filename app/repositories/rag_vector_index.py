"""Derived vector and full-text index used to pre-filter RAG retrieval.

``rag_chunks`` stays the source of truth. On MariaDB 11.7+ a sidecar table
holds each active chunk's embedding in a ``VECTOR`` column with an HNSW index
plus a FULLTEXT index over its text, so retrieval can ask the database for the
nearest and best lexical matches instead of scoring every chunk in Python.

The table is created at runtime because its vector width follows
``RAG_EMBEDDING_DIMENSIONS`` and because older supported servers (MariaDB
10.10+, SQLite) have no ``VECTOR`` type. Every function here is best-effort:
callers fall back to the full scan whenever the index is unsupported, still
backfilling, or erroring.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Sequence

from app.core.config import get_settings
from app.core.database import db
from app.core.logging import log_info, log_warning
from app.services.rag_embedding_identity import embedding_model

_BACKFILL_BATCH = 500
_COVERAGE_TTL_SECONDS = 300.0

_supported: bool | None = None
_tables_ready: set[int] = set()
_ready_until: dict[str, float] = {}
_backfill: dict[str, asyncio.Task[None]] = {}


def _dimensions() -> int:
    return int(get_settings().rag_embedding_dimensions)


def _embedding_model() -> str:
    return embedding_model()


def table_name(dimensions: int | None = None) -> str:
    """Return the sidecar table for one vector width (an int, so safe to inline)."""
    return f"rag_chunk_vectors_d{int(dimensions or _dimensions())}"


def reset_state() -> None:
    """Forget cached capability and readiness (tests, reconnects)."""
    global _supported
    _supported = None
    _tables_ready.clear()
    _ready_until.clear()
    _backfill.clear()


async def is_supported() -> bool:
    """Return whether the connected database provides vector search."""
    global _supported
    if _supported is not None:
        return _supported
    if db.is_sqlite() or not db.is_connected():
        return False
    try:
        await db.fetch_one(
            "SELECT VEC_DISTANCE_COSINE(VEC_FromText('[1,0]'), VEC_FromText('[0,1]')) AS d"
        )
        _supported = True
    except Exception:  # noqa: BLE001 - any failure means the server lacks vectors
        _supported = False
        log_info("RAG vector pre-filter unavailable; database has no vector support")
    return _supported


async def _ensure_table() -> str | None:
    if not await is_supported():
        return None
    dimensions = _dimensions()
    table = table_name(dimensions)
    if dimensions in _tables_ready:
        return table
    exists = await db.fetch_one(
        """
        SELECT 1 AS present FROM information_schema.TABLES
        WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = ?
        """,
        (table,),
    )
    if exists:
        _tables_ready.add(dimensions)
        return table
    await db.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {table} (
            chunk_id INT NOT NULL PRIMARY KEY,
            document_id INT NOT NULL,
            source_type VARCHAR(64) NOT NULL,
            embedding_model VARCHAR(128) NOT NULL,
            chunk_hash VARCHAR(64) NOT NULL,
            search_text LONGTEXT NOT NULL,
            embedding VECTOR({dimensions}) NOT NULL,
            KEY idx_{table}_document (document_id),
            KEY idx_{table}_source_model (source_type, embedding_model),
            FULLTEXT KEY ft_{table}_text (search_text),
            VECTOR INDEX vi_{table}_embedding (embedding) M=16 DISTANCE=cosine
        ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci
        """  # nosec B608 - table name and width are derived from a bounded int setting
    )
    _tables_ready.add(dimensions)
    return table


def _insert_select(table: str, verb: str) -> str:
    return f"""
        {verb} INTO {table}
            (chunk_id, document_id, source_type, embedding_model, chunk_hash,
             search_text, embedding)
        SELECT c.id, d.id, d.source_type, c.embedding_model, c.chunk_hash,
               CONCAT_WS(' ', d.title, d.source_id, c.chunk_text),
               VEC_FromText(c.embedding_json)
        FROM rag_chunks c
        JOIN rag_documents d ON d.id = c.document_id
        WHERE c.is_active = 1 AND d.is_active = 1 AND c.embedding_model = ?
    """  # nosec B608 - table name comes from table_name()


async def sync_document(document_id: int) -> None:
    """Mirror one document's active chunks into the sidecar (write-path hook)."""
    try:
        table = await _ensure_table()
        if not table:
            return
        await db.execute(
            f"DELETE FROM {table} WHERE document_id = ?",  # nosec B608
            (int(document_id),),
        )
        await db.execute(
            _insert_select(table, "REPLACE") + " AND c.document_id = ?",
            (_embedding_model(), int(document_id)),
        )
    except Exception as exc:  # noqa: BLE001 - the index is derived; coverage repairs it
        log_warning("RAG vector index sync failed", document_id=document_id, error=str(exc))


async def remove_documents(document_ids: Sequence[int]) -> None:
    ids = [int(value) for value in document_ids if value is not None]
    if not ids:
        return
    try:
        table = await _ensure_table()
        if not table:
            return
        placeholders = ",".join("?" for _ in ids)
        await db.execute(
            f"DELETE FROM {table} WHERE document_id IN ({placeholders})",  # nosec B608
            tuple(ids),
        )
    except Exception as exc:  # noqa: BLE001
        log_warning("RAG vector index removal failed", error=str(exc))


async def remove_source(source_type: str, source_id: str) -> None:
    try:
        table = await _ensure_table()
        if not table:
            return
        await db.execute(
            f"""
            DELETE FROM {table} WHERE document_id IN (
                SELECT id FROM rag_documents WHERE source_type = ? AND source_id = ?
            )
            """,  # nosec B608
            (source_type, source_id),
        )
    except Exception as exc:  # noqa: BLE001
        log_warning("RAG vector index removal failed", error=str(exc))


async def coverage() -> dict[str, int]:
    """Count active chunks missing from (or outdated in) the sidecar, and stale rows."""
    table = await _ensure_table()
    if not table:
        return {"missing": -1, "stale": -1}
    model = _embedding_model()
    missing = await db.fetch_one(
        f"""
        SELECT COUNT(*) AS count
        FROM rag_chunks c
        JOIN rag_documents d ON d.id = c.document_id
        LEFT JOIN {table} v ON v.chunk_id = c.id AND v.chunk_hash = c.chunk_hash
        WHERE c.is_active = 1 AND d.is_active = 1 AND c.embedding_model = ?
          AND v.chunk_id IS NULL
        """,  # nosec B608
        (model,),
    )
    stale = await db.fetch_one(
        f"""
        SELECT COUNT(*) AS count
        FROM {table} v
        LEFT JOIN rag_chunks c ON c.id = v.chunk_id
        LEFT JOIN rag_documents d ON d.id = c.document_id
        WHERE c.id IS NULL OR c.is_active = 0 OR d.id IS NULL OR d.is_active = 0
           OR c.chunk_hash <> v.chunk_hash OR c.embedding_model <> ?
        """,  # nosec B608
        (model,),
    )
    return {
        "missing": int((missing or {}).get("count") or 0),
        "stale": int((stale or {}).get("count") or 0),
    }


async def backfill() -> int:
    """Remove stale rows and insert every missing active chunk, in batches."""
    table = await _ensure_table()
    if not table:
        return 0
    model = _embedding_model()
    await db.execute(
        f"""
        DELETE v FROM {table} v
        LEFT JOIN rag_chunks c ON c.id = v.chunk_id
        LEFT JOIN rag_documents d ON d.id = c.document_id
        WHERE c.id IS NULL OR c.is_active = 0 OR d.id IS NULL OR d.is_active = 0
           OR c.chunk_hash <> v.chunk_hash OR c.embedding_model <> ?
        """,  # nosec B608
        (model,),
    )
    inserted = 0
    while True:
        count = await db.execute_rowcount(
            _insert_select(table, "INSERT IGNORE")
            + f"""
              AND NOT EXISTS (SELECT 1 FROM {table} v WHERE v.chunk_id = c.id)
            ORDER BY c.id
            LIMIT ?
            """,  # nosec B608
            (model, _BACKFILL_BATCH),
        )
        inserted += count
        if count < _BACKFILL_BATCH:
            break
    return inserted


async def _run_backfill(key: str) -> None:
    try:
        inserted = await backfill()
        remaining = await coverage()
        if remaining["missing"] == 0:
            _ready_until[key] = time.monotonic() + _COVERAGE_TTL_SECONDS
        log_info(
            "RAG vector index backfill completed",
            inserted=inserted,
            missing=remaining["missing"],
        )
    except Exception as exc:  # noqa: BLE001
        log_warning("RAG vector index backfill failed", error=str(exc))


async def prefilter_ready() -> bool:
    """Return True only when the sidecar covers every active chunk.

    A partial index would silently drop documents from retrieval, so until a
    backfill completes the caller keeps using the full scan.
    """
    try:
        if not bool(get_settings().rag_vector_prefilter) or not await is_supported():
            return False
        key = f"{table_name()}:{_embedding_model()}"
        if _ready_until.get(key, 0.0) > time.monotonic():
            return True
        state = await coverage()
        if state["missing"] == 0:
            _ready_until[key] = time.monotonic() + _COVERAGE_TTL_SECONDS
        running = _backfill.get("task")
        if (state["missing"] or state["stale"]) and (running is None or running.done()):
            _backfill["task"] = asyncio.create_task(_run_backfill(key))
        return state["missing"] == 0
    except Exception as exc:  # noqa: BLE001
        log_warning("RAG vector pre-filter check failed", error=str(exc))
        return False


async def nearest_chunk_ids(
    source_type: str, query_embedding: Sequence[float], *, limit: int, offset: int = 0
) -> list[int]:
    table = table_name()
    rows = await db.fetch_all(
        f"""
        SELECT chunk_id FROM {table}
        WHERE source_type = ? AND embedding_model = ?
        ORDER BY VEC_DISTANCE_COSINE(embedding, VEC_FromText(?))
        LIMIT ? OFFSET ?
        """,  # nosec B608
        (
            source_type,
            _embedding_model(),
            json.dumps([float(v) for v in query_embedding]),
            int(limit),
            int(offset),
        ),
    )
    return [int(row["chunk_id"]) for row in rows or []]


async def lexical_chunk_ids(source_type: str, text: str, *, limit: int) -> list[int]:
    if not text.strip():
        return []
    table = table_name()
    rows = await db.fetch_all(
        f"""
        SELECT chunk_id, MATCH(search_text) AGAINST (? IN NATURAL LANGUAGE MODE) AS relevance
        FROM {table}
        WHERE source_type = ? AND embedding_model = ?
          AND MATCH(search_text) AGAINST (? IN NATURAL LANGUAGE MODE)
        ORDER BY relevance DESC
        LIMIT ?
        """,  # nosec B608
        (text, source_type, _embedding_model(), text, int(limit)),
    )
    return [int(row["chunk_id"]) for row in rows or []]

