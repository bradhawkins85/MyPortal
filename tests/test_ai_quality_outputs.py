"""Recording any AI output and the 👍/👎 votes on it."""
from __future__ import annotations

from pathlib import Path

import aiosqlite
import pytest

from app.core.database import Database
from app.repositories import ai_quality as repo

MIGRATIONS = ("migrations/387_ai_quality_feedback.sql", "migrations/563_ai_quality_outputs.sql")


@pytest.fixture
async def quality_db(monkeypatch):
    connection = await aiosqlite.connect(":memory:")
    connection.row_factory = aiosqlite.Row
    await connection.executescript(
        "CREATE TABLE users (id INTEGER PRIMARY KEY); INSERT INTO users VALUES (1), (3), (5), (6);"
    )
    database = Database()
    for path in MIGRATIONS:
        sql = Path(path).read_text(encoding="utf-8")
        for statement in database._split_sql_statements(database._adapt_sql_for_sqlite(sql)):
            await connection.execute(statement)
    await connection.commit()
    monkeypatch.setattr(repo.db, "_use_sqlite", True)
    monkeypatch.setattr(repo.db, "_sqlite_conn", connection)
    yield connection
    await connection.close()


async def _rows(connection, sql):
    cursor = await connection.execute(sql)
    return [tuple(row) for row in await cursor.fetchall()]


@pytest.mark.anyio
async def test_record_output_dedupes_same_output(quality_db):
    first = await repo.record_output(feature="ticket_ai_summary", user_id=1, company_id=2,
                                     subject_type="ticket", subject_id=7, output_text="Printer offline")
    again = await repo.record_output(feature="ticket_ai_summary", user_id=3, company_id=2,
                                     subject_type="ticket", subject_id=7, output_text="Printer offline")
    changed = await repo.record_output(feature="ticket_ai_summary", user_id=1, company_id=2,
                                       subject_type="ticket", subject_id=7, output_text="Printer back online")
    assert first == again
    assert changed != first
    stored = await repo.get_response(first)
    assert (stored["subject_type"], stored["subject_id"], stored["output_text"]) == ("ticket", "7", "Printer offline")


@pytest.mark.anyio
async def test_record_output_keeps_links_to_different_targets_apart(quality_db):
    a = await repo.record_output(feature="rag_relationships", user_id=1, company_id=2, subject_type="ticket",
                                 subject_id=7, target_type="knowledge_base", target_id="vpn")
    b = await repo.record_output(feature="rag_relationships", user_id=1, company_id=2, subject_type="ticket",
                                 subject_id=7, target_type="knowledge_base", target_id="printer")
    assert a != b


@pytest.mark.anyio
async def test_record_output_rejects_unknown_feature(quality_db):
    with pytest.raises(ValueError):
        await repo.record_output(feature="nope", user_id=1, company_id=None)


@pytest.mark.anyio
async def test_feedback_upsert_and_clear(quality_db):
    response_id = await repo.record_output(feature="ticket_ai_summary", user_id=1, company_id=2,
                                           subject_type="ticket", subject_id=7, output_text="x")
    await repo.save_feedback(response_id=response_id, user_id=5, rating="up", reason=None, comment=None,
                             is_technician=True)
    await repo.save_feedback(response_id=response_id, user_id=5, rating="down", reason="unrelated",
                             comment=" ", is_technician=True)
    assert await _rows(quality_db, "SELECT rating, reason, comment, is_technician FROM ai_quality_feedback") == [
        ("down", "unrelated", None, 1)
    ]
    await repo.save_feedback(response_id=response_id, user_id=5, rating=None, reason=None, comment=None)
    assert await _rows(quality_db, "SELECT id FROM ai_quality_feedback") == []


def test_unrelated_is_a_known_reason():
    assert "unrelated" in repo.REASONS


@pytest.mark.anyio
async def test_ai_search_answers_are_recorded(quality_db):
    # Regression: record_response called a non-existent db method, so the agent
    # route's try/except swallowed every failure and nothing was ever stored.
    response_id = await repo.record_response(
        user_id=1, company_id=2, feature="agent", query="vpn down for bob@example.com",
        evidence={"knowledge_base": [{"source_id": 4}]}, model="ollama:llama3", latency_ms=120,
        confidence_band="high", outcome="answered",
    )
    stored = await repo.get_response(response_id)
    assert stored["feature"] == "agent"
    assert await repo.response_owned_by(response_id, 1)


@pytest.mark.anyio
async def test_response_counts(quality_db):
    response_id = await repo.record_output(feature="ticket_ai_summary", user_id=1, company_id=2,
                                           subject_type="ticket", subject_id=7, output_text="x")
    assert await repo.response_counts(response_id) == {"helpful": 0, "unhelpful": 0}
    for user_id, rating in ((1, "up"), (3, "down"), (5, "down")):
        await repo.save_feedback(response_id=response_id, user_id=user_id, rating=rating, reason=None, comment=None)
    assert await repo.response_counts(response_id) == {"helpful": 1, "unhelpful": 2}
