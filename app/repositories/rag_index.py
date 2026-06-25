from __future__ import annotations

from typing import Any, Sequence

from app.core.database import db


async def upsert_document(record: dict[str, Any]) -> int:
    existing = await db.fetch_one(
        """
        SELECT id FROM rag_documents
        WHERE source_type = ? AND source_id = ? AND embedding_model = ?
        """,
        (record["source_type"], record["source_id"], record["embedding_model"]),
    )
    params = (
        record.get("company_id"),
        record["title"],
        record.get("url"),
        record.get("permission_scope_json"),
        record.get("metadata_json"),
        record["content_hash"],
        1,
    )
    if existing:
        await db.execute(
            """
            UPDATE rag_documents
            SET company_id = ?, title = ?, url = ?, permission_scope_json = ?,
                metadata_json = ?, content_hash = ?, is_active = ?, indexed_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (*params, existing["id"]),
        )
        return int(existing["id"])
    return await db.execute_returning_lastrowid(
        """
        INSERT INTO rag_documents
            (source_type, source_id, company_id, title, url, permission_scope_json,
             metadata_json, content_hash, embedding_model, is_active)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
        """,
        (
            record["source_type"], record["source_id"], record.get("company_id"),
            record["title"], record.get("url"), record.get("permission_scope_json"),
            record.get("metadata_json"), record["content_hash"], record["embedding_model"],
        ),
    )


async def replace_chunks(document_id: int, chunks: Sequence[dict[str, Any]]) -> None:
    await db.execute("UPDATE rag_chunks SET is_active = 0 WHERE document_id = ?", (document_id,))
    for chunk in chunks:
        existing = await db.fetch_one(
            """
            SELECT id FROM rag_chunks
            WHERE document_id = ? AND chunk_index = ? AND embedding_model = ?
            """,
            (document_id, chunk["chunk_index"], chunk["embedding_model"]),
        )
        params = (
            chunk["chunk_text"], chunk["chunk_hash"], chunk["embedding_json"],
            int(chunk.get("token_count") or 0), 1,
        )
        if existing:
            await db.execute(
                """
                UPDATE rag_chunks
                SET chunk_text = ?, chunk_hash = ?, embedding_json = ?, token_count = ?,
                    is_active = ?, indexed_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (*params, existing["id"]),
            )
        else:
            await db.execute(
                """
                INSERT INTO rag_chunks
                    (document_id, chunk_index, chunk_text, chunk_hash, embedding_json,
                     embedding_model, token_count, is_active)
                VALUES (?, ?, ?, ?, ?, ?, ?, 1)
                """,
                (
                    document_id, chunk["chunk_index"], chunk["chunk_text"], chunk["chunk_hash"],
                    chunk["embedding_json"], chunk["embedding_model"], int(chunk.get("token_count") or 0),
                ),
            )


async def list_active_chunks(*, embedding_model: str, source_types: Sequence[str] | None = None) -> list[dict[str, Any]]:
    params: list[Any] = [embedding_model]
    where = ["d.is_active = 1", "c.is_active = 1", "c.embedding_model = ?"]
    if source_types:
        where.append("d.source_type IN (" + ",".join("?" for _ in source_types) + ")")
        params.extend(source_types)
    return await db.fetch_all(
        f"""
        SELECT c.id AS chunk_id, c.chunk_index, c.chunk_text, c.embedding_json,
               d.id AS document_id, d.source_type, d.source_id, d.company_id,
               d.title, d.url, d.permission_scope_json, d.metadata_json, d.indexed_at
        FROM rag_chunks c
        JOIN rag_documents d ON d.id = c.document_id
        WHERE {' AND '.join(where)}
        ORDER BY d.indexed_at DESC, c.id DESC
        LIMIT 2000
        """,
        tuple(params),
    )


async def health() -> dict[str, Any]:
    docs = await db.fetch_one("SELECT COUNT(*) AS count FROM rag_documents WHERE is_active = 1")
    chunks = await db.fetch_one("SELECT COUNT(*) AS count FROM rag_chunks WHERE is_active = 1")
    stale = await db.fetch_one("SELECT COUNT(*) AS count FROM rag_chunks WHERE is_active = 0")
    by_source = await db.fetch_all(
        "SELECT source_type, COUNT(*) AS count, MAX(indexed_at) AS last_indexed_at FROM rag_documents WHERE is_active = 1 GROUP BY source_type ORDER BY source_type"
    )
    return {
        "documents": int((docs or {}).get("count") or 0),
        "chunks": int((chunks or {}).get("count") or 0),
        "stale_chunks": int((stale or {}).get("count") or 0),
        "sources": by_source or [],
    }
