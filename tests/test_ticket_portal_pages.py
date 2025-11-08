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


def _make_request(path: str = "/tickets", query_string: str = "") -> Request:
    scope = {"type": "http", "method": "GET", "path": path, "headers": [], "query_string": query_string.encode()}
    request = Request(scope, _dummy_receive)
    return request


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio("asyncio")
async def test_render_portal_tickets_page_formats_results(monkeypatch):
    request = _make_request()
    request.state.active_company_id = 22
    user = {"id": 5, "company_id": 22}

    statuses = [
        TicketStatusDefinition(tech_status="open", tech_label="Open", public_status="Open"),
        TicketStatusDefinition(tech_status="closed", tech_label="Closed", public_status="Closed"),
    ]
    listed_tickets = [
        {
            "id": 41,
            "subject": "Printer offline",
            "status": "open",
            "priority": "high",
            "company_id": 22,
            "updated_at": datetime(2025, 1, 10, 9, 30, tzinfo=timezone.utc),
            "created_at": datetime(2025, 1, 9, 16, 45, tzinfo=timezone.utc),
        }
    ]

    monkeypatch.setattr(
        main.company_access,
        "list_accessible_companies",
        AsyncMock(return_value=[{"company_id": 22, "company_name": "Example"}]),
    )
    list_mock = AsyncMock(return_value=listed_tickets)
    count_mock = AsyncMock(return_value=1)
    monkeypatch.setattr(main.tickets_repo, "list_tickets_for_user", list_mock)
    monkeypatch.setattr(main.tickets_repo, "count_tickets_for_user", count_mock)
    monkeypatch.setattr(main.tickets_service, "list_status_definitions", AsyncMock(return_value=statuses))

    captured: dict[str, Any] = {}

    async def fake_render_template(template_name, request_obj, user_obj, *, extra):
        captured["template"] = template_name
        captured["extra"] = extra
        return HTMLResponse("OK")

    monkeypatch.setattr(main, "_render_template", fake_render_template)

    response = await main._render_portal_tickets_page(
        request,
        user,
        status_filter="open",
        search_term=" printer ",
    )

    assert isinstance(response, HTMLResponse)
    assert response.status_code == status.HTTP_200_OK
    list_mock.assert_awaited_once()
    count_mock.assert_awaited_once()
    assert list_mock.await_args.kwargs["status"] == ["open"]
    assert count_mock.await_args.kwargs["status"] == ["open"]
    assert captured["template"] == "tickets/index.html"

    extra = captured["extra"]
    assert extra["tickets_total"] == 1
    assert extra["status_filter"] == "open"
    assert extra["search_term"] == "printer"
    assert extra["filters_active"] is True
    assert extra["tickets"] == [
        {
            "id": 41,
            "subject": "Printer offline",
            "status": "open",
            "status_label": "Open",
            "status_badge": "badge--warning",
            "priority_label": "High",
            "company_name": "Example",
            "company_id": 22,
            "updated_iso": "2025-01-10T09:30:00+00:00",
            "created_iso": "2025-01-09T16:45:00+00:00",
        }
    ]
    assert extra["status_summary"] == [
        {"slug": "open", "label": "Open", "count": 1},
    ]
    option_labels = {option["value"]: option["label"] for option in extra["status_options"]}
    assert option_labels == {"open": "Open", "closed": "Closed"}


@pytest.mark.anyio("asyncio")
async def test_render_portal_tickets_page_merges_duplicate_status_labels(monkeypatch):
    request = _make_request()
    user = {"id": 9, "company_id": 77}

    statuses = [
        TicketStatusDefinition(
            tech_status="waiting_on_client",
            tech_label="Waiting on client",
            public_status="Waiting on client",
        ),
        TicketStatusDefinition(
            tech_status="pending_client",
            tech_label="Pending client",
            public_status="Waiting on client",
        ),
        TicketStatusDefinition(
            tech_status="open",
            tech_label="Open",
            public_status="Open",
        ),
    ]

    listed_tickets = [
        {
            "id": 71,
            "subject": "Need more info",
            "status": "waiting_on_client",
            "priority": "normal",
            "company_id": 77,
            "updated_at": datetime(2025, 3, 10, 8, 15, tzinfo=timezone.utc),
            "created_at": datetime(2025, 3, 9, 11, 0, tzinfo=timezone.utc),
        },
        {
            "id": 72,
            "subject": "Customer replied",
            "status": "pending_client",
            "priority": "high",
            "company_id": 77,
            "updated_at": datetime(2025, 3, 10, 9, 0, tzinfo=timezone.utc),
            "created_at": datetime(2025, 3, 9, 12, 30, tzinfo=timezone.utc),
        },
    ]

    combined_value = "pending_client,waiting_on_client"

    monkeypatch.setattr(
        main.company_access,
        "list_accessible_companies",
        AsyncMock(return_value=[{"company_id": 77, "company_name": "Example"}]),
    )
    list_mock = AsyncMock(return_value=listed_tickets)
    count_mock = AsyncMock(return_value=2)
    monkeypatch.setattr(main.tickets_repo, "list_tickets_for_user", list_mock)
    monkeypatch.setattr(main.tickets_repo, "count_tickets_for_user", count_mock)
    monkeypatch.setattr(main.tickets_service, "list_status_definitions", AsyncMock(return_value=statuses))

    captured: dict[str, Any] = {}

    async def fake_render_template(template_name, request_obj, user_obj, *, extra):
        captured["template"] = template_name
        captured["extra"] = extra
        return HTMLResponse("OK")

    monkeypatch.setattr(main, "_render_template", fake_render_template)

    response = await main._render_portal_tickets_page(
        request,
        user,
        status_filter=combined_value,
    )

    assert isinstance(response, HTMLResponse)
    assert response.status_code == status.HTTP_200_OK
    list_mock.assert_awaited_once()
    count_mock.assert_awaited_once()
    assert list_mock.await_args.kwargs["status"] == [
        "pending_client",
        "waiting_on_client",
    ]
    assert count_mock.await_args.kwargs["status"] == [
        "pending_client",
        "waiting_on_client",
    ]

    extra = captured["extra"]
    assert extra["status_filter"] == combined_value
    assert extra["filters_active"] is True
    assert extra["status_summary"] == [
        {"slug": combined_value, "label": "Waiting on client", "count": 2}
    ]

    option_values = extra["status_options"]
    assert option_values == [
        {"value": "open", "label": "Open"},
        {"value": combined_value, "label": "Waiting on client"},
    ]


