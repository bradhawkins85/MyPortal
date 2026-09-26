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
