from __future__ import annotations

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
    monkeypatch.setattr(main_module.db, "connect", AsyncMock())
    monkeypatch.setattr(main_module.db, "disconnect", AsyncMock())
    monkeypatch.setattr(main_module.asyncio, "sleep", sleep)

    await main_module._initialise_database_for_startup()

    assert attempts == [1, 2, 3]
    assert main_module.db.disconnect.await_count == 2
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
    monkeypatch.setattr(main_module.db, "connect", AsyncMock())
    monkeypatch.setattr(main_module.db, "disconnect", AsyncMock())
    monkeypatch.setattr(main_module.asyncio, "sleep", sleep)

    with pytest.raises(aiomysql.ProgrammingError):
        await main_module._initialise_database_for_startup()

    assert main_module.db.disconnect.await_count == 1
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
    monkeypatch.setattr(main_module.db, "connect", AsyncMock())
    monkeypatch.setattr(main_module.db, "disconnect", AsyncMock())
    monkeypatch.setattr(main_module.asyncio, "sleep", sleep)

    await main_module._initialise_database_for_startup()

    assert attempts == [1, 2]
    assert main_module.db.disconnect.await_count == 1
    assert sleep.await_args_list == [call(3)]
