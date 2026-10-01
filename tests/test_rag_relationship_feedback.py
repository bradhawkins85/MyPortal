"""Technician 👍/👎 feedback on ticket Related items."""
import json
from pathlib import Path
from types import SimpleNamespace

import aiosqlite
import pytest
from fastapi import HTTPException

from app.core.database import Database
from app.features.tickets import admin_routes
from app.repositories import rag_relationships as repo
from scripts.evaluate_ai_quality import evaluate_related_feedback


@pytest.fixture
async def feedback_db(monkeypatch):
    connection = await aiosqlite.connect(":memory:")
    connection.row_factory = aiosqlite.Row
    await connection.executescript("""
        CREATE TABLE users (id INTEGER PRIMARY KEY);
        CREATE TABLE tickets (id INTEGER PRIMARY KEY);
        CREATE TABLE rag_documents (
            id INTEGER PRIMARY KEY, source_type TEXT, source_id TEXT, title TEXT, url TEXT,
            permission_scope_json TEXT, metadata_json TEXT, is_active INTEGER NOT NULL DEFAULT 1
        );
        CREATE TABLE rag_chunks (
            id INTEGER PRIMARY KEY, document_id INTEGER, chunk_text TEXT, is_active INTEGER DEFAULT 1
        );
        CREATE TABLE rag_relationships (
            id INTEGER PRIMARY KEY, source_document_id INTEGER NOT NULL,
            target_document_id INTEGER NOT NULL, relationship_type TEXT NOT NULL,
            match_status TEXT NOT NULL, relevance_score REAL NOT NULL DEFAULT 0,
            confidence REAL NOT NULL DEFAULT 0, reason TEXT, supporting_excerpt TEXT,
            evaluated_model TEXT
        );
        INSERT INTO users VALUES (8), (9);
        INSERT INTO tickets VALUES (4);
        INSERT INTO rag_documents (id, source_type, source_id, title) VALUES
            (1, 'tickets', '4', 'Ticket 4'),
            (2, 'knowledge_base', 'vpn', 'VPN'),
            (3, 'products', '12', 'Battery');
        INSERT INTO rag_relationships VALUES
            (10, 1, 2, 'KNOWN_ISSUE', 'MATCH', 0.9, 0.88, 'vpn', NULL, 'model-a'),
            (11, 1, 3, 'RELATED', 'MATCH', 0.7, 0.6, 'battery', NULL, 'model-a');
        """)
    database = Database()
    migration = Path("migrations/453_rag_relationship_feedback.sql").read_text(encoding="utf-8")
    for statement in database._split_sql_statements(database._adapt_sql_for_sqlite(migration)):
        await connection.execute(statement)
    await connection.commit()
    monkeypatch.setattr(repo.db, "_use_sqlite", True)
    monkeypatch.setattr(repo.db, "_sqlite_conn", connection)
    yield connection
    await connection.close()


@pytest.mark.anyio
async def test_downvote_hides_item_from_that_ticket_only(feedback_db):
    relationship = await repo.get_relationship_for_document(11, 1)
    await repo.save_relationship_feedback(relationship=relationship, ticket_id=4, user_id=8, rating="down")

    rows = await repo.list_relationship_evidence(1, limit=12, ticket_id=4, user_id=9)
    assert [row["relationship_id"] for row in rows] == [10]

    # Without a ticket context (e.g. the other document's view) nothing is hidden.
    unscoped = await repo.list_relationship_evidence(1, limit=12)
    assert {row["relationship_id"] for row in unscoped} == {10, 11}

    targets = await repo.relationships_for_targets(1, [2, 3], ticket_id=4)
    assert targets == {
        2: {"relationship_id": 10, "voted_down": False},
        3: {"relationship_id": 11, "voted_down": True},
    }


@pytest.mark.anyio
async def test_vote_is_replaced_and_cleared(feedback_db):
    relationship = await repo.get_relationship_for_document(10, 1)
    await repo.save_relationship_feedback(relationship=relationship, ticket_id=4, user_id=8, rating="down")
    await repo.save_relationship_feedback(relationship=relationship, ticket_id=4, user_id=8, rating="up")

    rows = await repo.list_relationship_evidence(1, limit=12, ticket_id=4, user_id=8)
    assert {row["relationship_id"]: row["my_rating"] for row in rows} == {10: "up", 11: None}

    await repo.save_relationship_feedback(relationship=relationship, ticket_id=4, user_id=8, rating=None)
    cursor = await feedback_db.execute("SELECT COUNT(*) FROM rag_relationship_feedback")
    assert (await cursor.fetchone())[0] == 0


