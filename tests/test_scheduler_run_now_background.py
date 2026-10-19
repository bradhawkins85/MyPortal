"""Run now starts a scheduled task in the background.

Long tasks, such as the Chocolatey app list refresh, take longer than the
reverse proxy allows a request to wait; waiting for them returned HTTP 504
while the task carried on.
"""
import asyncio
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.api.routes import scheduler as scheduler_routes


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_run_now_answers_before_the_task_finishes(monkeypatch):
    monkeypatch.setattr(scheduler_routes.scheduled_tasks_repo, "get_task", AsyncMock(return_value={"id": 7}))
    release = asyncio.Event()
    finished = []

    async def run_now(task_id):
        await release.wait()
        finished.append(task_id)

    monkeypatch.setattr(scheduler_routes.scheduler_service, "run_now", run_now)
    response = await asyncio.wait_for(scheduler_routes.run_task_now(7, None, {}), timeout=1)
    assert response.accepted is True and finished == []
    release.set()
    for _ in range(10):
        await asyncio.sleep(0)
    assert finished == [7]


@pytest.mark.anyio
async def test_run_now_rejects_unknown_tasks_without_starting_one(monkeypatch):
    monkeypatch.setattr(scheduler_routes.scheduled_tasks_repo, "get_task", AsyncMock(return_value=None))
    run_now = AsyncMock()
    monkeypatch.setattr(scheduler_routes.scheduler_service, "run_now", run_now)
    with pytest.raises(HTTPException) as exc:
        await scheduler_routes.run_task_now(7, None, {})
    assert exc.value.status_code == 404
    run_now.assert_not_called()
