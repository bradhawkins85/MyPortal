import pytest
import aiosqlite
from contextlib import asynccontextmanager
from pathlib import Path
import sqlite3

from app.repositories import rag_relationships as rag_relationships_repo
from app.core.database import Database
from app.services.rag_relationships import (
    _skip_pair,
    _relationship_response_payload,
    parse_relationship_response,
)


@pytest.mark.anyio
async def test_relationship_evidence_includes_every_positive_type(monkeypatch):
    captured = {}

    async def fetch_all(sql, params):
        captured["sql"] = sql
        return []

    monkeypatch.setattr(rag_relationships_repo.db, "fetch_all", fetch_all)
    await rag_relationships_repo.list_relationship_evidence(11, limit=12)

    for relationship_type in (
        "DIRECT_MATCH",
        "RELATED",
        "SUPPORTING",
        "DUPLICATE",
        "FOLLOW_UP",
        "KNOWN_ISSUE",
        "PARENT_CHILD",
    ):
        assert relationship_type in captured["sql"]
    assert "target_available" in captured["sql"]


def test_parse_relationship_response_stores_positive_match():
    parsed = parse_relationship_response(
        '{"relationship":"DIRECT_MATCH","confidence":0.94,"score":0.93,"reason":"same fix","supporting_excerpt":"replace CMOS"}',
        min_score=0.55,
    )

    assert parsed["relationship_type"] == "DIRECT_MATCH"
    assert parsed["match_status"] == "MATCH"
    assert parsed["relevance_score"] == 0.93
    assert parsed["confidence"] == 0.94


def test_parse_relationship_response_stores_negative_no_match():
    parsed = parse_relationship_response(
        {"relationship": "RELATED", "confidence": 0.5, "score": 0.2},
        min_score=0.55,
    )

    assert parsed["relationship_type"] == "NOT_RELEVANT"
    assert parsed["match_status"] == "NO_MATCH"


def test_parse_relationship_response_accepts_fenced_json():
    parsed = parse_relationship_response(
        '```json\n{"relationship":"RELATED","confidence":0.8,"score":0.75}\n```',
        min_score=0.55,
    )

    assert parsed["relationship_type"] == "RELATED"
    assert parsed["match_status"] == "MATCH"


def test_relationship_response_payload_reads_module_response_key():
    raw = _relationship_response_payload(
        {
            "status": "succeeded",
            "response": {
                "response": '{"relationship":"DUPLICATE","confidence":0.9,"score":0.88}'
            },
        }
    )

    parsed = parse_relationship_response(raw, min_score=0.55)

    assert parsed["relationship_type"] == "DUPLICATE"
    assert parsed["match_status"] == "MATCH"


def test_parse_relationship_response_empty_error_is_actionable():
    try:
        parse_relationship_response("", min_score=0.55)
    except ValueError as exc:
        assert "empty response" in str(exc)
    else:  # pragma: no cover - assertion guard
        raise AssertionError(
            "Expected empty relationship responses to fail with a clear message"
        )


def test_relationship_prompt_prefers_ticket_as_document_a_for_mixed_pair():
    from app.services.rag_relationships import _prompt

    asset = {
        "source_type": "assets",
        "source_id": 42,
        "title": "Laptop Asset",
        "content": "Device inventory details",
    }
    ticket = {
        "source_type": "tickets",
        "source_id": 1001,
        "title": "Laptop will not boot",
        "content": "Customer reports startup failure",
    }

    prompt = _prompt(asset, ticket)

    document_a = prompt.split("----------------------------", 1)[0]
    document_b = prompt.split("----------------------------", 1)[1]
    assert "Document A\ntickets #1001" in document_a
    assert "Document B\nassets #42" in document_b


def test_relationship_prompt_keeps_non_ticket_order():
    from app.services.rag_relationships import _prompt

    asset = {
        "source_type": "assets",
        "source_id": 42,
        "title": "Laptop Asset",
        "content": "",
    }
    article = {
        "source_type": "knowledge_base",
        "source_id": 9,
        "title": "Boot guide",
        "content": "",
    }

    prompt = _prompt(asset, article)

    document_a = prompt.split("----------------------------", 1)[0]
    assert "Document A\nassets #42" in document_a


