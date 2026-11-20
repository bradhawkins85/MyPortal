"""Billing's active-asset counts include devices seen by the tray or MyPortal RMM agent.

Only Tactical RMM and Syncro write ``assets.last_sync``; with those modules
disabled every device would otherwise drop out of the count within a month.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import aiosqlite
import pytest

from app.repositories import asset_custom_fields as custom_fields_repo
from app.repositories import assets as assets_repo

NOW = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)
RECENT = "2026-11-19 12:00:00"
OLD = "2026-09-01 12:00:00"


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def asset_db(monkeypatch):
    connection = await aiosqlite.connect(":memory:")
    connection.row_factory = aiosqlite.Row
    await connection.executescript(
        "CREATE TABLE assets (id INTEGER PRIMARY KEY, company_id INTEGER, type TEXT, last_sync TEXT);"
        "CREATE TABLE tray_devices (id INTEGER PRIMARY KEY, asset_id INTEGER, status TEXT,"
        " last_seen_utc TEXT, ws_last_seen_utc TEXT);"
        "CREATE TABLE rmm_agents (id INTEGER PRIMARY KEY, asset_id INTEGER, status TEXT, last_seen_utc TEXT);"
        "CREATE TABLE asset_custom_field_definitions (id INTEGER PRIMARY KEY, name TEXT, field_type TEXT);"
        "CREATE TABLE asset_custom_field_values (asset_id INTEGER, field_definition_id INTEGER,"
        " value_boolean INTEGER);"
        # 1 synced recently; 2 stale sync, tray heartbeat; 3 tray WebSocket only;
        # 4 RMM agent only; 5 stale everywhere; 6 revoked tray; 7 other company.
        f"INSERT INTO assets VALUES (1, 5, 'Workstation', '{RECENT}'), (2, 5, 'Workstation', '{OLD}'),"
        " (3, 5, 'Server', NULL), (4, 5, 'Workstation', NULL), (5, 5, 'Workstation', '" + OLD + "'),"
        " (6, 5, 'Workstation', NULL), (7, 9, 'Workstation', '" + RECENT + "');"
        f"INSERT INTO tray_devices VALUES (1, 2, 'active', '{RECENT}', NULL), (2, 3, 'active', '{OLD}', '{RECENT}'),"
        f" (3, 5, 'active', '{OLD}', NULL), (4, 6, 'revoked', '{RECENT}', NULL);"
        f"INSERT INTO rmm_agents VALUES (1, 4, 'active', '{RECENT}'), (2, 5, 'active', '{OLD}');"
        "INSERT INTO asset_custom_field_definitions VALUES (1, 'Managed', 'checkbox');"
        "INSERT INTO asset_custom_field_values VALUES (2, 1, 1), (4, 1, 1), (5, 1, 1);"
    )
    monkeypatch.setattr(assets_repo.db, "_use_sqlite", True)
    monkeypatch.setattr(assets_repo.db, "_sqlite_conn", connection)
    yield connection
    await connection.close()


@pytest.mark.anyio
async def test_active_count_includes_tray_and_rmm_check_ins(asset_db):
    since = NOW - timedelta(days=30)
    assert await assets_repo.count_active_assets(company_id=5, since=since) == 4


@pytest.mark.anyio
async def test_active_count_by_type_includes_agent_check_ins(asset_db):
    since = NOW - timedelta(days=30)
    assert await assets_repo.count_active_assets_by_type(
        company_id=5, since=since, device_type="Workstation") == 3
    assert await assets_repo.count_active_assets_by_type(
        company_id=5, since=since, device_type="Server") == 1


@pytest.mark.anyio
async def test_active_custom_field_count_includes_agent_check_ins(asset_db):
    since = NOW - timedelta(days=30)
    assert await custom_fields_repo.count_assets_by_custom_field(5, "Managed", True, since=since) == 2
    assert await custom_fields_repo.count_assets_by_custom_field(5, "Managed", True) == 3
