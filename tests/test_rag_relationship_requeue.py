"""Undoing link learning re-checks the links learning already decided."""
from __future__ import annotations

import aiosqlite
import pytest

from app.repositories import rag_relationships as repo


@pytest.fixture
async def rag_db(monkeypatch):
    connection = await aiosqlite.connect(":memory:")
    connection.row_factory = aiosqlite.Row
    # Only the columns these queries touch (the legacy MySQL migrations don't run on SQLite).
    await connection.executescript("""
        CREATE TABLE rag_documents (id INTEGER PRIMARY KEY, source_type TEXT, source_id TEXT, title TEXT,
            content_hash TEXT, embedding_model TEXT, is_active INTEGER NOT NULL DEFAULT 1);
        CREATE TABLE rag_relationships (id INTEGER PRIMARY KEY, source_document_id INTEGER,
            target_document_id INTEGER, relationship_type TEXT, match_status TEXT, relevance_score REAL,
            confidence REAL, source_hash TEXT, target_hash TEXT, evaluated_model TEXT,
            UNIQUE (source_document_id, target_document_id));
        CREATE TABLE rag_relationship_queue (id INTEGER PRIMARY KEY AUTOINCREMENT, source_document_id INTEGER,
            target_document_id INTEGER, priority INTEGER, status TEXT, retry_count INTEGER DEFAULT 0,
            started_at TEXT, completed_at TEXT, last_error TEXT, claim_token TEXT, lease_expires_at TEXT,
            heartbeat_at TEXT, source_hash TEXT, target_hash TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP, UNIQUE (source_document_id, target_document_id));
    """)
    await connection.executescript("""
        INSERT INTO rag_documents (id, source_type, source_id, title, content_hash, embedding_model) VALUES
            (1, 'tickets', '4', 'Ticket 4', 'h1', 'm'),
            (2, 'knowledge_base', '11', 'VPN', 'h2', 'm'),
            (3, 'tickets', '5', 'Ticket 5', 'h3', 'm'),
            (4, 'knowledge_base', '12', 'Printer', 'h4', 'm');
        INSERT INTO rag_relationships (id, source_document_id, target_document_id, relationship_type, match_status,
            relevance_score, confidence, source_hash, target_hash, evaluated_model) VALUES
            (10, 1, 2, 'NOT_RELEVANT', 'NO_MATCH', 0.4, 0.8, 'h1', 'h2', 'm'),
            (11, 2, 3, 'RELATED', 'MATCH', 0.7, 0.8, 'h2', 'h3', 'm'),
            (12, 1, 4, 'RELATED', 'MATCH', 0.7, 0.8, 'h1', 'h4', 'm'),
            (13, 2, 4, 'RELATED', 'MATCH', 0.7, 0.8, 'h2', 'h4', 'm');
    """)
    await connection.commit()
    monkeypatch.setattr(repo.db, "_use_sqlite", True)
    monkeypatch.setattr(repo.db, "_sqlite_conn", connection)
    yield connection
    await connection.close()


@pytest.mark.anyio
async def test_requeue_ticket_links_for_target(rag_db):
    assert await repo.relationship_current(1, 2)

    assert await repo.requeue_ticket_links_for_target("knowledge_base", "11") == 2

    # Ticket links to the item are no longer current and are queued again, behind new work.
    assert not await repo.relationship_current(1, 2)
    assert not await repo.relationship_current(2, 3)
    cursor = await rag_db.execute(
        "SELECT source_document_id, target_document_id, priority, status FROM rag_relationship_queue ORDER BY 1, 2")
    assert [tuple(row) for row in await cursor.fetchall()] == [(1, 2, 900, "PENDING"), (2, 3, 900, "PENDING")]
    # Links that don't involve a ticket, or a different item, are untouched.
    assert await repo.relationship_current(2, 4)
    assert await repo.relationship_current(1, 4)


@pytest.mark.anyio
async def test_requeue_unknown_target_is_a_no_op(rag_db):
    assert await repo.requeue_ticket_links_for_target("knowledge_base", "999") == 0