def test_relationship_evaluator_uses_configured_module_model_by_default():
    from app.services.rag_relationships import _evaluation_payload

    assert _evaluation_payload("compare", "") == {
        "prompt": "compare",
        "format": "json",
    }
    assert _evaluation_payload("compare", "  specialist-model  ")["model"] == (
        "specialist-model"
    )


def test_relationship_queue_priority_prefers_ticket_pairs():
    from app.services.rag_relationships import _relationship_queue_priority

    ticket = {"source_type": "tickets"}
    asset = {"source_type": "assets"}
    article = {"source_type": "knowledge_base"}

    assert _relationship_queue_priority(asset, ticket) > _relationship_queue_priority(
        asset, article
    )
    assert _relationship_queue_priority(ticket, article) > _relationship_queue_priority(
        asset, article
    )


def test_company_scope_rejects_cross_company_and_unapproved_global_documents():
    ticket = {"id": 1, "source_type": "tickets", "company_id": 10}
    other_company = {"id": 2, "source_type": "assets", "company_id": 11}
    unsafe_global = {
        "id": 3,
        "source_type": "knowledge_base",
        "company_id": None,
        "permission_scope_json": '{"visibility":"super_admin"}',
    }
    safe_global = {
        "id": 4,
        "source_type": "knowledge_base",
        "company_id": None,
        "permission_scope_json": '{"visibility":"authenticated"}',
    }

    assert _skip_pair(ticket, other_company, False)
    assert _skip_pair(ticket, unsafe_global, False)
    assert not _skip_pair(ticket, safe_global, False)


@pytest.mark.anyio
async def test_ticket_enqueue_is_bounded_and_preserves_mixed_candidates(monkeypatch):
    from app.services import rag_relationships

    source = {"id": 1, "source_type": "tickets", "company_id": 10}
    targets = [
        {
            "id": 2,
            "source_type": "knowledge_base",
            "company_id": 10,
            "eligible_documents": 5000,
        },
        {
            "id": 3,
            "source_type": "assets",
            "company_id": 10,
            "eligible_documents": 5000,
        },
    ]
    captured: dict = {}

    async def list_targets(document_id, **kwargs):
        captured.update(kwargs)
        return targets

    async def false(*args, **kwargs):
        return False

    async def true(*args, **kwargs):
        return True

    async def record(document_id, **kwargs):
        captured["funnel"] = kwargs

    monkeypatch.setattr(rag_relationships.rel_repo, "matching_paused", false)
    monkeypatch.setattr(rag_relationships.rel_repo, "get_document", lambda *_: None)

    async def get_document(*_):
        return source

    monkeypatch.setattr(rag_relationships.rel_repo, "get_document", get_document)
    monkeypatch.setattr(
        rag_relationships.rel_repo, "list_compatible_targets", list_targets
    )
    monkeypatch.setattr(rag_relationships.rel_repo, "relationship_current", false)
    monkeypatch.setattr(rag_relationships.rel_repo, "enqueue", true)
    monkeypatch.setattr(rag_relationships.rel_repo, "record_candidate_funnel", record)

    assert await rag_relationships.enqueue_relationships_for_document(1) == 2
    assert captured["limit"] <= 100
    assert captured["funnel"] == {
        "eligible_documents": 5000,
        "prefiltered_pairs": 2,
        "queued_evaluations": 2,
    }


@pytest.mark.anyio
async def test_matching_paused_quotes_reserved_key_column(monkeypatch):
    executed: list[tuple[str, tuple]] = []

    async def fake_fetch_one(query, params=()):
        executed.append((query, params))
        return {"value": "1"}

    monkeypatch.setattr(rag_relationships_repo.db, "fetch_one", fake_fetch_one)

    assert await rag_relationships_repo.matching_paused() is True
    query, params = executed[0]
    assert "WHERE `key` = 'paused'" in query
    assert "WHERE key = 'paused'" not in query
    assert params == ()


