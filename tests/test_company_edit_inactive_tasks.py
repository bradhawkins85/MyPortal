"""Test that inactive scheduled tasks can be shown/hidden in company edit page."""
import pytest
from typing import Any
from unittest.mock import AsyncMock
from fastapi import status
from starlette.requests import Request
from starlette.responses import HTMLResponse

from app import main


async def _dummy_receive() -> dict[str, Any]:
    return {"type": "http.request", "body": b"", "more_body": False}


def _make_request(path: str = "/admin/companies/4/edit") -> Request:
    scope = {"type": "http", "method": "GET", "path": path, "headers": []}
    return Request(scope, _dummy_receive)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio("asyncio")
async def test_company_edit_hides_inactive_tasks_by_default(monkeypatch):
    """Test that company edit page hides inactive tasks by default."""
    request = _make_request("/admin/companies/4/edit")
    current_user = {"id": 1, "is_super_admin": True}

    company_record = {
        "id": 4,
        "name": "Example Co",
        "email_domains": [],
        "syncro_company_id": None,
        "xero_id": None,
        "is_vip": 0,
    }
    monkeypatch.setattr(
        main.company_repo,
        "get_company_by_id",
        AsyncMock(return_value=company_record),
    )

    managed_companies = [
        {"id": 4, "name": "Example Co"},
    ]
    monkeypatch.setattr(
        main,
        "_get_company_management_scope",
        AsyncMock(return_value=(True, managed_companies, {})),
    )
    monkeypatch.setattr(
        main.user_company_repo,
        "list_assignments",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(
        main.role_repo,
        "list_roles",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(
        main.pending_staff_access_repo,
        "list_assignments_for_company",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(
        main.staff_repo,
        "list_staff_with_users",
        AsyncMock(return_value=[]),
    )

    # Mock scheduled tasks list_tasks to verify it's called with include_inactive=False
    list_tasks_mock = AsyncMock(return_value=[
        {
            "id": 1,
            "name": "Active Task",
            "command": "sync_staff",
            "company_id": 4,
            "active": 1,
            "last_run_at": None,
        },
    ])
    monkeypatch.setattr(
        main.scheduled_tasks_repo,
        "list_tasks",
        list_tasks_mock,
    )

    # Mock recurring items repo
    monkeypatch.setattr(
        main.recurring_items_repo,
        "list_company_recurring_invoice_items",
        AsyncMock(return_value=[]),
    )

    captured: dict[str, Any] = {}

    async def fake_render_template(template_name, request_obj, user_obj, *, extra):
        captured["template"] = template_name
        captured["extra"] = extra
        return HTMLResponse("ok")

    monkeypatch.setattr(main, "_render_template", fake_render_template)

    # Call with default (show_inactive_tasks=False)
    response = await main._render_company_edit_page(
        request,
        current_user,
        company_id=4,
    )

    assert response.status_code == status.HTTP_200_OK
    
    # Verify list_tasks was called with include_inactive=False
    list_tasks_mock.assert_called_once_with(include_inactive=False)
    
    # Verify show_inactive_tasks is False in template context
    extra = captured.get("extra", {})
    assert extra.get("show_inactive_tasks") is False


@pytest.mark.anyio("asyncio")
async def test_company_edit_shows_inactive_tasks_when_requested(monkeypatch):
    """Test that company edit page shows inactive tasks when show_inactive_tasks=True."""
    request = _make_request("/admin/companies/4/edit?show_inactive=true")
    current_user = {"id": 1, "is_super_admin": True}

    company_record = {
        "id": 4,
        "name": "Example Co",
        "email_domains": [],
        "syncro_company_id": None,
        "xero_id": None,
        "is_vip": 0,
    }
    monkeypatch.setattr(
        main.company_repo,
        "get_company_by_id",
        AsyncMock(return_value=company_record),
    )

    managed_companies = [
        {"id": 4, "name": "Example Co"},
    ]
    monkeypatch.setattr(
        main,
        "_get_company_management_scope",
        AsyncMock(return_value=(True, managed_companies, {})),
    )
    monkeypatch.setattr(
        main.user_company_repo,
        "list_assignments",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(
        main.role_repo,
        "list_roles",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(
        main.pending_staff_access_repo,
        "list_assignments_for_company",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(
        main.staff_repo,
        "list_staff_with_users",
        AsyncMock(return_value=[]),
    )

    # Mock scheduled tasks list_tasks to verify it's called with include_inactive=True
    list_tasks_mock = AsyncMock(return_value=[
        {
            "id": 1,
            "name": "Active Task",
            "command": "sync_staff",
            "company_id": 4,
            "active": 1,
            "last_run_at": None,
        },
        {
            "id": 2,
            "name": "Inactive Task",
            "command": "sync_o365",
            "company_id": 4,
            "active": 0,
            "last_run_at": None,
        },
    ])
    monkeypatch.setattr(
        main.scheduled_tasks_repo,
        "list_tasks",
        list_tasks_mock,
    )

    # Mock recurring items repo
    monkeypatch.setattr(
        main.recurring_items_repo,
        "list_company_recurring_invoice_items",
        AsyncMock(return_value=[]),
    )

    captured: dict[str, Any] = {}

    async def fake_render_template(template_name, request_obj, user_obj, *, extra):
        captured["template"] = template_name
        captured["extra"] = extra
        return HTMLResponse("ok")

    monkeypatch.setattr(main, "_render_template", fake_render_template)

    # Call with show_inactive_tasks=True
    response = await main._render_company_edit_page(
        request,
        current_user,
        company_id=4,
        show_inactive_tasks=True,
    )

    assert response.status_code == status.HTTP_200_OK
    
    # Verify list_tasks was called with include_inactive=True
    list_tasks_mock.assert_called_once_with(include_inactive=True)
    
    # Verify show_inactive_tasks is True in template context
    extra = captured.get("extra", {})
    assert extra.get("show_inactive_tasks") is True
