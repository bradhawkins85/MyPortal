"""The asset page's per-device reads stay indexed and batched, so large companies do not time out."""
from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.core import event_log_database, stats_database
from app.core.event_log_database import EventLogDatabase
from app.core.stats_database import StatsDatabase
from app.features.assets import routes


@pytest.fixture
def anyio_backend():
    return "asyncio"


async def _plan(database, sql: str, params: tuple) -> str:
    rows = await database.fetch_all("EXPLAIN QUERY PLAN " + sql, params)
    return " ".join(str(row["detail"]) for row in rows)


def test_mysql_schemas_index_a_devices_history():
    assert "idx_rmm_event_log_company_asset_time (company_id, asset_id, time_created)" in event_log_database.MYSQL_SCHEMA[0]
    assert "idx_rmm_device_stats_asset_time (asset_id, sampled_at)" in stats_database.MYSQL_SCHEMA[0]
    # Tables created before the indexes get them on the next connect.
    assert "idx_rmm_event_log_company_asset_time" in {index.name for index in event_log_database.INDEXES}
    assert "idx_rmm_device_stats_asset_time" in {index.name for index in stats_database.INDEXES}


@pytest.mark.anyio
async def test_asset_event_log_query_uses_the_device_index(tmp_path):
    path = str(tmp_path / "events.db")
    import aiosqlite

    # A table from before the index existed.
    async with aiosqlite.connect(path) as conn:
        await conn.execute(
            "CREATE TABLE rmm_event_log_entries (id INTEGER PRIMARY KEY AUTOINCREMENT, company_id INTEGER NULL, "
            "asset_id INTEGER NULL, agent_id INTEGER NOT NULL, log_name TEXT NOT NULL, record_id INTEGER NOT NULL, "
            "event_id INTEGER NOT NULL, level INTEGER NOT NULL DEFAULT 4, provider TEXT NULL, computer TEXT NULL, "
            "message TEXT NULL, time_created TEXT NOT NULL, received_at TEXT NOT NULL, UNIQUE (agent_id, log_name, record_id))"
        )
        await conn.commit()
    database = EventLogDatabase(sqlite_path=path)
    try:
        assert await database.connect()
        plan = await _plan(
            database,
            "SELECT id FROM rmm_event_log_entries WHERE company_id = %s AND asset_id = %s "
            "ORDER BY time_created DESC, id DESC LIMIT 25",
            (1, 10),
        )
        assert "idx_rmm_event_log_company_asset_time" in plan
    finally:
        await database.disconnect()


@pytest.mark.anyio
async def test_asset_stats_query_uses_the_device_time_index():
    database = StatsDatabase(sqlite_path=":memory:")
    try:
        assert await database.connect()
        plan = await _plan(
            database,
            "SELECT metric FROM rmm_device_stats WHERE asset_id = %s AND sampled_at >= %s",
            (10, "2026-01-01"),
        )
        assert "idx_rmm_device_stats_asset_time" in plan
    finally:
        await database.disconnect()


@pytest.mark.anyio
async def test_customer_visible_assets_reads_audiences_once(monkeypatch):
    audiences = AsyncMock(return_value={1: [7], 2: [8]})
    monkeypatch.setattr(routes.audience_repo, "list_role_ids_for_records", audiences)
    rows = [
        {"id": 1, "customer_visible": True},
        {"id": 2, "customer_visible": True},
        {"id": 3, "customer_visible": True},
        {"id": 4, "customer_visible": False},
    ]
    member = {"role_id": 7, "menu_permissions": {"content.assets": "read"}}

    assert [row["id"] for row in await routes._customer_visible_assets(member, 3, rows)] == [1]
    audiences.assert_awaited_once_with(3, "asset", [1, 2, 3])

    # Roleless memberships keep the legacy path: only publications without audiences.
    assert [row["id"] for row in await routes._customer_visible_assets({"role_id": None}, 3, rows)] == [3]

    # A role without asset content permission sees nothing and causes no query.
    audiences.reset_mock()
    assert await routes._customer_visible_assets({"role_id": 7, "menu_permissions": {}}, 3, rows) == []
    audiences.assert_not_awaited()


