"""
Test that ticket detail page includes Cal.com booking link for assigned technician.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi import status
from fastapi.responses import HTMLResponse
from starlette.requests import Request

from app import main
from app.services.tickets import TicketStatusDefinition


async def _dummy_receive() -> dict[str, Any]:
    return {"type": "http.request", "body": b"", "more_body": False}


def _make_request(path: str = "/admin/tickets/1") -> Request:
    scope = {"type": "http", "method": "GET", "path": path, "headers": []}
    request = Request(scope, _dummy_receive)
    return request


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio("asyncio")
async def test_ticket_detail_includes_booking_link_when_assigned_user_has_url(monkeypatch):
    """Test that booking link appears when assigned user has booking_link_url configured."""
    request = _make_request("/admin/tickets/1")
    user = {"id": 1, "is_super_admin": True}

    ticket = {
        "id": 1,
        "subject": "Test ticket",
        "description": "Test description",
        "status": "open",
        "priority": "normal",
        "company_id": 1,
        "requester_id": 1,
        "assigned_user_id": 2,  # Assigned to user 2
        "created_at": datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc),
        "updated_at": datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc),
    }

    assigned_user = {
        "id": 2,
        "email": "technician@example.com",
        "booking_link_url": "https://cal.com/technician",
    }

    class DummySanitized:
        def __init__(self, html: str):
            self.html = html
            self.text_content = html.strip()

    def fake_sanitize(value: str | None) -> DummySanitized:
        return DummySanitized(f"<p>{value or ''}</p>")

    statuses = [
        TicketStatusDefinition(tech_status="open", tech_label="Open", public_status="Open"),
    ]

    # Mock all required dependencies
    async def mock_get_user_by_id(user_id: int):
        if user_id == 2:
            return assigned_user
        return {"id": user_id, "email": f"user{user_id}@example.com"}

    monkeypatch.setattr(main, "sanitize_rich_text", fake_sanitize)
    monkeypatch.setattr(main.tickets_repo, "get_ticket", AsyncMock(return_value=ticket))
    monkeypatch.setattr(main.tickets_repo, "list_replies", AsyncMock(return_value=[]))
    monkeypatch.setattr(main.tickets_repo, "list_watchers", AsyncMock(return_value=[]))
    monkeypatch.setattr(main.tickets_repo, "list_ticket_assets", AsyncMock(return_value=[]))
    monkeypatch.setattr(main.user_repo, "get_user_by_id", mock_get_user_by_id)
    monkeypatch.setattr(main.company_repo, "get_company_by_id", AsyncMock(return_value={"id": 1, "name": "Test Company"}))
    monkeypatch.setattr(main.modules_service, "list_modules", AsyncMock(return_value=[]))
    monkeypatch.setattr(main.labour_types_service, "list_labour_types", AsyncMock(return_value=[]))
    monkeypatch.setattr(main.tickets_service, "list_status_definitions", AsyncMock(return_value=statuses))
    monkeypatch.setattr(main.company_repo, "list_companies", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        main.membership_repo,
        "list_users_with_permission",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(main.staff_repo, "list_enabled_staff_users", AsyncMock(return_value=[]))
    monkeypatch.setattr(main.assets_repo, "list_company_assets", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        main.knowledge_base_repo,
        "find_relevant_articles_for_ticket",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(
        main.service_status_service,
        "find_relevant_services_for_ticket",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(
        main.call_recordings_repo,
        "list_ticket_call_recordings",
        AsyncMock(return_value=[]),
    )

    captured: dict[str, Any] = {}

    async def fake_render_template(template_name, request_obj, user_obj, *, extra):
        captured["template"] = template_name
        captured["extra"] = extra
        return HTMLResponse("OK")

    monkeypatch.setattr(main, "_render_template", fake_render_template)

    response = await main._render_ticket_detail(request, user, ticket_id=1)

    assert isinstance(response, HTMLResponse)
    assert response.status_code == status.HTTP_200_OK
    assert captured["template"] == "admin/ticket_detail.html"

    # Verify that the assigned user with booking link is in context
    assert captured["extra"]["ticket_assigned_user"] == assigned_user
    assert captured["extra"]["ticket_assigned_user"]["booking_link_url"] == "https://cal.com/technician"


@pytest.mark.anyio("asyncio")
async def test_ticket_detail_without_booking_link_when_no_assigned_user(monkeypatch):
    """Test that booking link section is not shown when ticket has no assigned user."""
    request = _make_request("/admin/tickets/1")
    user = {"id": 1, "is_super_admin": True}

    ticket = {
        "id": 1,
        "subject": "Test ticket",
        "description": "Test description",
        "status": "open",
        "priority": "normal",
        "company_id": 1,
        "requester_id": 1,
        "assigned_user_id": None,  # No assigned user
        "created_at": datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc),
        "updated_at": datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc),
    }

    class DummySanitized:
        def __init__(self, html: str):
            self.html = html
            self.text_content = html.strip()

    def fake_sanitize(value: str | None) -> DummySanitized:
        return DummySanitized(f"<p>{value or ''}</p>")

    statuses = [
        TicketStatusDefinition(tech_status="open", tech_label="Open", public_status="Open"),
    ]

    monkeypatch.setattr(main, "sanitize_rich_text", fake_sanitize)
    monkeypatch.setattr(main.tickets_repo, "get_ticket", AsyncMock(return_value=ticket))
    monkeypatch.setattr(main.tickets_repo, "list_replies", AsyncMock(return_value=[]))
    monkeypatch.setattr(main.tickets_repo, "list_watchers", AsyncMock(return_value=[]))
    monkeypatch.setattr(main.tickets_repo, "list_ticket_assets", AsyncMock(return_value=[]))
    monkeypatch.setattr(main.user_repo, "get_user_by_id", AsyncMock(return_value={"id": 1, "email": "user@example.com"}))
    monkeypatch.setattr(main.company_repo, "get_company_by_id", AsyncMock(return_value={"id": 1, "name": "Test Company"}))
    monkeypatch.setattr(main.modules_service, "list_modules", AsyncMock(return_value=[]))
    monkeypatch.setattr(main.labour_types_service, "list_labour_types", AsyncMock(return_value=[]))
    monkeypatch.setattr(main.tickets_service, "list_status_definitions", AsyncMock(return_value=statuses))
    monkeypatch.setattr(main.company_repo, "list_companies", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        main.membership_repo,
        "list_users_with_permission",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(main.staff_repo, "list_enabled_staff_users", AsyncMock(return_value=[]))
    monkeypatch.setattr(main.assets_repo, "list_company_assets", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        main.knowledge_base_repo,
        "find_relevant_articles_for_ticket",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(
        main.service_status_service,
        "find_relevant_services_for_ticket",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(
        main.call_recordings_repo,
        "list_ticket_call_recordings",
        AsyncMock(return_value=[]),
    )

    captured: dict[str, Any] = {}

    async def fake_render_template(template_name, request_obj, user_obj, *, extra):
        captured["template"] = template_name
        captured["extra"] = extra
        return HTMLResponse("OK")

    monkeypatch.setattr(main, "_render_template", fake_render_template)

    response = await main._render_ticket_detail(request, user, ticket_id=1)

    assert isinstance(response, HTMLResponse)
    assert response.status_code == status.HTTP_200_OK
    
    # Verify that ticket_assigned_user is None when no user is assigned
    assert captured["extra"]["ticket_assigned_user"] is None
