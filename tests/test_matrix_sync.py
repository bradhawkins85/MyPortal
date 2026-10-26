from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from app.services import matrix_sync


@pytest.fixture
def sync_env(monkeypatch):
    state = {"locked": False, "cursor": "batch-1", "released": 0}

    @asynccontextmanager
    async def acquire_lock(name, timeout):
        assert name == "matrix_chat_sync"
        assert timeout == 0
        if state["locked"]:
            yield False
            return
        state["locked"] = True
        try:
            yield True
        finally:
            state["locked"] = False
            state["released"] += 1

    async def get_state():
        assert state["locked"]
        return state["cursor"]

    async def save_state(cursor):
        assert state["locked"]
        state["cursor"] = cursor

    repo = SimpleNamespace(
        db=SimpleNamespace(acquire_lock=acquire_lock),
        get_sync_state=AsyncMock(side_effect=get_state),
        save_sync_state=AsyncMock(side_effect=save_state),
    )
    sync = AsyncMock(return_value={"next_batch": "batch-2"})
    process = AsyncMock()
    monkeypatch.setattr(matrix_sync, "chat_repo", repo)
    monkeypatch.setattr(matrix_sync.matrix_service, "sync", sync)
    monkeypatch.setattr(matrix_sync, "process_sync_response", process)
    monkeypatch.setattr(matrix_sync, "_running", False)
    monkeypatch.setattr(matrix_sync, "_settings", SimpleNamespace(
        matrix_enabled=True, matrix_bot_access_token="test-token",
    ))
    return state, repo, sync, process


@pytest.mark.asyncio
async def test_eight_workers_only_open_one_sync_request(sync_env):
    state, repo, sync, process = sync_env
    started = asyncio.Event()
    finish = asyncio.Event()

    async def poll(**kwargs):
        assert state["locked"]
        started.set()
        await finish.wait()
        return {"next_batch": "batch-2"}

    sync.side_effect = poll
    leader = asyncio.create_task(matrix_sync._sync_once())
    try:
        await asyncio.wait_for(started.wait(), timeout=1)
        # Each call represents an independent worker sharing the database lock.
        results = await asyncio.gather(*(matrix_sync._sync_once() for _ in range(7)))
        assert results == [False] * 7
        sync.assert_awaited_once_with(since="batch-1", timeout_ms=30_000)
        repo.get_sync_state.assert_awaited_once()
        repo.save_sync_state.assert_not_awaited()
        process.assert_not_awaited()
    finally:
        finish.set()
        await leader

    assert state["cursor"] == "batch-2"
    assert state["locked"] is False
    assert state["released"] == 1


@pytest.mark.asyncio
async def test_handoff_reads_latest_cursor_and_checkpoints_after_processing(sync_env):
    state, repo, sync, process = sync_env

    async def process_response(data):
        assert state["locked"]
        assert state["cursor"] == "batch-1"

    process.side_effect = process_response
    assert await matrix_sync._sync_once() is True
    process.side_effect = None
    sync.return_value = {"next_batch": "batch-3"}
    assert await matrix_sync._sync_once() is True
    assert sync.await_args_list[1].kwargs["since"] == "batch-2"
    assert state["cursor"] == "batch-3"


@pytest.mark.asyncio
async def test_processing_failure_keeps_cursor_for_retry(sync_env):
    state, repo, sync, process = sync_env
    process.side_effect = RuntimeError("database unavailable")
    with pytest.raises(RuntimeError):
        await matrix_sync._sync_once()
    repo.save_sync_state.assert_not_awaited()
    assert state["locked"] is False
    process.side_effect = None
    await matrix_sync._sync_once()
    assert sync.await_args_list[1].kwargs["since"] == "batch-1"


@pytest.mark.asyncio
async def test_invalid_sync_response_does_not_advance_cursor(sync_env):
    state, repo, sync, process = sync_env
    sync.return_value = {}
    with pytest.raises(matrix_sync.matrix_service.MatrixError, match="missing next_batch"):
        await matrix_sync._sync_once()
    process.assert_not_awaited()
    repo.save_sync_state.assert_not_awaited()
    assert state["locked"] is False


@pytest.mark.asyncio
async def test_cancelling_worker_releases_lock_for_takeover(sync_env):
    state, repo, sync, process = sync_env
    started = asyncio.Event()

    async def poll(**kwargs):
        started.set()
        await asyncio.Event().wait()

    sync.side_effect = poll
    task = asyncio.create_task(matrix_sync.run_sync_loop())
    try:
        await asyncio.wait_for(started.wait(), timeout=1)
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert state["locked"] is False
    assert matrix_sync._running is False
    repo.save_sync_state.assert_not_awaited()
    sync.side_effect = None
    assert await matrix_sync._sync_once() is True
    assert sync.await_args.kwargs["since"] == "batch-1"


@pytest.mark.asyncio
async def test_standby_worker_waits_without_polling(sync_env, monkeypatch):
    state, repo, sync, process = sync_env
    state["locked"] = True

    async def sleep(seconds):
        assert state["released"] == 0
        matrix_sync.stop_sync_loop()

    sleep_mock = AsyncMock(side_effect=sleep)
    monkeypatch.setattr(matrix_sync.asyncio, "sleep", sleep_mock)
    await matrix_sync.run_sync_loop()
    sleep_mock.assert_awaited_once_with(5)
    sync.assert_not_awaited()
    repo.get_sync_state.assert_not_awaited()


@pytest.mark.asyncio
async def test_sync_failure_is_logged_and_backed_off(sync_env, monkeypatch):
    state, repo, sync, process = sync_env
    sync.side_effect = matrix_sync.matrix_service.MatrixError("M_UNKNOWN", "connection failed")
    log_error = Mock()
    monkeypatch.setattr(matrix_sync, "log_error", log_error)

    async def sleep(seconds):
        assert state["locked"] is False
        matrix_sync.stop_sync_loop()

    sleep_mock = AsyncMock(side_effect=sleep)
    monkeypatch.setattr(matrix_sync.asyncio, "sleep", sleep_mock)
    await matrix_sync.run_sync_loop()
    log_error.assert_called_once()
    assert log_error.call_args.args[0] == "Matrix sync error"
    sleep_mock.assert_awaited_once_with(10)
    repo.save_sync_state.assert_not_awaited()