@pytest.mark.anyio
async def test_customer_visible_assets_matches_the_single_asset_check(monkeypatch):
    audience_rows = {1: [7], 2: [], 3: [8]}
    monkeypatch.setattr(routes.audience_repo, "list_role_ids_for_records", AsyncMock(
        side_effect=lambda _company, _kind, ids: {i: audience_rows[i] for i in ids if audience_rows.get(i)}))
    monkeypatch.setattr(routes.audience_repo, "list_role_ids", AsyncMock(
        side_effect=lambda _company, _kind, record_id: audience_rows[record_id]))
    monkeypatch.setattr(routes.audience_repo, "role_can_access", AsyncMock(
        side_effect=lambda _company, _kind, record_id, role_id: role_id in audience_rows[record_id]))
    rows = [{"id": record_id, "customer_visible": True} for record_id in audience_rows]
    for member in ({"role_id": None}, {"role_id": 7, "menu_permissions": {"content.assets": "write"}},
                   {"role_id": 8, "permissions": {"content.assets": "read"}}):
        expected = [row["id"] for row in rows if await routes._customer_role_can_view_asset(member, 3, row["id"])]
        assert [row["id"] for row in await routes._customer_visible_assets(member, 3, rows)] == expected


class _SqliteDb:
    def __init__(self, conn):
        self.conn = conn

    async def fetch_all(self, sql, params=()):
        cursor = await self.conn.execute(sql.replace("%s", "?"), params)
        return [dict(row) for row in await cursor.fetchall()]


@pytest.mark.anyio
async def test_relationship_picker_queries_read_only_labels(monkeypatch):
    import aiosqlite

    from app.repositories import customer_content_audience, infrastructure, processes, tickets

    conn = await aiosqlite.connect(":memory:")
    conn.row_factory = aiosqlite.Row
    await conn.executescript("""
        CREATE TABLE tickets (id INTEGER PRIMARY KEY, company_id INT, ticket_number TEXT, subject TEXT, status TEXT,
          description TEXT, merged_into_ticket_id INT, updated_at TEXT);
        INSERT INTO tickets VALUES (1, 3, 'T1', 'Old', 'open', 'long', NULL, '2026-01-01'),
          (2, 3, 'T2', 'New', 'closed', 'long', NULL, '2026-02-01'), (3, 3, 'T3', 'Merged', 'open', '', 1, '2026-03-01'),
          (4, 9, 'T4', 'Other company', 'open', '', NULL, '2026-03-01');
        CREATE TABLE process_runs (id INTEGER PRIMARY KEY, company_id INT, template_name TEXT, status TEXT, started_at TEXT);
        INSERT INTO process_runs VALUES (1, 3, 'Onboard', 'pending', '2026-01-01'), (2, 3, 'Gone', 'cancelled', '2026-01-02'),
          (3, 9, 'Other', 'pending', '2026-01-03');
        CREATE TABLE ip_networks (id INTEGER PRIMARY KEY, company_id INT, name TEXT, cidr TEXT);
        INSERT INTO ip_networks VALUES (1, 3, 'LAN', '10.0.0.0/24'), (2, 9, 'Other', '10.1.0.0/24');
        CREATE TABLE racks (id INTEGER PRIMARY KEY, company_id INT, name TEXT, location TEXT, unit_count INT);
        INSERT INTO racks VALUES (1, 3, 'Rack A', 'Comms', 42), (2, 9, 'Other', NULL, 12);
        CREATE TABLE customer_content_role_audience (company_id INT, content_type TEXT, record_id INT, role_id INT);
        INSERT INTO customer_content_role_audience VALUES (3, 'asset', 10, 8), (3, 'asset', 10, 7), (3, 'asset', 11, 7),
          (3, 'knowledge_base', 10, 9), (9, 'asset', 10, 5);
    """)
    fake = _SqliteDb(conn)
    for module in (tickets, processes, infrastructure, customer_content_audience):
        monkeypatch.setattr(module, "db", fake)
    try:
        assert await tickets.list_relationship_targets(3) == [
            {"id": 2, "ticket_number": "T2", "subject": "New", "status": "closed"},
            {"id": 1, "ticket_number": "T1", "subject": "Old", "status": "open"},
        ]
        assert await processes.list_relationship_targets(3) == [{"id": 1, "template_name": "Onboard", "status": "pending"}]
        assert await infrastructure.relationship_targets(3) == {
            "networks": [{"id": 1, "name": "LAN", "cidr": "10.0.0.0/24"}],
            "racks": [{"id": 1, "name": "Rack A", "location": "Comms", "unit_count": 42}],
        }
        assert await customer_content_audience.list_role_ids_for_records(3, "asset", [10, 11, 12]) == {
            10: [7, 8], 11: [7]}
        assert await customer_content_audience.list_role_ids_for_records(3, "asset", []) == {}
    finally:
        await conn.close()
