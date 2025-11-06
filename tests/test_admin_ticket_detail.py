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
async def test_render_ticket_detail_with_tactical_module_and_ai_tags(monkeypatch):
    """Test that settings.ai_tag_threshold works even when tactical module has settings dict."""
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
        "assigned_user_id": 1,
        "ai_tags": ["networking", "router", "configuration"],
        "created_at": datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc),
        "updated_at": datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc),
    }

    # Mock modules including tacticalrmm with settings
    modules = [
        {
            "slug": "tacticalrmm",
            "name": "TacticalRMM",
            "enabled": True,
            "settings": {
                "base_url": "https://tactical.example.com",
                "api_key": "test-key",
            },
        }
    ]

    relevant_kb_articles = [
        {"id": 1, "title": "Router Configuration Guide"},
    ]

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
    monkeypatch.setattr(main.user_repo, "get_user_by_id", AsyncMock(return_value={"id": 1, "email": "test@example.com"}))
    monkeypatch.setattr(main.company_repo, "get_company_by_id", AsyncMock(return_value={"id": 1, "name": "Test Company"}))
    monkeypatch.setattr(main.modules_service, "list_modules", AsyncMock(return_value=modules))
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
        AsyncMock(return_value=relevant_kb_articles),
    )

    captured: dict[str, Any] = {}

    async def fake_render_template(template_name, request_obj, user_obj, *, extra):
        captured["template"] = template_name
        captured["extra"] = extra
        return HTMLResponse("OK")

    monkeypatch.setattr(main, "_render_template", fake_render_template)

    # This should not raise AttributeError anymore
    response = await main._render_ticket_detail(request, user, ticket_id=1)

    assert isinstance(response, HTMLResponse)
    assert response.status_code == status.HTTP_200_OK
    assert captured["template"] == "admin/ticket_detail.html"

    # Verify that knowledge base search was called with the correct threshold
    main.knowledge_base_repo.find_relevant_articles_for_ticket.assert_awaited_once()
    call_args = main.knowledge_base_repo.find_relevant_articles_for_ticket.await_args
    assert call_args.kwargs["ticket_ai_tags"] == ["networking", "router", "configuration"]
    # Default ai_tag_threshold from Settings is 1
    assert call_args.kwargs["min_matching_tags"] == 1

    # Verify relevant articles were included in the response
    assert captured["extra"]["relevant_kb_articles"] == relevant_kb_articles
    # Verify tactical base URL was extracted correctly
    assert captured["extra"]["tacticalrmm_base_url"] == "https://tactical.example.com"
