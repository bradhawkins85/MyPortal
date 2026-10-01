import asyncio
from datetime import datetime
from pathlib import Path

from app.repositories import rag_index


def test_health_aggregates_each_table_in_one_pass(monkeypatch):
    calls: list[str] = []

    async def fetch_one(sql, params=None):
        calls.append(sql)
        if "FROM rag_chunks" in sql:
            return {
                "active_count": 120,
                "stale_count": 8,
                "token_count": 4800,
                "avg_tokens": 40.0,
                "max_tokens": 512,
            }
        if "FROM rag_documents" in sql:
            return {
                "active_count": 30,
                "inactive_count": 2,
                "first_indexed_at": datetime(2026, 1, 1),
                "last_indexed_at": datetime(2026, 9, 30),
                "company_count": 4,
                "model_count": 1,
            }
        return None

    async def fetch_all(sql, params=None):
        calls.append(sql)
        if "GROUP BY d.source_type" in sql:
            return [{"source_type": "tickets", "count": 30, "chunk_count": 120}]
        return []

    monkeypatch.setattr(rag_index.db, "fetch_one", fetch_one)
    monkeypatch.setattr(rag_index.db, "fetch_all", fetch_all)

    result = asyncio.run(rag_index.health())

    assert result["documents"] == 30
    assert result["inactive_documents"] == 2
    assert result["chunks"] == 120
    assert result["stale_chunks"] == 8
    assert result["token_count"] == 4800
    assert result["avg_tokens_per_chunk"] == 40.0
    assert result["max_tokens_per_chunk"] == 512
    assert result["company_count"] == 4
    assert result["sources"][0]["chunk_count"] == 120
    assert result["outbox"] == {"pending": 0, "failed": 0, "oldest_pending_at": None}
    # One aggregate statement per large table instead of one per statistic.
    assert sum("FROM rag_chunks\n" in sql for sql in calls) == 1
    assert len(calls) == 7
    # Chunk statistics never read the wide text or embedding columns.
    assert not any("chunk_text" in sql or "embedding_json" in sql for sql in calls)


def test_health_handles_empty_index(monkeypatch):
    async def fetch_one(sql, params=None):
        return None

    async def fetch_all(sql, params=None):
        return []

    monkeypatch.setattr(rag_index.db, "fetch_one", fetch_one)
    monkeypatch.setattr(rag_index.db, "fetch_all", fetch_all)

    result = asyncio.run(rag_index.health())

    assert result["documents"] == 0
    assert result["chunks"] == 0
    assert result["avg_tokens_per_chunk"] == 0.0
    assert result["sources"] == []


def test_health_covering_index_migration_exists():
    sql = Path("migrations/454_rag_health_covering_indexes.sql").read_text()
    assert "rag_chunks (is_active, document_id, token_count)" in sql
