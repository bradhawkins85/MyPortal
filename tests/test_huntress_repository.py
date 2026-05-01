"""Tests for the Huntress snapshot repository helpers."""
from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock

import pytest


@pytest.mark.asyncio
async def test_upsert_edr_inserts_when_no_row(monkeypatch):
    from app.repositories import huntress as repo

    fetch = AsyncMock(return_value=None)
    execute = AsyncMock()
    monkeypatch.setattr(repo.db, "fetch_one", fetch)
    monkeypatch.setattr(repo.db, "execute", execute)

    snapshot = datetime(2026, 5, 1, 0, 0, 0)
    await repo.upsert_edr_stats(
        7,
        active_incidents=2,
        resolved_incidents=4,
        signals_investigated=11,
        snapshot_at=snapshot,
    )

    sql, params = execute.await_args.args
    assert "INSERT INTO huntress_edr_stats" in sql
    assert params == (7, 2, 4, 11, snapshot)


@pytest.mark.asyncio
async def test_upsert_edr_updates_when_row_exists(monkeypatch):
    from app.repositories import huntress as repo

    fetch = AsyncMock(return_value={"company_id": 7})
    execute = AsyncMock()
    monkeypatch.setattr(repo.db, "fetch_one", fetch)
    monkeypatch.setattr(repo.db, "execute", execute)

    snapshot = datetime(2026, 5, 1)
    await repo.upsert_edr_stats(
        7,
        active_incidents=1,
        resolved_incidents=2,
        signals_investigated=3,
        snapshot_at=snapshot,
    )

    sql, params = execute.await_args.args
    assert sql.strip().upper().startswith("UPDATE")
    assert params == (1, 2, 3, snapshot, 7)


@pytest.mark.asyncio
async def test_upsert_sat_round_trip(monkeypatch):
    from app.repositories import huntress as repo

    rows: list[dict] = []

    async def fake_fetch_one(sql, params=None):
        if rows:
            return {"company_id": params[0]}
        return None

    async def fake_execute(sql, params=None):
        if sql.strip().upper().startswith("INSERT"):
            rows.append({
                "company_id": params[0],
                "avg_completion_rate": params[1],
                "avg_score": params[2],
                "phishing_clicks": params[3],
                "phishing_compromises": params[4],
                "phishing_reports": params[5],
                "snapshot_at": params[6],
            })

    monkeypatch.setattr(repo.db, "fetch_one", fake_fetch_one)
    monkeypatch.setattr(repo.db, "execute", fake_execute)

    snapshot = datetime(2026, 5, 1)
    await repo.upsert_sat_stats(
        3,
        avg_completion_rate=82.5,
        avg_score=91.0,
        phishing_clicks=4,
        phishing_compromises=1,
        phishing_reports=12,
        snapshot_at=snapshot,
    )

    assert rows == [
        {
            "company_id": 3,
            "avg_completion_rate": 82.5,
            "avg_score": 91.0,
            "phishing_clicks": 4,
            "phishing_compromises": 1,
            "phishing_reports": 12,
            "snapshot_at": snapshot,
        }
    ]


@pytest.mark.asyncio
async def test_replace_sat_learner_progress_deletes_then_inserts(monkeypatch):
    from app.repositories import huntress as repo

    sql_calls: list[tuple[str, tuple]] = []

    async def fake_execute(sql, params=None):
        sql_calls.append((sql.strip(), tuple(params or ())))

    monkeypatch.setattr(repo.db, "execute", fake_execute)

    snapshot = datetime(2026, 5, 1)
    count = await repo.replace_sat_learner_progress(
        9,
        rows=[
            {
                "learner_external_id": "L1",
                "learner_email": "l1@example.com",
                "learner_name": "Learner One",
                "assignment_id": "A1",
                "assignment_name": "Phishing 101",
                "status": "completed",
                "completion_percent": 100.0,
                "score": 95,
                "click_rate": 0,
                "compromise_rate": 0,
                "report_rate": 100,
            },
            # Skipped because the keys are blank
            {"learner_external_id": "", "assignment_id": ""},
        ],
        snapshot_at=snapshot,
    )

    assert count == 2  # The function returns the input length
    delete_sql, delete_params = sql_calls[0]
    assert delete_sql.upper().startswith("DELETE")
    assert delete_params == (9,)
    insert_sqls = [s for s, _ in sql_calls[1:]]
    assert all("INSERT INTO huntress_sat_learner_assignments" in s for s in insert_sqls)
    assert len(insert_sqls) == 1  # second row was skipped