@pytest.mark.anyio("asyncio")
async def test_render_portal_ticket_detail_includes_replies(monkeypatch):
    request = _make_request("/tickets/41")
    user = {"id": 5, "is_super_admin": False}

    ticket = {
        "id": 41,
        "subject": "Printer offline",
        "description": "Needs toner",
        "status": "open",
        "priority": "high",
        "company_id": 22,
        "requester_id": 5,
        "assigned_user_id": 9,
        "created_at": datetime(2025, 1, 9, 16, 45, tzinfo=timezone.utc),
        "updated_at": datetime(2025, 1, 10, 9, 30, tzinfo=timezone.utc),
    }
    replies = [
        {
            "id": 101,
            "author_id": 9,
            "body": "Replaced toner",
            "minutes_spent": 15,
            "is_billable": True,
            "is_internal": False,
            "created_at": datetime(2025, 1, 10, 10, 0, tzinfo=timezone.utc),
        }
    ]

    class DummySanitized:
        def __init__(self, html: str, has_content: bool):
            self.html = html
            self.text_content = html
            self.has_rich_content = has_content

    def fake_sanitize(value: str | None) -> DummySanitized:
        text = (value or "").strip()
        if not text:
            return DummySanitized("", False)
        return DummySanitized(f"<p>{text}</p>", True)

    async def fake_get_user_by_id(identifier: int) -> dict[str, Any] | None:
        if identifier == 5:
            return {"id": 5, "first_name": "Pat", "last_name": "Requester", "email": "pat@example.com"}
        if identifier == 9:
            return {"id": 9, "first_name": "Taylor", "last_name": "Agent", "email": "agent@example.com"}
        return None

    monkeypatch.setattr(main, "sanitize_rich_text", fake_sanitize)
    monkeypatch.setattr(main.tickets_repo, "get_ticket", AsyncMock(return_value=ticket))
    monkeypatch.setattr(main.tickets_repo, "list_replies", AsyncMock(return_value=replies))
    monkeypatch.setattr(main.tickets_repo, "is_ticket_watcher", AsyncMock())
    monkeypatch.setattr(main.tickets_service, "get_public_status_map", AsyncMock(return_value={"open": "Open"}))
    monkeypatch.setattr(
        main.tickets_service,
        "format_reply_time_summary",
        lambda minutes, is_billable, labour=None: f"{minutes} minutes" if minutes is not None else "",
    )
    monkeypatch.setattr(main, "_is_helpdesk_technician", AsyncMock(return_value=False))
    monkeypatch.setattr(main.company_repo, "get_company_by_id", AsyncMock(return_value={"id": 22, "name": "Example"}))
    monkeypatch.setattr(main.user_repo, "get_user_by_id", AsyncMock(side_effect=fake_get_user_by_id))

    captured: dict[str, Any] = {}

    async def fake_render_template(template_name, request_obj, user_obj, *, extra):
        captured["template"] = template_name
        captured["extra"] = extra
        return HTMLResponse("OK")

    monkeypatch.setattr(main, "_render_template", fake_render_template)

    response = await main._render_portal_ticket_detail(request, user, ticket_id=41)

    assert isinstance(response, HTMLResponse)
    assert response.status_code == status.HTTP_200_OK
    assert captured["template"] == "tickets/detail.html"

    extra = captured["extra"]
    ticket_payload = extra["ticket"]
    assert ticket_payload["status_badge"] == "badge--warning"
    assert ticket_payload["priority_label"] == "High"
    assert ticket_payload["description_html"] == "<p>Needs toner</p>"
    assert ticket_payload["description_has_content"] is True
    assert ticket_payload["requester_label"] == "Pat Requester"
    assert ticket_payload["assigned_label"] == "Taylor Agent"
    assert extra["can_reply"] is True
    assert extra["ticket_replies"] == [
        {
            "id": 101,
            "author": {"id": 9, "first_name": "Taylor", "last_name": "Agent", "email": "agent@example.com"},
            "author_label": "Taylor Agent",
            "created_iso": "2025-01-10T10:00:00+00:00",
            "body_html": "<p>Replaced toner</p>",
            "has_content": True,
            "time_summary": "15 minutes",
            "is_internal": False,
            "labour_type_name": None,
            "labour_type_code": None,
        }
    ]


