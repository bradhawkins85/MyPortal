"""Verify the Huntress integration is wired into the scheduler."""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_sync_huntress_command_is_registered_for_module():
    from app.services import scheduler

    assert "huntress" in scheduler.COMMANDS_BY_MODULE
    assert "sync_huntress" in scheduler.COMMANDS_BY_MODULE["huntress"]


@pytest.mark.asyncio
async def test_daily_sync_job_invokes_refresh_all_companies(monkeypatch):
    from app.services import huntress as huntress_service
    from app.services import scheduler

    service = scheduler.SchedulerService()

    # Pretend the lock acquired so the sync runs.
    class _Lock:
        async def __aenter__(self):
            return True

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(scheduler.db, "acquire_lock", lambda *a, **kw: _Lock())

    refresh = AsyncMock(
        return_value={"status": "ok", "refreshed": 2, "skipped": 0, "failed": 0}
    )
    monkeypatch.setattr(huntress_service, "is_module_enabled", AsyncMock(return_value=True))
    monkeypatch.setattr(huntress_service, "refresh_all_companies", refresh)

    await service._run_huntress_daily_sync()

    refresh.assert_awaited_once()


def test_huntress_label_present_in_main():
    from app import main

    assert main.TASK_COMMAND_LABELS.get("sync_huntress") == "Sync Huntress data"
