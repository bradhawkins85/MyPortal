from pathlib import Path

import pytest

from app.repositories import assets


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_ambiguous_fallback_match_is_quarantined(monkeypatch):
    calls = []

    async def fetch_one(query, params=None):
        if "asset_source_records" in query or "syncro_asset_id" in query:
            return None
        raise AssertionError(query)

    async def fetch_all(query, params=None):
        assert "serial_number" in query
        return [{"id": 1}, {"id": 2}]

    async def execute(query, params=None):
        calls.append((query, params))

    monkeypatch.setattr(assets.db, "fetch_one", fetch_one)
    monkeypatch.setattr(assets.db, "fetch_all", fetch_all)
    monkeypatch.setattr(assets.db, "execute", execute)

    result = await assets.upsert_asset(
        company_id=7,
        name="host",
        serial_number="duplicate",
        syncro_asset_id="remote-9",
        source="syncro",
        source_external_id="remote-9",
    )

    assert result is None
    assert any("asset_source_records" in query for query, _ in calls)
    assert any("quarantined" in params for _, params in calls)


def _capture_db(monkeypatch, *, fetch_one, fetch_all):
    calls = []

    async def execute(query, params=None):
        calls.append((query, params))

    async def execute_returning_lastrowid(query, params=None):
        calls.append((query, params))
        return 99

    monkeypatch.setattr(assets.db, "fetch_one", fetch_one)
    monkeypatch.setattr(assets.db, "fetch_all", fetch_all)
    monkeypatch.setattr(assets.db, "execute", execute)
    monkeypatch.setattr(
        assets.db, "execute_returning_lastrowid", execute_returning_lastrowid
    )
    return calls


@pytest.mark.anyio
async def test_shared_serial_does_not_merge_distinct_tactical_agents(monkeypatch):
    async def fetch_one(query, params=None):
        return None

    async def fetch_all(query, params=None):
        # Existing asset shares the serial but belongs to another TRMM agent.
        return [{"id": 5, "provenance": "integration", "tactical_asset_id": "agent-1"}]

    calls = _capture_db(monkeypatch, fetch_one=fetch_one, fetch_all=fetch_all)

    result = await assets.upsert_asset(
        company_id=7,
        name="host-2",
        serial_number="SHARED-VM-SERIAL",
        tactical_asset_id="agent-2",
        match_name=True,
        source="tacticalrmm",
        source_external_id="agent-2",
    )

    assert result == 99
    assert any("INSERT INTO assets" in query for query, _ in calls)
    assert not any("UPDATE assets" in query for query, _ in calls)


@pytest.mark.anyio
async def test_device_without_serial_is_imported(monkeypatch):
    queries = []

    async def fetch_one(query, params=None):
        return None

    async def fetch_all(query, params=None):
        queries.append(query)
        return []

    calls = _capture_db(monkeypatch, fetch_one=fetch_one, fetch_all=fetch_all)

    result = await assets.upsert_asset(
        company_id=7,
        name="no-serial-host",
        serial_number=None,
        tactical_asset_id="agent-3",
        match_name=True,
        source="tacticalrmm",
        source_external_id="agent-3",
    )

    assert result == 99
    assert not any("serial_number = %s" in q for q in queries)
    assert any("INSERT INTO assets" in query for query, _ in calls)


@pytest.mark.anyio
async def test_placeholder_serial_is_not_used_for_matching(monkeypatch):
    queries = []

    async def fetch_one(query, params=None):
        return None

    async def fetch_all(query, params=None):
        queries.append(query)
        return []

    _capture_db(monkeypatch, fetch_one=fetch_one, fetch_all=fetch_all)

    await assets.upsert_asset(
        company_id=7,
        name="oem-host",
        serial_number="Default string",
        tactical_asset_id="agent-4",
        match_name=True,
        source="tacticalrmm",
        source_external_id="agent-4",
    )

    assert not any("serial_number = %s" in q for q in queries)


@pytest.mark.anyio
async def test_stale_merged_source_link_is_split(monkeypatch):
    async def fetch_one(query, params=None):
        if "FROM asset_source_records" in query:
            return {"asset_id": 5, "status": "active", "id": 1}
        if "WHERE id = %s AND company_id" in query:
            # Asset 5 was previously overwritten by a different agent.
            return {"id": 5, "tactical_asset_id": "agent-1"}
        return None

    async def fetch_all(query, params=None):
        return []

    calls = _capture_db(monkeypatch, fetch_one=fetch_one, fetch_all=fetch_all)

    result = await assets.upsert_asset(
        company_id=7,
        name="host-2",
        tactical_asset_id="agent-2",
        match_name=True,
        source="tacticalrmm",
        source_external_id="agent-2",
    )

    assert result == 99
    assert not any("UPDATE assets" in query for query, _ in calls)


def test_source_migration_tracks_provenance_and_sync_runs():
    sql = Path("migrations/408_documentation_integration_sources.sql").read_text()
    assert "UNIQUE KEY uq_asset_source_identity (company_id, source, external_id)" in sql
    assert "field_ownership_json" in sql
    assert "last_success_at" in sql
    assert "last_error" in sql
    assert "integration_sync_runs" in sql


def test_hudu_integration_remains_available():
    assert Path("app/services/hudu.py").exists()
    assert Path("app/features/hudu/__init__.py").exists()
