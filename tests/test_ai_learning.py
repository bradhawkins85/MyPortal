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

    async def list_link_votes(*, since=None):
        import asyncio

        await asyncio.sleep(0)  # a real query yields to other requests
        state["calls"] += 1
        state["since"] = since
        return state["rows"]

    async def store(output_id, embedding, model):
        state["stored"].append((output_id, embedding, model))

    async def get_document_by_source(source_type, source_id, model):
        state.setdefault("doc_lookups", []).append(source_id)
        return {"id": 70} if (source_type, source_id) == ("tickets", "7") else None

    async def mean_embedding(document_id, *, embedding_model=None):
        return [1.0, 0.0] if document_id == 70 else None

    monkeypatch.setattr(ai_learning.quality_repo, "list_link_votes", list_link_votes)
    monkeypatch.setattr(ai_learning.quality_repo, "store_subject_embedding", store)
    monkeypatch.setattr(ai_learning.rag_index_repo, "get_document_by_source", get_document_by_source)
    monkeypatch.setattr(ai_learning.rel_repo, "document_mean_embedding", mean_embedding)
    monkeypatch.setattr(ai_learning, "_current_embedding_model", lambda: "m1")
    ai_learning.invalidate_cache()
    ai_learning._missing_embeddings.clear()
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
    async def broken(*, since=None):
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


def test_repositories_do_not_import_the_learning_service():
    # Repositories stay below services; importing ai_learning from one creates an import cycle.
    from pathlib import Path

    import re

    pattern = re.compile(r"^\s*(from\s+\S+\s+)?import\s+.*\bai_learning\b|^\s*from\s+\S*ai_learning\b", re.M)
    for path in Path("app/repositories").glob("*.py"):
        assert not pattern.search(path.read_text(encoding="utf-8")), path


@pytest.mark.anyio
async def test_concurrent_reloads_share_one_load(votes):
    import asyncio

    await asyncio.gather(*(ai_learning.load_link_lessons() for _ in range(5)))
    assert votes["calls"] == 1


@pytest.mark.anyio
async def test_only_votes_from_the_last_three_half_lives_are_loaded(votes):
    await ai_learning.load_link_lessons()
    expected = datetime.now(timezone.utc) - timedelta(days=3 * ai_learning.HALF_LIFE_DAYS)
    assert abs((votes["since"] - expected).total_seconds()) < 60


@pytest.mark.anyio
async def test_tickets_without_a_rag_document_are_not_looked_up_on_every_reload(votes):
    await ai_learning.load_link_lessons()
    ai_learning.invalidate_cache()
    await ai_learning.load_link_lessons()
    assert votes["doc_lookups"].count("9") == 1