@pytest.mark.anyio
async def test_set_matching_paused_quotes_reserved_key_column_for_sqlite(monkeypatch):
    executed: list[tuple[str, tuple]] = []

    def fake_is_sqlite():
        return True

    async def fake_execute(query, params=()):
        executed.append((query, params))

    monkeypatch.setattr(rag_relationships_repo.db, "is_sqlite", fake_is_sqlite)
    monkeypatch.setattr(rag_relationships_repo.db, "execute", fake_execute)

    await rag_relationships_repo.set_matching_paused(True)
    query, params = executed[0]
    assert "INSERT INTO rag_matching_state (`key`, value, updated_at)" in query
    assert "ON CONFLICT(`key`)" in query
    assert params == ("1",)


def test_relationship_evaluator_unavailable_reason_includes_skipped_reason():
    from app.services.rag_relationships import (
        _relationship_evaluator_unavailable_reason,
    )

    assert (
        _relationship_evaluator_unavailable_reason(
            {"status": "skipped", "reason": "Module disabled", "module": "ollama"}
        )
        == "Module disabled"
    )
    assert _relationship_evaluator_unavailable_reason({"status": "succeeded"}) is None


@pytest.mark.anyio
async def test_evaluate_next_batch_does_not_claim_jobs_when_ollama_disabled(
    monkeypatch,
):
    from app.services import rag_relationships

    claimed = False

    async def fake_matching_paused():
        return False

    async def fake_get_module(slug, *, redact=True):
        assert slug == "ollama"
        return {"slug": "ollama", "enabled": False}

    async def fake_claim_jobs(limit):
        nonlocal claimed
        claimed = True
        return []

    monkeypatch.setattr(rag_relationships, "_evaluator_retry_after", 0.0)
    monkeypatch.setattr(
        rag_relationships.rel_repo, "matching_paused", fake_matching_paused
    )
    monkeypatch.setattr(
        rag_relationships.modules_service, "get_module", fake_get_module
    )
    monkeypatch.setattr(rag_relationships.rel_repo, "claim_jobs", fake_claim_jobs)

    assert await rag_relationships.evaluate_next_batch(limit=1) == 0
    assert claimed is False
    assert rag_relationships._evaluator_retry_after > 0


@pytest.mark.anyio
async def test_evaluate_next_batch_requeues_evaluator_failures_without_retry_increment(
    monkeypatch,
):
    from app.services import rag_relationships

    reset_calls: list[tuple[int, str]] = []
    failed_calls: list[tuple[int, str]] = []

    async def fake_matching_paused():
        return False

    async def fake_get_module(slug, *, redact=True):
        return {"slug": slug, "enabled": True}

    async def fake_claim_jobs(limit):
        return [
            {
                "id": 9,
                "source_document_id": 1,
                "target_document_id": 2,
                "claim_token": "claim-9",
            }
        ]

    async def fake_get_document_with_content(document_id):
        return {
            "id": document_id,
            "source_type": "knowledge_base",
            "source_id": document_id,
            "title": f"Doc {document_id}",
            "content": "content",
            "content_hash": f"hash-{document_id}",
        }

    async def fake_relationship_current(source_id, target_id):
        return False

    async def fake_trigger_module(*args, **kwargs):
        return {"status": "failed", "last_error": "connection refused"}

    async def fake_reset_queue_item(queue_id, claim_token, note=None):
        assert claim_token == "claim-9"
        reset_calls.append((queue_id, note or ""))

    async def fake_fail_queue_item(queue_id, claim_token, error, *, max_retries):
        assert claim_token == "claim-9"
        failed_calls.append((queue_id, error))

    monkeypatch.setattr(rag_relationships, "_evaluator_retry_after", 0.0)
    monkeypatch.setattr(
        rag_relationships.rel_repo, "matching_paused", fake_matching_paused
    )
    monkeypatch.setattr(
        rag_relationships.modules_service, "get_module", fake_get_module
    )
    monkeypatch.setattr(rag_relationships.rel_repo, "claim_jobs", fake_claim_jobs)
    monkeypatch.setattr(
        rag_relationships.rel_repo,
        "get_document_with_content",
        fake_get_document_with_content,
    )
    monkeypatch.setattr(
        rag_relationships.rel_repo, "relationship_current", fake_relationship_current
    )
    monkeypatch.setattr(
        rag_relationships.modules_service, "trigger_module", fake_trigger_module
    )
    monkeypatch.setattr(
        rag_relationships.rel_repo, "reset_queue_item", fake_reset_queue_item
    )
    monkeypatch.setattr(
        rag_relationships.rel_repo, "fail_queue_item", fake_fail_queue_item
    )

    assert await rag_relationships.evaluate_next_batch(limit=1) == 0
    assert reset_calls == [
        (9, "Relationship evaluator unavailable: connection refused")
    ]
    assert failed_calls == []


