"""Tests for hiding inactive scheduled tasks on the automation admin page."""
"""Tests covering inactive scheduled task visibility on the automation page."""

from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi import status
from starlette.requests import Request
from starlette.responses import HTMLResponse

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
async def test_admin_automation_hides_inactive_by_default(monkeypatch):
    """Inactive tasks should be hidden unless explicitly requested."""
    request = _make_request()
    current_user = {"id": 1, "is_super_admin": True}

    monkeypatch.setattr(
        main,
        "_require_super_admin_page",
        AsyncMock(return_value=(current_user, None)),
    )
    list_tasks_mock = AsyncMock(
        return_value=[
            {
                "id": 1,
                "name": "Active Task",
                "command": "sync_staff",
                "company_id": None,
                "active": 1,
                "last_run_at": None,
            }
        ]
    )
    monkeypatch.setattr(main.scheduled_tasks_repo, "list_tasks", list_tasks_mock)
    monkeypatch.setattr(
        main.company_repo,
        "list_companies",
        AsyncMock(return_value=[{"id": 1, "name": "Example Co"}]),
    )

    captured: dict[str, Any] = {}

    async def fake_render_template(template_name, request_obj, user_obj, *, extra):
        captured["template"] = template_name
        captured["extra"] = extra
        return HTMLResponse("ok")

    monkeypatch.setattr(main, "_render_template", fake_render_template)

    response = await main.admin_automation(request, show_inactive=False)

    assert response.status_code == status.HTTP_200_OK
    list_tasks_mock.assert_called_once_with(include_inactive=False)
    extra = captured.get("extra", {})
    assert extra.get("show_inactive_tasks") is False


@pytest.mark.anyio("asyncio")
async def test_admin_automation_shows_inactive_when_requested(monkeypatch):
    """Setting show_inactive to true should include inactive rows."""
    request = _make_request("/admin/automation?show_inactive=1")
    current_user = {"id": 1, "is_super_admin": True}

    monkeypatch.setattr(
        main,
        "_require_super_admin_page",
        AsyncMock(return_value=(current_user, None)),
    )
    list_tasks_mock = AsyncMock(
        return_value=[
            {
                "id": 1,
                "name": "Active Task",
                "command": "sync_staff",
                "company_id": None,
                "active": 1,
                "last_run_at": None,
            },
            {
                "id": 2,
                "name": "Inactive Task",
                "command": "sync_o365",
                "company_id": None,
                "active": 0,
                "last_run_at": None,
            },
        ]
    )
    monkeypatch.setattr(main.scheduled_tasks_repo, "list_tasks", list_tasks_mock)
    monkeypatch.setattr(
        main.company_repo,
        "list_companies",
        AsyncMock(return_value=[{"id": 1, "name": "Example Co"}]),
    )

    captured: dict[str, Any] = {}

    async def fake_render_template(template_name, request_obj, user_obj, *, extra):
        captured["template"] = template_name
        captured["extra"] = extra
        return HTMLResponse("ok")

    monkeypatch.setattr(main, "_render_template", fake_render_template)

    response = await main.admin_automation(request, show_inactive=True)

    assert response.status_code == status.HTTP_200_OK
    list_tasks_mock.assert_called_once_with(include_inactive=True)
    extra = captured.get("extra", {})
    assert extra.get("show_inactive_tasks") is True
