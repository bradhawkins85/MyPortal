from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, call

import aiomysql
import pytest

import app.main as main_module


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_startup_database_initialization_retries_retryable_mysql_errors(
    monkeypatch,
):
    attempts: list[int] = []
    sleep = AsyncMock()

    async def fake_run_migrations() -> None:
        attempts.append(len(attempts) + 1)
        if len(attempts) < 3:
            raise aiomysql.OperationalError(
                2003, "Can't connect to MySQL server on '127.0.0.1'"
            )

    monkeypatch.setattr(main_module.settings, "startup_database_retry_attempts", 3)
    monkeypatch.setattr(main_module.settings, "startup_database_retry_delay_seconds", 7)
    monkeypatch.setattr(main_module.db, "run_migrations", fake_run_migrations)
    connect = AsyncMock()
    disconnect = AsyncMock()
    monkeypatch.setattr(main_module.db, "connect", connect)
    monkeypatch.setattr(main_module.db, "disconnect", disconnect)
    monkeypatch.setattr(main_module.asyncio, "sleep", sleep)

    await main_module._initialise_database_for_startup()

    assert attempts == [1, 2, 3]
    assert disconnect.await_count == 2
    assert connect.await_count == 1
    assert sleep.await_args_list == [call(7), call(7)]


@pytest.mark.anyio
async def test_startup_database_initialization_does_not_retry_non_retryable_errors(
    monkeypatch,
):
    sleep = AsyncMock()

    async def fake_run_migrations() -> None:
        raise aiomysql.ProgrammingError(1064, "syntax error")

    monkeypatch.setattr(main_module.settings, "startup_database_retry_attempts", 5)
    monkeypatch.setattr(main_module.settings, "startup_database_retry_delay_seconds", 7)
    monkeypatch.setattr(main_module.db, "run_migrations", fake_run_migrations)
    connect = AsyncMock()
    disconnect = AsyncMock()
    monkeypatch.setattr(main_module.db, "connect", connect)
    monkeypatch.setattr(main_module.db, "disconnect", disconnect)
    monkeypatch.setattr(main_module.asyncio, "sleep", sleep)

    with pytest.raises(aiomysql.ProgrammingError):
        await main_module._initialise_database_for_startup()

    assert disconnect.await_count == 1
    assert connect.await_count == 0
    sleep.assert_not_awaited()


@pytest.mark.anyio
async def test_startup_database_initialization_retries_os_errors(monkeypatch):
    attempts: list[int] = []
    sleep = AsyncMock()

    async def fake_run_migrations() -> None:
        attempts.append(len(attempts) + 1)
        if len(attempts) == 1:
            raise PermissionError(1, "Operation not permitted")

    monkeypatch.setattr(main_module.settings, "startup_database_retry_attempts", 2)
    monkeypatch.setattr(main_module.settings, "startup_database_retry_delay_seconds", 3)
    monkeypatch.setattr(main_module.db, "run_migrations", fake_run_migrations)
    connect = AsyncMock()
    disconnect = AsyncMock()
    monkeypatch.setattr(main_module.db, "connect", connect)
    monkeypatch.setattr(main_module.db, "disconnect", disconnect)
    monkeypatch.setattr(main_module.asyncio, "sleep", sleep)

    await main_module._initialise_database_for_startup()

    assert attempts == [1, 2]
    assert disconnect.await_count == 1
    assert connect.await_count == 1
    assert sleep.await_args_list == [call(3)]


@pytest.mark.anyio
async def test_on_startup_skips_system_update_check(monkeypatch):
    run_system_update = AsyncMock()
    startup_db = AsyncMock()
    change_log_sync = AsyncMock()
    ensure_modules = AsyncMock()
    refresh_schedules = AsyncMock()
    scheduler_start = AsyncMock()
    bootstrap_template = AsyncMock()
    seed_demo = AsyncMock(return_value={"skipped": True})
    fetch_tray = AsyncMock()
    plugin_loader = SimpleNamespace(load_all=AsyncMock())

    async def list_tasks(*args, **kwargs):
        return []

    monkeypatch.setattr(main_module.scheduler_service, "run_system_update", run_system_update)
    monkeypatch.setattr(main_module, "_initialise_database_for_startup", startup_db)
    monkeypatch.setattr(main_module.change_log_service, "sync_change_log_sources", change_log_sync)
    monkeypatch.setattr(main_module.modules_service, "ensure_default_modules", ensure_modules)
    monkeypatch.setattr(main_module.automations_service, "refresh_all_schedules", refresh_schedules)
    monkeypatch.setattr(main_module.scheduler_service, "start", scheduler_start)
    monkeypatch.setattr(main_module.modules_service, "start_xero_token_keepalive", lambda: None)
    monkeypatch.setattr(main_module.scheduled_tasks_repo, "list_tasks", list_tasks)
    monkeypatch.setattr("app.services.bcp_template.bootstrap_default_template", bootstrap_template)
    monkeypatch.setattr("app.services.demo_seeding.seed_demo_data", seed_demo)
    monkeypatch.setattr("app.services.tray_installer.fetch_latest_tray_installers", fetch_tray)
    monkeypatch.setattr(main_module, "init_plugin_loader", lambda dirs: plugin_loader)
    monkeypatch.setattr(main_module.feature_registry, "load_many", AsyncMock())
    monkeypatch.setattr(main_module.settings, "feature_packs", "")
    monkeypatch.setattr(main_module.settings, "enable_background_relationships", False)
    monkeypatch.setattr(main_module.settings, "matrix_enabled", False)

    await main_module.on_startup()

    run_system_update.assert_not_awaited()
    startup_db.assert_awaited_once()
    scheduler_start.assert_awaited_once()
