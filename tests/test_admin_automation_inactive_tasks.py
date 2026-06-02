"""Tests for the /admin/automation redirect to /admin/scheduled-tasks."""

from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi import status
from starlette.requests import Request
from starlette.responses import RedirectResponse

from app import main


async def _dummy_receive() -> dict[str, Any]:
    return {"type": "http.request", "body": b"", "more_body": False}


def _make_request(path: str = "/admin/automation") -> Request:
    scope = {"type": "http", "method": "GET", "path": path, "headers": []}
    return Request(scope, _dummy_receive)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio("asyncio")
async def test_admin_automation_redirects_to_scheduled_tasks():
    """GET /admin/automation should redirect to /admin/scheduled-tasks."""
    request = _make_request()
    response = await main.admin_automation(request, show_inactive=False)
    assert response.status_code == status.HTTP_301_MOVED_PERMANENTLY
    assert response.headers["location"] == "/admin/scheduled-tasks"


@pytest.mark.anyio("asyncio")
async def test_admin_automation_redirects_with_show_inactive():
    """GET /admin/automation?show_inactive=1 should also redirect."""
    request = _make_request("/admin/automation?show_inactive=1")
    response = await main.admin_automation(request, show_inactive=True)
    assert response.status_code == status.HTTP_301_MOVED_PERMANENTLY
    assert response.headers["location"] == "/admin/scheduled-tasks"
