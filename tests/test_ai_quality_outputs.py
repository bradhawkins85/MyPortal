"""Recording any AI output and the 👍/👎 votes on it."""
from __future__ import annotations

from pathlib import Path

import aiosqlite
import pytest

from app.core.database import Database
from app.repositories import ai_quality as repo

MIGRATIONS = (
    "migrations/387_ai_quality_feedback.sql",
    "migrations/563_ai_quality_outputs.sql",
    "migrations/565_ai_link_learning.sql",
)


@pytest.fixture
async def quality_db(monkeypatch):
    connection = await aiosqlite.connect(":memory:")
    connection.row_factory = aiosqlite.Row
    await connection.executescript(
        "PRAGMA foreign_keys = ON; CREATE TABLE users (id INTEGER PRIMARY KEY);"
        " INSERT INTO users VALUES (1), (3), (5), (6);"
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
    assert await _rows(quality_db, "SELECT rating, reason, comment, is_technician FROM ai_output_feedback") == [
        ("down", "unrelated", None, 1)
    ]
    await repo.save_feedback(response_id=response_id, user_id=5, rating=None, reason=None, comment=None)
    assert await _rows(quality_db, "SELECT id FROM ai_output_feedback") == []


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


@pytest.mark.anyio
async def test_aggregate_by_feature(quality_db):
    summary = await repo.record_output(feature="ticket_ai_summary", user_id=1, company_id=2,
                                       subject_type="ticket", subject_id=7, output_text="a")
    other = await repo.record_output(feature="ticket_ai_summary", user_id=1, company_id=2,
                                     subject_type="ticket", subject_id=8, output_text="b")
    link = await repo.record_output(feature="rag_relationships", user_id=1, company_id=2,
                                    subject_type="ticket", subject_id=7, target_type="knowledge_base", target_id="vpn")
    await repo.record_output(feature="product_descriptions", user_id=1, company_id=None, output_text="unrated")
    await repo.save_feedback(response_id=summary, user_id=5, rating="down", reason="incomplete", comment=None,
                             is_technician=True)
    await repo.save_feedback(response_id=summary, user_id=6, rating="up", reason=None, comment=None)
    await repo.save_feedback(response_id=other, user_id=5, rating="down", reason="incomplete", comment=None,
                             is_technician=True)
    await repo.save_feedback(response_id=link, user_id=5, rating="down", reason="unrelated", comment=None,
                             is_technician=True)

    groups = {row["feature"]: row for row in await repo.aggregate_by_feature()}

    summary_row = groups["ticket_ai_summary"]
    assert (summary_row["outputs"], summary_row["helpful"], summary_row["unhelpful"]) == (2, 1, 2)
    assert (summary_row["technician_votes"], summary_row["customer_votes"]) == (2, 1)
    assert summary_row["top_reason"] == "incomplete"
    assert summary_row["last_rated_at"].endswith("+00:00")
    assert groups["rag_relationships"]["top_reason"] == "unrelated"
    unrated = groups["product_descriptions"]
    assert (unrated["outputs"], unrated["helpful"], unrated["unhelpful"]) == (1, 0, 0)
    assert unrated["top_reason"] is None and unrated["last_rated_at"] is None
    assert [row["feature"] for row in await repo.aggregate_by_feature()][0] == "ticket_ai_summary"


@pytest.mark.anyio
async def test_rating_overview(quality_db):
    from datetime import datetime, timedelta, timezone

    assert await repo.rating_overview(since=datetime.now(timezone.utc) - timedelta(days=30)) == {
        "ratings_since": 0, "helpful_pct": None, "features_rated": 0,
    }
    first = await repo.record_output(feature="ticket_ai_summary", user_id=1, company_id=2, output_text="a")
    second = await repo.record_output(feature="agent", user_id=1, company_id=2, output_text="b")
    await repo.save_feedback(response_id=first, user_id=5, rating="up", reason=None, comment=None)
    await repo.save_feedback(response_id=first, user_id=6, rating="up", reason=None, comment=None)
    await repo.save_feedback(response_id=second, user_id=5, rating="down", reason=None, comment=None)
    overview = await repo.rating_overview(since=datetime.now(timezone.utc) - timedelta(days=30))
    assert overview == {"ratings_since": 3, "helpful_pct": 67, "features_rated": 2}
    future = await repo.rating_overview(since=datetime.now(timezone.utc) + timedelta(days=1))
    assert future["ratings_since"] == 0


def test_new_tables_use_the_same_collation_as_rag_documents():
    # MariaDB refuses to compare utf8mb4 columns with different collations
    # (error 1267); the backfill compares these columns with rag_documents.
    sql = Path("migrations/563_ai_quality_outputs.sql").read_text(encoding="utf-8")
    for table in ("ai_outputs", "ai_output_feedback"):
        definition = sql.split("CREATE TABLE IF NOT EXISTS " + table, 1)[1].split(";", 1)[0]
        assert "COLLATE=utf8mb4_unicode_ci" in definition, table


def test_backfill_never_compares_strings_from_cast():
    sql = Path("migrations/564_ai_quality_related_backfill.sql").read_text(encoding="utf-8")
    assert "= CAST(" not in sql.replace(" ", " ")


@pytest.mark.anyio
async def test_deleting_the_output_owner_keeps_everyone_elses_votes(quality_db):
    response_id = await repo.record_output(feature="rag_relationships", user_id=1, company_id=2,
                                           subject_type="ticket", subject_id=7, target_type="knowledge_base",
                                           target_id="vpn")
    await repo.save_feedback(response_id=response_id, user_id=5, rating="down", reason=None, comment=None,
                             is_technician=True)
    await quality_db.execute("DELETE FROM users WHERE id = 1")
    await quality_db.commit()
    assert (await repo.get_response(response_id))["user_id"] is None
    assert await repo.response_counts(response_id) == {"helpful": 0, "unhelpful": 1}


@pytest.mark.anyio
async def test_record_output_survives_a_concurrent_insert(quality_db, monkeypatch):
    first = await repo.record_output(feature="ticket_ai_summary", user_id=1, company_id=2,
                                     subject_type="ticket", subject_id=7, output_text="same")
    real_fetch_one = repo.db.fetch_one
    calls = {"n": 0}

    async def miss_first_lookup(sql, params=None):
        calls["n"] += 1
        if calls["n"] == 1:
            return None  # another request inserted between our lookup and insert
        return await real_fetch_one(sql, params)

    monkeypatch.setattr(repo.db, "fetch_one", miss_first_lookup)
    again = await repo.record_output(feature="ticket_ai_summary", user_id=3, company_id=2,
                                     subject_type="ticket", subject_id=7, output_text="same")
    assert again == first
    assert await _rows(quality_db, "SELECT COUNT(*) FROM ai_outputs") == [(1,)]


@pytest.mark.anyio
async def test_each_ai_search_answer_is_its_own_output(quality_db):
    ids = {
        await repo.record_response(user_id=1, company_id=2, feature="agent", query="vpn", evidence={},
                                   model=None, latency_ms=5, confidence_band=None, outcome="answered")
        for _ in range(2)
    }
    assert len(ids) == 2


@pytest.mark.anyio
async def test_user_rating(quality_db):
    response_id = await repo.record_output(feature="ticket_ai_summary", user_id=1, company_id=2,
                                           subject_type="ticket", subject_id=7, output_text="x")
    assert await repo.user_rating(response_id, 5) is None
    await repo.save_feedback(response_id=response_id, user_id=5, rating="down", reason=None, comment=None)
    assert await repo.user_rating(response_id, 5) == "down"
