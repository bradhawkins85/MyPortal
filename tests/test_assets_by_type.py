import asyncio

from app.repositories import assets as assets_repo


def test_count_active_assets_by_type_filters_workstations(monkeypatch):
    captured: dict[str, object] = {}

    async def fake_fetch_one(sql, params=None):
        captured["sql"] = sql
        captured["params"] = params
        return {"total": 5}

    monkeypatch.setattr(assets_repo.db, "fetch_one", fake_fetch_one)

    result = asyncio.run(
        assets_repo.count_active_assets_by_type(
            company_id=10,
            device_type="Workstation",
        )
    )

    assert result == 5
    assert "company_id = %s" in captured["sql"]
    assert "LOWER(type) = LOWER(%s)" in captured["sql"]
    assert captured["params"] == (10, "Workstation")


def test_count_active_assets_by_type_filters_servers(monkeypatch):
    captured: dict[str, object] = {}

    async def fake_fetch_one(sql, params=None):
        captured["sql"] = sql
        captured["params"] = params
        return {"total": 3}

    monkeypatch.setattr(assets_repo.db, "fetch_one", fake_fetch_one)

    result = asyncio.run(
        assets_repo.count_active_assets_by_type(
            company_id=10,
            device_type="Server",
        )
    )

    assert result == 3
    assert "LOWER(type) = LOWER(%s)" in captured["sql"]
    assert captured["params"] == (10, "Server")


def test_count_active_assets_by_type_filters_users(monkeypatch):
    captured: dict[str, object] = {}

    async def fake_fetch_one(sql, params=None):
        captured["sql"] = sql
        captured["params"] = params
        return {"total": 15}

    monkeypatch.setattr(assets_repo.db, "fetch_one", fake_fetch_one)

    result = asyncio.run(
        assets_repo.count_active_assets_by_type(
            company_id=10,
            device_type="User",
        )
    )

    assert result == 15
    assert "LOWER(type) = LOWER(%s)" in captured["sql"]
    assert captured["params"] == (10, "User")


def test_count_active_assets_by_type_case_insensitive(monkeypatch):
    """Test that device type matching is case-insensitive."""
    captured: dict[str, object] = {}

    async def fake_fetch_one(sql, params=None):
        captured["sql"] = sql
        captured["params"] = params
        return {"total": 7}

    monkeypatch.setattr(assets_repo.db, "fetch_one", fake_fetch_one)

    result = asyncio.run(
        assets_repo.count_active_assets_by_type(
            company_id=10,
            device_type="workstation",  # lowercase
        )
    )

    assert result == 7
    assert "LOWER(type) = LOWER(%s)" in captured["sql"]
