"""Technician link votes as lessons, and undoing them."""
from __future__ import annotations

from pathlib import Path

import aiosqlite
import pytest

from app.core.database import Database
from app.repositories import ai_quality as repo

MIGRATIONS = ("migrations/563_ai_quality_outputs.sql", "migrations/565_ai_link_learning.sql")


@pytest.fixture
async def link_db(monkeypatch):
    connection = await aiosqlite.connect(":memory:")
    connection.row_factory = aiosqlite.Row
    await connection.executescript(
        "PRAGMA foreign_keys = ON; CREATE TABLE users (id INTEGER PRIMARY KEY);"
        " INSERT INTO users VALUES (1), (5), (6), (7);"
        " CREATE TABLE rag_documents (id INTEGER PRIMARY KEY, source_type TEXT, source_id TEXT, title TEXT);"
        " INSERT INTO rag_documents VALUES (2, 'knowledge_base', 'vpn', 'VPN setup');"
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


async def _link(ticket_id, target_id="vpn", target_type="knowledge_base", feature="rag_relationships"):
    return await repo.record_output(feature=feature, user_id=1, company_id=2, subject_type="ticket",
                                    subject_id=ticket_id, target_type=target_type, target_id=target_id)


async def _vote(output_id, user_id, rating, technician=True):
    await repo.save_feedback(response_id=output_id, user_id=user_id, rating=rating, reason=None, comment=None,
                             is_technician=technician)


@pytest.mark.anyio
async def test_list_link_votes_only_returns_teaching_votes(link_db):
    teaching = await _link(7)
    await _vote(teaching, 5, "down")
    await _vote(teaching, 6, "down", technician=False)
    other_feature = await repo.record_output(feature="ticket_ai_summary", user_id=1, company_id=2,
                                             subject_type="ticket", subject_id=7, output_text="x")
    await _vote(other_feature, 5, "down")
    ignored = await _link(8)
    await _vote(ignored, 5, "down")
    await link_db.execute("UPDATE ai_output_feedback SET learning_ignored_at = '2026-11-01 00:00:00' "
                          "WHERE response_id = ?", (ignored,))
    await link_db.commit()

    votes = await repo.list_link_votes()

    assert [(v["output_id"], v["subject_id"], v["target_type"], v["target_id"], v["rating"]) for v in votes] == [
        (teaching, "7", "knowledge_base", "vpn", "down")
    ]
    assert votes[0]["voted_at"]


@pytest.mark.anyio
async def test_undo_and_restore(link_db):
    for ticket_id, user_id in ((7, 5), (8, 6)):
        await _vote(await _link(ticket_id), user_id, "down")

    assert await repo.set_link_learning_ignored("knowledge_base", "vpn", ignored=True) == 2
    assert await repo.list_link_votes() == []

    await _vote(await _link(9), 7, "down")
    assert [v["subject_id"] for v in await repo.list_link_votes()] == ["9"]

    assert await repo.set_link_learning_ignored("knowledge_base", "vpn", ignored=False) == 2
    assert sorted(v["subject_id"] for v in await repo.list_link_votes()) == ["7", "8", "9"]


@pytest.mark.anyio
async def test_learned_links_summary(link_db):
    await _vote(await _link(7), 5, "down")
    await _vote(await _link(8), 6, "down")
    await _vote(await _link(7, target_type="products", target_id="12"), 5, "up")
    await _vote(await _link(10, target_type="products", target_id="99"), 6, "down", technician=False)

    rows = {row["target_id"]: row for row in await repo.learned_links()}

    assert set(rows) == {"vpn", "12"}
    vpn = rows["vpn"]
    assert (vpn["title"], vpn["unhelpful"], vpn["helpful"], vpn["tickets"], vpn["ignored"]) == (
        "VPN setup", 2, 0, 2, False)
    assert vpn["last_voted_at"].endswith("+00:00")
    assert (rows["12"]["title"], rows["12"]["helpful"]) == (None, 1)
    assert [row["target_id"] for row in await repo.learned_links()][0] == "vpn"

    await repo.set_link_learning_ignored("knowledge_base", "vpn", ignored=True)
    assert {row["target_id"]: row["ignored"] for row in await repo.learned_links()} == {"vpn": True, "12": False}


@pytest.mark.anyio
async def test_store_subject_embedding_round_trips(link_db):
    output_id = await _link(7)
    await _vote(output_id, 5, "down")
    await repo.store_subject_embedding(output_id, [0.6, 0.8], "model-x")
    vote = (await repo.list_link_votes())[0]
    assert vote["subject_embedding_model"] == "model-x"
    assert vote["subject_embedding_json"] == "[0.6, 0.8]"


@pytest.mark.anyio
async def test_changing_a_vote_after_undo_counts_again(link_db):
    output_id = await _link(7)
    await _vote(output_id, 5, "down")
    await repo.set_link_learning_ignored("knowledge_base", "vpn", ignored=True)
    await _vote(output_id, 5, "up")
    assert [v["rating"] for v in await repo.list_link_votes()] == ["up"]


@pytest.mark.anyio
async def test_undo_and_restore_keep_the_vote_time(link_db, monkeypatch):
    # MariaDB stamps updated_at ON UPDATE; SQLite does not, so check the SQL
    # keeps it explicitly — updated_at is the vote time used for decay.
    statements = []
    real = repo.db.execute_rowcount

    async def spy(sql, params=None):
        statements.append(sql)
        return await real(sql, params)

    monkeypatch.setattr(repo.db, "execute_rowcount", spy)
    await _vote(await _link(7), 5, "down")
    await repo.set_link_learning_ignored("knowledge_base", "vpn", ignored=True)
    await repo.set_link_learning_ignored("knowledge_base", "vpn", ignored=False)
    assert len(statements) == 2
    assert all("updated_at = updated_at" in sql for sql in statements)


@pytest.mark.anyio
async def test_learned_links_counts_survive_an_embedding_model_change(link_db):
    # rag_documents keeps one row per embedding model; a second row must not double the counts.
    await link_db.execute("INSERT INTO rag_documents VALUES (3, 'knowledge_base', 'vpn', 'VPN setup')")
    await link_db.commit()
    await _vote(await _link(7), 5, "down")
    (vpn,) = await repo.learned_links()
    assert (vpn["unhelpful"], vpn["title"]) == (1, "VPN setup")