@pytest.fixture
async def relationship_queue_db(monkeypatch):
    connection = await aiosqlite.connect(":memory:")
    connection.row_factory = aiosqlite.Row
    await connection.executescript("""
        CREATE TABLE rag_documents (
            id INTEGER PRIMARY KEY, content_hash TEXT NOT NULL, is_active INTEGER NOT NULL
        );
        CREATE TABLE rag_relationship_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_document_id INTEGER NOT NULL,
            target_document_id INTEGER NOT NULL,
            priority INTEGER NOT NULL DEFAULT 1000,
            status TEXT NOT NULL DEFAULT 'PENDING'
                CHECK(status IN ('PENDING','PROCESSING','COMPLETED','SKIPPED','FAILED')),
            retry_count INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            started_at TEXT, completed_at TEXT, last_error TEXT,
            claim_token TEXT, lease_expires_at TEXT, heartbeat_at TEXT,
            source_hash TEXT NOT NULL DEFAULT '', target_hash TEXT NOT NULL DEFAULT '',
            UNIQUE(source_document_id, target_document_id)
        );
        CREATE TABLE rag_relationships (
            id INTEGER PRIMARY KEY, source_document_id INTEGER NOT NULL,
            target_document_id INTEGER NOT NULL, match_status TEXT NOT NULL,
            source_hash TEXT NOT NULL, target_hash TEXT NOT NULL
        );
        INSERT INTO rag_documents VALUES (1, 'one-v1', 1), (2, 'two-v1', 1);
        """)
    monkeypatch.setattr(rag_relationships_repo.db, "_use_sqlite", True)
    monkeypatch.setattr(rag_relationships_repo.db, "_sqlite_conn", connection)
    yield connection
    await connection.close()


@pytest.mark.anyio
async def test_sqlite_compare_and_set_allows_only_one_claim(relationship_queue_db):
    assert await rag_relationships_repo.enqueue(2, 1, priority=1000)

    first = await rag_relationships_repo.claim_jobs(1, lease_seconds=60)
    second = await rag_relationships_repo.claim_jobs(1, lease_seconds=60)

    assert len(first) == 1
    assert first[0]["source_document_id"] == 1
    assert first[0]["target_document_id"] == 2
    assert second == []


@pytest.mark.anyio
async def test_reindex_resets_durable_pair_and_invalidates_old_claim(
    relationship_queue_db,
):
    assert await rag_relationships_repo.enqueue(1, 2, priority=1000)
    old_job = (await rag_relationships_repo.claim_jobs(1, lease_seconds=60))[0]
    await relationship_queue_db.execute(
        "UPDATE rag_documents SET content_hash = 'one-v2' WHERE id = 1"
    )
    await relationship_queue_db.commit()

    assert await rag_relationships_repo.enqueue(2, 1, priority=1100)
    assert not await rag_relationships_repo.complete_queue_item(
        old_job["id"], "COMPLETED", old_job["claim_token"]
    )
    new_job = (await rag_relationships_repo.claim_jobs(1, lease_seconds=60))[0]
    assert new_job["id"] == old_job["id"]
    assert new_job["claim_token"] != old_job["claim_token"]
    assert await rag_relationships_repo.complete_queue_item(
        new_job["id"], "COMPLETED", new_job["claim_token"]
    )


