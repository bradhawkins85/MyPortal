"""Turning technician link votes into score adjustments."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import aiosqlite
import pytest

from app.repositories import rag_relationships as rel_repo
from app.services import ai_learning
from app.services.ai_learning import LinkLesson, link_adjustment

NOW = datetime(2026, 11, 19, tzinfo=timezone.utc)
SAME = (1.0, 0.0)
OTHER = (0.6, 0.8)  # cosine 0.6 with SAME


def _lessons(vote, count, embedding=SAME, voted_at=NOW):
    return [LinkLesson("knowledge_base", "vpn", vote, embedding, voted_at) for _ in range(count)]


@pytest.mark.parametrize("vote,count,expected", [
    (-1, 1, -0.05),
    (-1, 4, -0.2),
    (-1, 10, -0.3),
    (1, 6, 0.15),
])
def test_votes_on_the_same_kind_of_ticket(vote, count, expected):
    assert link_adjustment(_lessons(vote, count), SAME, now=NOW) == expected


def test_votes_on_unrelated_tickets_change_nothing():
    assert link_adjustment(_lessons(-1, 10, embedding=OTHER), SAME, now=NOW) == 0.0


def test_old_votes_count_half_after_half_life():
    assert link_adjustment(_lessons(-1, 1, voted_at=NOW - timedelta(days=180)), SAME, now=NOW) == -0.025


def test_mismatched_or_missing_embeddings_are_ignored():
    assert link_adjustment(_lessons(-1, 3, embedding=(1.0, 0.0, 0.0)), SAME, now=NOW) == 0.0
    assert link_adjustment(_lessons(-1, 3, embedding=None), SAME, now=NOW) == 0.0
    assert link_adjustment(_lessons(-1, 3), None, now=NOW) == 0.0


def test_opposite_votes_cancel():
    assert link_adjustment(_lessons(-1, 1) + _lessons(1, 1), SAME, now=NOW) == 0.0


@pytest.fixture
def votes(monkeypatch):
    state = {"calls": 0, "stored": [], "rows": [
        {"output_id": 1, "subject_type": "ticket", "subject_id": "7", "target_type": "knowledge_base",
         "target_id": "vpn", "subject_embedding_json": None, "subject_embedding_model": None,
         "rating": "down", "voted_at": "2026-11-18 10:00:00"},
        {"output_id": 2, "subject_type": "ticket", "subject_id": "8", "target_type": "knowledge_base",
         "target_id": "vpn", "subject_embedding_json": json.dumps([0.0, 1.0]), "subject_embedding_model": "m1",
         "rating": "up", "voted_at": "2026-11-18 11:00:00"},
        {"output_id": 3, "subject_type": "ticket", "subject_id": "9", "target_type": "products",
         "target_id": "12", "subject_embedding_json": None, "subject_embedding_model": None,
         "rating": "down", "voted_at": "2026-11-18 12:00:00"},
    ]}

    async def list_link_votes():
        state["calls"] += 1
        return state["rows"]

    async def store(output_id, embedding, model):
        state["stored"].append((output_id, embedding, model))

    async def get_document_by_source(source_type, source_id, model):
        return {"id": 70} if (source_type, source_id) == ("tickets", "7") else None

    async def mean_embedding(document_id, *, embedding_model=None):
        return [1.0, 0.0] if document_id == 70 else None

    monkeypatch.setattr(ai_learning.quality_repo, "list_link_votes", list_link_votes)
    monkeypatch.setattr(ai_learning.quality_repo, "store_subject_embedding", store)
    monkeypatch.setattr(ai_learning.rag_index_repo, "get_document_by_source", get_document_by_source)
    monkeypatch.setattr(ai_learning.rel_repo, "document_mean_embedding", mean_embedding)
    monkeypatch.setattr(ai_learning, "_current_embedding_model", lambda: "m1")
    ai_learning.invalidate_cache()
    yield state
    ai_learning.invalidate_cache()


@pytest.mark.anyio
async def test_load_link_lessons_groups_and_fills_embeddings(votes):
    lessons = await ai_learning.load_link_lessons()

    vpn = lessons[("knowledge_base", "vpn")]
    assert [(lesson.vote, lesson.embedding) for lesson in vpn] == [(-1, (1.0, 0.0)), (1, (0.0, 1.0))]
    assert vpn[0].voted_at == datetime(2026, 11, 18, 10, tzinfo=timezone.utc)
    assert lessons[("products", "12")][0].embedding is None  # ticket 9 has no RAG document
    assert votes["stored"] == [(1, [1.0, 0.0], "m1")]


@pytest.mark.anyio
async def test_lessons_are_cached_until_invalidated(votes):
    await ai_learning.load_link_lessons()
    await ai_learning.load_link_lessons()
    assert votes["calls"] == 1
    ai_learning.invalidate_cache()
    await ai_learning.load_link_lessons()
    assert votes["calls"] == 2


@pytest.mark.anyio
async def test_stale_embedding_model_is_recomputed(votes, monkeypatch):
    votes["rows"][1]["subject_embedding_model"] = "old-model"
    votes["rows"][1]["subject_id"] = "7"
    await ai_learning.load_link_lessons()
    assert (2, [1.0, 0.0], "m1") in votes["stored"]


@pytest.mark.anyio
async def test_adjustment_for_never_raises(monkeypatch):
    async def broken():
        raise RuntimeError("database unavailable")

    monkeypatch.setattr(ai_learning.quality_repo, "list_link_votes", broken)
    ai_learning.invalidate_cache()
    assert await ai_learning.adjustment_for("knowledge_base", "vpn", [1.0, 0.0]) == 0.0
    assert await ai_learning.lessons_or_empty() == {}


@pytest.mark.anyio
async def test_adjustment_for_uses_lessons(votes):
    # Ticket 7's lesson is a 👎 identical to the subject; ticket 8's 👍 is orthogonal.
    for row in votes["rows"]:
        row["voted_at"] = datetime.now(timezone.utc)
    assert await ai_learning.adjustment_for("knowledge_base", "vpn", [1.0, 0.0]) == -0.05


@pytest.mark.anyio
async def test_document_mean_embedding(monkeypatch):
    connection = await aiosqlite.connect(":memory:")
    connection.row_factory = aiosqlite.Row
    await connection.executescript(
        "CREATE TABLE rag_chunks (id INTEGER PRIMARY KEY, document_id INTEGER, embedding_json TEXT,"
        " embedding_model TEXT, is_active INTEGER);"
        " INSERT INTO rag_chunks VALUES (1, 5, '[1.0, 0.0]', 'm1', 1), (2, 5, '[0.0, 1.0]', 'm1', 1),"
        " (3, 5, '[5.0, 5.0]', 'old', 1), (4, 5, '[9.0, 0.0]', 'm1', 0);"
    )
    monkeypatch.setattr(rel_repo.db, "_use_sqlite", True)
    monkeypatch.setattr(rel_repo.db, "_sqlite_conn", connection)
    try:
        vector = await rel_repo.document_mean_embedding(5, embedding_model="m1")
        assert [round(value, 4) for value in vector] == [0.7071, 0.7071]
        assert await rel_repo.document_mean_embedding(6, embedding_model="m1") is None
    finally:
        await connection.close()


@pytest.mark.anyio
@pytest.mark.parametrize("rating", ["down", None])
async def test_saving_a_vote_clears_the_lesson_cache(monkeypatch, rating):
    from unittest.mock import AsyncMock

    from app.repositories import ai_quality as quality_repo

    monkeypatch.setattr(quality_repo.db, "fetch_one", AsyncMock(return_value=None))
    monkeypatch.setattr(quality_repo.db, "execute", AsyncMock())
    ai_learning._cache = {("knowledge_base", "vpn"): []}
    await quality_repo.save_feedback(response_id=1, user_id=5, rating=rating, reason=None, comment=None,
                                     is_technician=True)
    assert ai_learning._cache is None
