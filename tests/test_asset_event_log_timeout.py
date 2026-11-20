"""The asset page loads while a device's event log read is slow (504 regression).

A device that stopped reporting has no recent entries, so without the
company/asset/time index MariaDB walked the company's whole event history
newest first and the asset page timed out at the gateway.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from app.core.database import Database
from app.core.event_log_database import EventLogDatabase
from app.features.rmm import event_log_routes
from app.repositories import rmm_event_logs as event_log_repo

ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "migrations/567_rmm_event_log_company_asset_index.sql"


@pytest.fixture
def anyio_backend():
    return "asyncio"


def test_migration_builds_the_asset_index_online_on_the_event_log_database():
    database = Database()
    metadata = database._migration_metadata(MIGRATION)
    assert metadata["phase"] == "expand" and not metadata["maintenance"]
    assert database._migration_database(MIGRATION) is not None
    sql = MIGRATION.read_text(encoding="utf-8")
    assert "TRUNCATE" not in sql.upper()
    assert "ADD INDEX IF NOT EXISTS idx_rmm_event_log_company_asset_time" in sql
    assert "(company_id, asset_id, time_created)" in sql
    assert "ALGORITHM=INPLACE" in sql and "LOCK=NONE" in sql


def test_time_limit_uses_each_servers_syntax():
    database = EventLogDatabase()
    sql = "SELECT id FROM t WHERE a = %s"
    database._mariadb = True
    assert database._time_limited(sql, 5) == "SET STATEMENT max_statement_time = 5 FOR " + sql
    database._mariadb = False
    assert database._time_limited(sql, 5) == "SELECT /*+ MAX_EXECUTION_TIME(5000) */ id FROM t WHERE a = %s"


class _FakeMysql:
    is_sqlite = False

    def __init__(self, has_index: bool):
        self.has_index = AsyncMock(return_value=has_index)
        self.fetch_all = AsyncMock(return_value=[])


@pytest.mark.anyio
@pytest.mark.parametrize("has_index", [True, False])
async def test_asset_reads_force_the_asset_index_only_once_it_exists(monkeypatch, has_index):
    fake = _FakeMysql(has_index)
    monkeypatch.setattr(event_log_repo, "event_log_db", fake)

    await event_log_repo.list_entries(company_id=2, asset_id=76, limit=25, timeout_seconds=5)

    sql, params = fake.fetch_all.await_args.args
    assert ("FORCE INDEX (idx_rmm_event_log_company_asset_time)" in sql) is has_index
    assert params == (2, 76, 25)
    assert fake.fetch_all.await_args.kwargs == {"timeout_seconds": 5}


@pytest.mark.anyio
async def test_company_wide_reads_keep_the_optimisers_choice(monkeypatch):
    fake = _FakeMysql(True)
    monkeypatch.setattr(event_log_repo, "event_log_db", fake)

    await event_log_repo.list_entries(company_id=2, limit=200)

    fake.has_index.assert_not_awaited()
    assert "FORCE INDEX" not in fake.fetch_all.await_args.args[0]


def _no_settings(monkeypatch):
    monkeypatch.setattr(event_log_routes.rules_repo, "list_rules_for_device", AsyncMock(return_value=[]))
    monkeypatch.setattr(event_log_routes.event_log_repo, "get_settings",
                        AsyncMock(return_value={"extra_logs": [], "max_level": None}))


@pytest.mark.anyio
async def test_asset_page_limits_its_read_and_says_when_it_was_cut_short(monkeypatch):
    _no_settings(monkeypatch)
    read = AsyncMock(side_effect=RuntimeError("max_statement_time exceeded"))
    monkeypatch.setattr(event_log_routes, "_filtered_entries", read)

    context = await event_log_routes.asset_event_log_context(2, 76, can_edit=False, is_super_admin=False)

    assert read.await_args.kwargs["timeout_seconds"] == event_log_routes.ASSET_PAGE_TIMEOUT_SECONDS
    assert context["entries"] == [] and context["entries_unavailable"] is True


@pytest.mark.anyio
async def test_asset_page_shows_entries_normally(monkeypatch):
    _no_settings(monkeypatch)
    monkeypatch.setattr(event_log_routes, "_filtered_entries", AsyncMock(return_value=[{"id": 1}]))

    context = await event_log_routes.asset_event_log_context(2, 76, can_edit=False, is_super_admin=False)

    assert context["entries"] == [{"id": 1}] and context["entries_unavailable"] is False


def test_template_explains_entries_that_took_too_long():
    template = (ROOT / "app/templates/assets/detail.html").read_text(encoding="utf-8")
    assert "{% elif elog.entries_unavailable %}" in template
    assert "took too long to load" in template