@pytest.mark.anyio("asyncio")
async def test_render_portal_ticket_detail_denies_unrelated_user(monkeypatch):
    request = _make_request("/tickets/41")
    user = {"id": 99, "is_super_admin": False}

    ticket = {
        "id": 41,
        "status": "open",
        "priority": "normal",
        "company_id": 22,
        "requester_id": 5,
        "assigned_user_id": None,
        "description": "",
        "created_at": datetime(2025, 1, 9, 16, 45, tzinfo=timezone.utc),
        "updated_at": datetime(2025, 1, 10, 9, 30, tzinfo=timezone.utc),
    }

    list_replies_mock = AsyncMock(return_value=[])

    monkeypatch.setattr(main.tickets_repo, "get_ticket", AsyncMock(return_value=ticket))
    monkeypatch.setattr(main.tickets_repo, "list_replies", list_replies_mock)
    monkeypatch.setattr(main.tickets_repo, "is_ticket_watcher", AsyncMock(return_value=False))
    monkeypatch.setattr(main, "_is_helpdesk_technician", AsyncMock(return_value=False))

    with pytest.raises(main.HTTPException) as exc_info:
        await main._render_portal_ticket_detail(request, user, ticket_id=41)

    assert exc_info.value.status_code == status.HTTP_404_NOT_FOUND
    list_replies_mock.assert_not_awaited()


@pytest.mark.anyio("asyncio")
async def test_portal_tickets_page_loads_default_view(monkeypatch):
    """Test that default view is automatically loaded when no filters are provided"""
    request = _make_request()
    request.state.active_company_id = 30
    user = {"id": 10, "company_id": 30}
    
    # Default view with status filter
    default_view = {
        "id": 1,
        "user_id": 10,
        "name": "Default",
        "filters": {
            "status": ["open", "pending"],
            "search": "urgent"
        },
        "is_default": True,
    }
    
    statuses = [
        TicketStatusDefinition(tech_status="open", tech_label="Open", public_status="Open"),
        TicketStatusDefinition(tech_status="pending", tech_label="Pending", public_status="Pending"),
        TicketStatusDefinition(tech_status="closed", tech_label="Closed", public_status="Closed"),
    ]
    
    listed_tickets = [
        {
            "id": 50,
            "subject": "Urgent issue",
            "status": "open",
            "priority": "high",
            "company_id": 30,
            "updated_at": datetime(2025, 1, 15, 10, 0, tzinfo=timezone.utc),
            "created_at": datetime(2025, 1, 14, 9, 0, tzinfo=timezone.utc),
        }
    ]
    
    monkeypatch.setattr(
        main.company_access,
        "list_accessible_companies",
        AsyncMock(return_value=[{"company_id": 30, "company_name": "Test Co"}]),
    )
    monkeypatch.setattr(main.tickets_repo, "list_tickets_for_user", AsyncMock(return_value=listed_tickets))
    monkeypatch.setattr(main.tickets_repo, "count_tickets_for_user", AsyncMock(return_value=1))
    monkeypatch.setattr(main.tickets_service, "list_status_definitions", AsyncMock(return_value=statuses))
    monkeypatch.setattr(main.ticket_views_repo, "get_default_view", AsyncMock(return_value=default_view))
    
    async def fake_require_auth(request_obj):
        return user, None
    
    monkeypatch.setattr(main, "_require_authenticated_user", fake_require_auth)
    
    captured: dict[str, Any] = {}
    
    async def fake_render_template(template_name, request_obj, user_obj, *, extra):
        captured["template"] = template_name
        captured["extra"] = extra
        return HTMLResponse("OK")
    
    monkeypatch.setattr(main, "_render_template", fake_render_template)
    
    # Call the endpoint without any query parameters
    response = await main.portal_tickets_page(request)
    
    assert isinstance(response, HTMLResponse)
    assert response.status_code == status.HTTP_200_OK
    
    # Verify that the default view was loaded
    main.ticket_views_repo.get_default_view.assert_awaited_once_with(10)
    
    # Verify that filters from default view were applied
    extra = captured["extra"]
    assert extra["status_filter"] == "open,pending"
    assert extra["search_term"] == "urgent"
    assert extra["filters_active"] is True
