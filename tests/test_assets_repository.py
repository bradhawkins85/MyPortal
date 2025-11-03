import asyncio
from datetime import datetime, timezone

from app.repositories import assets as assets_repo


def test_count_active_assets_builds_expected_query(monkeypatch):
    captured: dict[str, object] = {}

    async def fake_fetch_one(sql, params=None):
        captured["sql"] = sql
        captured["params"] = params
        return {"total": 7}

    monkeypatch.setattr(assets_repo.db, "fetch_one", fake_fetch_one)

    since = datetime(2024, 4, 20, 15, 30, tzinfo=timezone.utc)
    result = asyncio.run(assets_repo.count_active_assets(company_id="5", since=since))

    assert result == 7
    assert "company_id = %s" in captured["sql"]
    assert "last_sync >= %s" in captured["sql"]
    assert captured["params"] == (5, "2024-04-20 15:30:00")


def test_count_active_assets_handles_empty_results(monkeypatch):
    async def fake_fetch_one(sql, params=None):
        return None

    monkeypatch.setattr(assets_repo.db, "fetch_one", fake_fetch_one)

    result = asyncio.run(assets_repo.count_active_assets())

    assert result == 0
