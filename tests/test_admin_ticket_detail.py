from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, patch
import io
from urllib.parse import parse_qs, unquote, urlparse

import pytest
from fastapi import status
from fastapi.responses import HTMLResponse
from starlette.requests import Request
from starlette.datastructures import FormData, UploadFile

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
    monkeypatch.setattr(main.attachments_repo, "list_attachments", AsyncMock(return_value=[]))
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

    # Use patch() for the two lazy-imported DB calls inside _render_ticket_detail
    with (
        patch(
            "app.repositories.call_recordings.list_ticket_call_recordings",
            new=AsyncMock(return_value=[]),
        ),
        patch(
            "app.services.service_status.find_relevant_services_for_ticket",
            new=AsyncMock(return_value=[]),
        ),
    ):
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


@pytest.mark.anyio("asyncio")
async def test_render_ticket_detail_includes_attachments(monkeypatch):
    """Ticket detail should include formatted attachments for the admin view."""
    request = _make_request("/admin/tickets/2")
    user = {"id": 1, "is_super_admin": True}

    ticket = {
        "id": 2,
        "subject": "Attachment ticket",
        "description": "Details",
        "status": "open",
        "priority": "normal",
        "company_id": 1,
        "requester_id": 1,
        "assigned_user_id": None,
        "created_at": datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc),
        "updated_at": datetime(2025, 1, 1, 12, 0, tzinfo=timezone.utc),
    }

    class DummySanitized:
        def __init__(self, html: str):
            self.html = html
            self.text_content = html.strip()

    def fake_sanitize(value: str | None) -> DummySanitized:
        return DummySanitized(f"<p>{value or ''}</p>")

    attachments = [
        {
            "id": 5,
            "ticket_id": 2,
            "filename": "secure.pdf",
            "original_filename": "report.pdf",
            "file_size": 2048,
            "access_level": "closed",
            "uploaded_at": datetime(2025, 1, 1, 13, 0, tzinfo=timezone.utc),
        }
    ]

    monkeypatch.setattr(main, "sanitize_rich_text", fake_sanitize)
    monkeypatch.setattr(main.tickets_repo, "get_ticket", AsyncMock(return_value=ticket))
    monkeypatch.setattr(main.tickets_repo, "list_replies", AsyncMock(return_value=[]))
    monkeypatch.setattr(main.tickets_repo, "list_watchers", AsyncMock(return_value=[]))
    monkeypatch.setattr(main.attachments_repo, "list_attachments", AsyncMock(return_value=attachments))
    monkeypatch.setattr(main.tickets_repo, "list_ticket_assets", AsyncMock(return_value=[]))
    monkeypatch.setattr(main.user_repo, "get_user_by_id", AsyncMock(return_value={"id": 1, "email": "test@example.com"}))
    monkeypatch.setattr(main.company_repo, "get_company_by_id", AsyncMock(return_value={"id": 1, "name": "Test Company"}))
    monkeypatch.setattr(main.modules_service, "list_modules", AsyncMock(return_value=[]))
    monkeypatch.setattr(main.labour_types_service, "list_labour_types", AsyncMock(return_value=[]))
    monkeypatch.setattr(main.tickets_service, "list_status_definitions", AsyncMock(return_value=[]))
    monkeypatch.setattr(main.company_repo, "list_companies", AsyncMock(return_value=[]))
    monkeypatch.setattr(main.membership_repo, "list_users_with_permission", AsyncMock(return_value=[]))
    monkeypatch.setattr(main.staff_repo, "list_enabled_staff_users", AsyncMock(return_value=[]))
    monkeypatch.setattr(main.assets_repo, "list_company_assets", AsyncMock(return_value=[]))
    monkeypatch.setattr(main.knowledge_base_repo, "find_relevant_articles_for_ticket", AsyncMock(return_value=[]))

    captured: dict[str, Any] = {}

    async def fake_render_template(template_name, request_obj, user_obj, *, extra):
        captured["template"] = template_name
        captured["extra"] = extra
        return HTMLResponse("OK")

    monkeypatch.setattr(main, "_render_template", fake_render_template)

    with patch(
        "app.repositories.call_recordings.list_ticket_call_recordings",
        new=AsyncMock(return_value=[]),
    ):
        response = await main._render_ticket_detail(request, user, ticket_id=2)

    assert isinstance(response, HTMLResponse)
    assert response.status_code == status.HTTP_200_OK
    assert captured["template"] == "admin/ticket_detail.html"
    assert captured["extra"]["ticket_attachments"][0]["id"] == 5
    assert captured["extra"]["ticket_attachments"][0]["file_size"] == 2048
    assert captured["extra"]["ticket_attachments"][0]["uploaded_iso"].startswith("2025-01-01T13:00:00")


