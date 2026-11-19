"""Rated examples and lessons for AI text features."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import aiosqlite
import pytest

from app.core.database import Database
from app.repositories import ai_quality as repo

MIGRATIONS = (
    "migrations/563_ai_quality_outputs.sql",
    "migrations/565_ai_link_learning.sql",
    "migrations/566_ai_feedback_lessons.sql",
)
LONG_AGO = datetime(2000, 1, 1, tzinfo=timezone.utc)


@pytest.fixture
async def text_db(monkeypatch):
    connection = await aiosqlite.connect(":memory:")
    connection.row_factory = aiosqlite.Row
    await connection.executescript(
        "PRAGMA foreign_keys = ON; CREATE TABLE users (id INTEGER PRIMARY KEY);"
        " INSERT INTO users VALUES (1), (5), (6), (7);"
        " CREATE TABLE tickets (id INTEGER PRIMARY KEY, company_id INTEGER);"
        " INSERT INTO tickets VALUES (7, 2), (8, 2), (9, 3), (10, 2);"
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


async def _summary(ticket_id, company_id, text):
    return await repo.record_output(feature="ticket_ai_summary", user_id=None, company_id=company_id,
                                    subject_type="ticket", subject_id=ticket_id, output_text=text)


async def _vote(output_id, user_id, rating, reason=None, technician=True):
    await repo.save_feedback(response_id=output_id, user_id=user_id, rating=rating, reason=reason, comment=None,
                             is_technician=technician)


async def _examples(company_id, *, rating="up", exclude=None, limit=5):
    rows = await repo.rated_examples("ticket_ai_summary", company_id, exclude_subject_id=exclude, rating=rating,
                                     limit=limit, since=LONG_AGO)
    return [row["output_text"] for row in rows]


@pytest.mark.anyio
async def test_rated_examples_stay_in_the_company(text_db):
    await _vote(await _summary(7, 2, "Company two summary"), 5, "up")
    await _vote(await _summary(9, 3, "Company three summary"), 5, "up")
    assert await _examples(2) == ["Company two summary"]
    assert await _examples(3) == ["Company three summary"]


@pytest.mark.anyio
async def test_rated_examples_exclude_the_current_ticket_customers_and_ignored_votes(text_db):
    await _vote(await _summary(7, 2, "This ticket"), 5, "up")
    await _vote(await _summary(8, 2, "Customer liked"), 6, "up", technician=False)
    ignored = await _summary(10, 2, "Ignored vote")
    await _vote(ignored, 5, "up")
    await text_db.execute("UPDATE ai_output_feedback SET learning_ignored_at = '2026-01-01' WHERE response_id = ?",
                          (ignored,))
    await text_db.commit()
    assert await _examples(2, exclude="7") == []


@pytest.mark.anyio
async def test_rated_examples_newest_first_limited_and_by_rating(text_db):
    older = await _summary(7, 2, "Older")
    newer = await _summary(8, 2, "Newer")
    bad = await _summary(10, 2, "Bad")
    await _vote(older, 5, "up")
    await _vote(newer, 5, "up")
    await _vote(bad, 5, "down", reason="incomplete")
    await text_db.execute("UPDATE ai_output_feedback SET updated_at = '2026-01-01 00:00:00' WHERE response_id = ?",
                          (older,))
    await text_db.commit()
    assert await _examples(2, limit=1) == ["Newer"]
    assert await _examples(2) == ["Newer", "Older"]
    rows = await repo.rated_examples("ticket_ai_summary", 2, exclude_subject_id=None, rating="down", limit=5,
                                     since=LONG_AGO)
    assert [(row["output_text"], row["reason"]) for row in rows] == [("Bad", "incomplete")]


@pytest.mark.anyio
async def test_rated_examples_respect_since(text_db):
    await _vote(await _summary(7, 2, "Old"), 5, "up")
    rows = await repo.rated_examples("ticket_ai_summary", 2, exclude_subject_id=None, rating="up", limit=5,
                                     since=datetime.now(timezone.utc) + timedelta(days=1))
    assert rows == []


@pytest.mark.anyio
async def test_downvote_reason_counts(text_db):
    for ticket_id, user_id, reason in ((7, 5, "incomplete"), (8, 5, "incomplete"), (10, 5, "incorrect"),
                                       (10, 6, "incomplete")):
        await _vote(await _summary(ticket_id, 2, f"Summary {ticket_id}"), user_id, "down", reason=reason,
                    technician=user_id == 5)
    await _vote(await _summary(9, 3, "Fine"), 5, "up")
    rows = await repo.downvote_reason_counts(LONG_AGO)
    assert sorted((r["feature"], r["reason"], int(r["votes"]), int(r["feature_total"])) for r in rows) == [
        ("ticket_ai_summary", "incomplete", 2, 3),
        ("ticket_ai_summary", "incorrect", 1, 3),
    ]


@pytest.mark.anyio
async def test_replace_lessons_keeps_ignored_and_deactivates_missing(text_db):
    await repo.replace_lessons([
        {"feature": "ticket_ai_summary", "reason": "incomplete", "votes": 4, "share": 0.5, "active": True},
        {"feature": "ticket_ai_summary", "reason": "incorrect", "votes": 3, "share": 0.3, "active": True},
    ])
    assert await repo.set_lesson_ignored("ticket_ai_summary", "incomplete", ignored=True) == 1
    await repo.replace_lessons([
        {"feature": "ticket_ai_summary", "reason": "incomplete", "votes": 5, "share": 0.6, "active": True},
    ])
    lessons = {row["reason"]: row for row in await repo.list_lessons()}
    assert set(lessons) == {"incomplete"}  # incorrect no longer active
    assert lessons["incomplete"]["ignored"] is True and int(lessons["incomplete"]["votes"]) == 5
    assert await repo.active_lessons("ticket_ai_summary") == []  # ignored lessons are not used
    await repo.set_lesson_ignored("ticket_ai_summary", "incomplete", ignored=False)
    assert [row["reason"] for row in await repo.active_lessons("ticket_ai_summary")] == ["incomplete"]


@pytest.mark.anyio
async def test_prune_orphan_ticket_outputs(text_db):
    gone = await _summary(10, 2, "Ticket deleted")
    kept = await _summary(7, 2, "Ticket kept")
    await _vote(gone, 5, "up")
    await text_db.execute("DELETE FROM tickets WHERE id = 10")
    await text_db.commit()
    assert await repo.prune_orphan_ticket_outputs() == 1
    assert await repo.get_response(gone) is None
    assert await repo.get_response(kept) is not None
    cursor = await text_db.execute("SELECT COUNT(*) FROM ai_output_feedback")
    assert (await cursor.fetchone())[0] == 0