@pytest.mark.anyio
async def test_cleanup_reclaims_only_expired_processing_lease(relationship_queue_db):
    assert await rag_relationships_repo.enqueue(1, 2, priority=1000)
    job = (await rag_relationships_repo.claim_jobs(1, lease_seconds=60))[0]
    await relationship_queue_db.execute(
        "UPDATE rag_relationship_queue SET lease_expires_at = datetime('now', '-1 second') WHERE id = ?",
        (job["id"],),
    )
    await relationship_queue_db.commit()

    result = await rag_relationships_repo.cleanup_stale_matches_and_decisions()

    row = await rag_relationships_repo.db.fetch_one(
        "SELECT status, claim_token FROM rag_relationship_queue WHERE id = ?",
        (job["id"],),
    )
    assert result["processing_reset"] == 1
    assert row == {"status": "PENDING", "claim_token": None}


@pytest.mark.anyio
async def test_mysql_claim_uses_transaction_and_skip_locked(monkeypatch):
    statements: list[str] = []

    class Cursor:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return None

        async def execute(self, query, params):
            statements.append(query)

        async def fetchall(self):
            return [{"id": 7, "source_document_id": 1, "target_document_id": 2}]

    class Connection:
        began = committed = False

        async def begin(self):
            self.began = True

        def cursor(self, _cursor_type):
            return Cursor()

        async def commit(self):
            self.committed = True

        async def rollback(self):
            raise AssertionError("claim should not roll back")

    connection = Connection()

    @asynccontextmanager
    async def acquire():
        yield connection

    monkeypatch.setattr(rag_relationships_repo.db, "is_sqlite", lambda: False)
    monkeypatch.setattr(
        rag_relationships_repo.db,
        "_require_aiomysql",
        lambda: type("MySQL", (), {"DictCursor": object()}),
    )
    monkeypatch.setattr(rag_relationships_repo.db, "acquire", acquire)

    claimed = await rag_relationships_repo.claim_jobs(1, lease_seconds=60)

    assert connection.began and connection.committed
    assert "FOR UPDATE SKIP LOCKED" in statements[0]
    assert "status = 'PENDING'" in statements[1]
    assert claimed[0]["claim_token"]


@pytest.mark.anyio
async def test_completion_rejects_noncanonical_status_before_database_call(monkeypatch):
    async def unexpected_execute(*args, **kwargs):
        raise AssertionError("invalid status must not reach the database")

    monkeypatch.setattr(
        rag_relationships_repo.db, "execute_rowcount", unexpected_execute
    )
    with pytest.raises(ValueError, match="Completion status"):
        await rag_relationships_repo.complete_queue_item(1, "FAILED", "token")


def test_queue_migration_deduplicates_existing_pairs_for_sqlite():
    connection = sqlite3.connect(":memory:")
    try:
        connection.executescript("""
            CREATE TABLE rag_relationship_queue (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_document_id INTEGER NOT NULL,
                target_document_id INTEGER NOT NULL,
                priority INTEGER NOT NULL DEFAULT 1000,
                status TEXT NOT NULL DEFAULT 'PENDING',
                retry_count INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                started_at TEXT, completed_at TEXT, last_error TEXT,
                UNIQUE(source_document_id, target_document_id, status)
            );
            CREATE INDEX idx_rag_relationship_queue_status
                ON rag_relationship_queue(status, priority, created_at);
            INSERT INTO rag_relationship_queue
                (source_document_id, target_document_id, status)
            VALUES (1, 2, 'completed'), (1, 2, 'PENDING');
            """)
        database = Database()
        migration = Path("migrations/386_relationship_queue_leases.sql").read_text(
            encoding="utf-8"
        )
        adapted = database._adapt_sql_for_sqlite(migration)
        for statement in database._split_sql_statements(adapted):
            connection.execute(statement)

        rows = connection.execute(
            "SELECT id, status FROM rag_relationship_queue"
        ).fetchall()
        columns = {
            row[1]
            for row in connection.execute(
                "PRAGMA table_info(rag_relationship_queue)"
            ).fetchall()
        }
        assert rows == [(2, "PENDING")]
        assert {
            "claim_token",
            "lease_expires_at",
            "source_hash",
            "target_hash",
        } <= columns
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO rag_relationship_queue "
                "(source_document_id, target_document_id, status) VALUES (1, 2, 'FAILED')"
            )
    finally:
        connection.close()