@pytest.mark.anyio("asyncio")
async def test_admin_reply_saves_attachments(monkeypatch):
    """Posting an admin reply should persist uploaded attachments."""

    class DummySanitized:
        def __init__(self, html: str):
            self.html = html
            self.text_content = html
            self.has_rich_content = True

    upload = UploadFile(filename="note.txt", file=io.BytesIO(b"hello"))
    form_data = FormData(
        [
            ("body", "<p>reply</p>"),
            ("attachments", upload),
        ]
    )

    class DummyRequest:
        def __init__(self) -> None:
            self.url = type("url", (), {"path": "/admin/tickets/3"})()

        async def form(self):
            return form_data

    request = DummyRequest()

    monkeypatch.setattr(
        main, "_require_helpdesk_page", AsyncMock(return_value=({"id": 9, "is_super_admin": True}, None))
    )
    monkeypatch.setattr(main, "sanitize_rich_text", lambda value: DummySanitized(str(value or "")))
    monkeypatch.setattr(
        main.tickets_repo,
        "get_ticket",
        AsyncMock(return_value={"id": 3, "xero_invoice_number": None}),
    )
    monkeypatch.setattr(main.tickets_repo, "create_reply", AsyncMock(return_value=None))
    monkeypatch.setattr(main.tickets_repo, "add_watcher", AsyncMock(return_value=None))
    monkeypatch.setattr(main.tickets_service, "refresh_ticket_ai_summary", AsyncMock(return_value=None))
    monkeypatch.setattr(main.tickets_service, "refresh_ticket_ai_tags", AsyncMock(return_value=None))
    monkeypatch.setattr(main.tickets_service, "broadcast_ticket_event", AsyncMock(return_value=None))
    monkeypatch.setattr(main.tickets_service, "emit_ticket_updated_event", AsyncMock(return_value=None))

    save_mock = AsyncMock(return_value={"id": 1})
    monkeypatch.setattr(main.attachments_service, "save_uploaded_file", save_mock)

    response = await main.admin_create_ticket_reply(3, request)

    assert response.status_code == status.HTTP_303_SEE_OTHER
    assert "/admin/tickets/3" in response.headers.get("location", "")
    assert save_mock.await_count == 1
    called_args = save_mock.await_args.kwargs
    assert called_args["ticket_id"] == 3
    saved_file = called_args["file"]
    assert saved_file.filename == "note.txt"
    saved_file.file.seek(0)
    assert saved_file.file.read() == b"hello"


@pytest.mark.anyio("asyncio")
async def test_admin_reply_reports_failed_attachments(monkeypatch):
    """Failed attachment uploads should surface in the success message."""

    class DummySanitized:
        def __init__(self, html: str):
            self.html = html
            self.text_content = html
            self.has_rich_content = True

    upload = UploadFile(filename="note.txt", file=io.BytesIO(b"hello"))
    form_data = FormData(
        [
            ("body", "<p>reply</p>"),
            ("attachments", upload),
        ]
    )

    class DummyRequest:
        def __init__(self) -> None:
            self.url = type("url", (), {"path": "/admin/tickets/4"})()

        async def form(self):
            return form_data

    request = DummyRequest()

    monkeypatch.setattr(
        main, "_require_helpdesk_page", AsyncMock(return_value=({"id": 9, "is_super_admin": True}, None))
    )
    monkeypatch.setattr(main, "sanitize_rich_text", lambda value: DummySanitized(str(value or "")))
    monkeypatch.setattr(
        main.tickets_repo,
        "get_ticket",
        AsyncMock(return_value={"id": 4, "xero_invoice_number": None}),
    )
    monkeypatch.setattr(main.tickets_repo, "create_reply", AsyncMock(return_value=None))
    monkeypatch.setattr(main.tickets_repo, "add_watcher", AsyncMock(return_value=None))
    monkeypatch.setattr(main.tickets_service, "refresh_ticket_ai_summary", AsyncMock(return_value=None))
    monkeypatch.setattr(main.tickets_service, "refresh_ticket_ai_tags", AsyncMock(return_value=None))
    monkeypatch.setattr(main.tickets_service, "broadcast_ticket_event", AsyncMock(return_value=None))
    monkeypatch.setattr(main.tickets_service, "emit_ticket_updated_event", AsyncMock(return_value=None))

    save_mock = AsyncMock(side_effect=RuntimeError("boom"))
    monkeypatch.setattr(main.attachments_service, "save_uploaded_file", save_mock)

    response = await main.admin_create_ticket_reply(4, request)

    assert response.status_code == status.HTTP_303_SEE_OTHER
    parsed = urlparse(response.headers.get("location", ""))
    success_values = parse_qs(parsed.query).get("success", [])
    assert any("failed to upload" in unquote(value) for value in success_values)
    assert save_mock.await_count == 1
