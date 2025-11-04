"""Test that company automation tasks are sorted correctly in the company edit page."""
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
async def test_company_automation_tasks_sorted_by_name(monkeypatch):
    """Test that company_automation_tasks is correctly sorted by name in _render_company_edit_page."""
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

    # Mock scheduled tasks with unsorted names
    unsorted_tasks = [
        {
            "id": 3,
            "name": "Zebra Task",
            "command": "sync_o365",
            "company_id": 4,
            "last_run_at": None,
        },
        {
            "id": 1,
            "name": "Alpha Task",
            "command": "sync_staff",
            "company_id": 4,
            "last_run_at": None,
        },
        {
            "id": 2,
            "name": "Beta Task",
            "command": "sync_to_xero",
            "company_id": 4,
            "last_run_at": None,
        },
    ]
    monkeypatch.setattr(
        main.scheduled_tasks_repo,
        "list_tasks",
        AsyncMock(return_value=unsorted_tasks),
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

    response = await main._render_company_edit_page(
        request,
        current_user,
        company_id=4,
    )

    assert response.status_code == status.HTTP_200_OK
    assert captured.get("template") == "admin/company_edit.html"
    
    extra = captured.get("extra", {})
    automation_tasks = extra.get("company_automation_tasks", [])
    
    # Verify tasks are sorted alphabetically by name
    assert len(automation_tasks) == 3
    assert automation_tasks[0]["name"] == "Alpha Task"
    assert automation_tasks[1]["name"] == "Beta Task"
    assert automation_tasks[2]["name"] == "Zebra Task"