@pytest.mark.anyio
async def test_relationship_lookup_rejects_unrelated_document(feedback_db):
    assert await repo.get_relationship_for_document(10, 3) is None
    assert (await repo.get_relationship_for_document(10, 2))["other_source_type"] == "tickets"


@pytest.mark.anyio
async def test_labels_export_snapshots_judgement_and_feeds_eval(feedback_db):
    for relationship_id, user_id, rating in ((10, 8, "up"), (11, 8, "up"), (11, 9, "down")):
        relationship = await repo.get_relationship_for_document(relationship_id, 1)
        await repo.save_relationship_feedback(
            relationship=relationship, ticket_id=4, user_id=user_id, rating=rating
        )
    # Re-evaluating the row later must not change the stored label.
    await feedback_db.execute("UPDATE rag_relationships SET relevance_score = 0.1, evaluated_model = 'model-b'")
    await feedback_db.commit()

    dataset = repo.build_related_feedback_dataset(await repo.list_relationship_feedback_labels())

    assert dataset["kind"] == "related_feedback"
    assert dataset["labels"] == [
        {"id": "rel:10", "relationship_type": "KNOWN_ISSUE", "relevance_score": 0.9, "confidence": 0.88,
         "evaluated_model": "model-a", "target_source_type": "knowledge_base", "relevant": True},
        {"id": "rel:11", "relationship_type": "RELATED", "relevance_score": 0.7, "confidence": 0.6,
         "evaluated_model": "model-a", "target_source_type": "products", "relevant": False},
    ]
    assert "battery" not in json.dumps(dataset).lower()
    assert evaluate_related_feedback(dataset["labels"], min_score=0.8)["precision"] == 1.0
    assert evaluate_related_feedback(dataset["labels"])["precision"] == 0.5


def test_related_feedback_fixture_threshold_tradeoffs():
    labels = json.loads(Path("evals/ai_quality/related_feedback_v1.json").read_text())["labels"]
    loose = evaluate_related_feedback(labels)
    strict = evaluate_related_feedback(labels, min_score=0.7)
    assert loose["precision"] < strict["precision"] == 1.0
    assert strict["rejected_suppression"] == 1.0
    assert strict["relevant_retention"] == 1.0
    assert evaluate_related_feedback(labels, min_confidence=0.8)["relevant_retention"] == 0.5


class _Request:
    def __init__(self, payload):
        self._payload = payload
        self.state = SimpleNamespace()

    async def json(self):
        return self._payload


@pytest.fixture
def route_env(monkeypatch):
    calls = []

    async def require(_request):
        return {"id": 8}, None

    async def get_ticket(ticket_id):
        return {"id": ticket_id}

    async def document_id(_ticket_id):
        return 1

    async def relationship_for(relationship_id, document_id):
        return {"id": relationship_id} if relationship_id == 10 else None

    async def save(**kwargs):
        calls.append(kwargs)

    monkeypatch.setattr(admin_routes, "_main", lambda: SimpleNamespace(_require_helpdesk_page=require))
    monkeypatch.setattr(admin_routes.tickets_repo, "get_ticket", get_ticket)
    monkeypatch.setattr(admin_routes, "_ticket_rag_document_id", document_id)
    monkeypatch.setattr(admin_routes.rag_relationship_repo, "get_relationship_for_document", relationship_for)
    monkeypatch.setattr(admin_routes.rag_relationship_repo, "save_relationship_feedback", save)

    async def record(**kwargs):
        calls.append({"audit": kwargs["action"], "metadata": kwargs["metadata"]})

    monkeypatch.setattr(admin_routes.audit_service, "record", record)
    return calls


@pytest.mark.anyio
async def test_feedback_route_records_vote(route_env):
    response = await admin_routes.admin_ticket_related_feedback(4, 10, _Request({"rating": "down"}))
    assert json.loads(response.body) == {"relationship_id": 10, "rating": "down", "hidden": True}
    assert route_env == [
        {"relationship": {"id": 10}, "ticket_id": 4, "user_id": 8, "rating": "down"},
        {"audit": "tickets.related.feedback", "metadata": {"relationship_id": 10, "rating": "down"}},
    ]


@pytest.mark.anyio
@pytest.mark.parametrize(
    "relationship_id, payload, expected",
    [(10, {"rating": "meh"}, 422), (10, ["down"], 400), (99, {"rating": "up"}, 404)],
)
async def test_feedback_route_rejects_bad_input(route_env, relationship_id, payload, expected):
    with pytest.raises(HTTPException) as excinfo:
        await admin_routes.admin_ticket_related_feedback(4, relationship_id, _Request(payload))
    assert excinfo.value.status_code == expected
    assert route_env == []
