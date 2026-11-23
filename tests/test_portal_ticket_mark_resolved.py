"""Tests for the portal "My Issue Is Resolved" action."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app import main as main_module
from app.features.tickets import portal_routes
from app.services import tickets as tickets_service
from app.services.tickets import TicketStatusDefinition


STATUSES = [
    TicketStatusDefinition(tech_status="open", tech_label="Open", public_status="Open"),
    TicketStatusDefinition(tech_status="resolved", tech_label="Resolved", public_status="Resolved"),
    TicketStatusDefinition(tech_status="closed", tech_label="Closed", public_status="Closed"),
    TicketStatusDefinition(
        tech_status="user_resolved", tech_label="User resolved", public_status="Resolved"
    ),
]


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


async def _receive() -> dict[str, Any]:
    return {"type": "http.request", "body": b"", "more_body": False}


def _request(ticket_id: int = 42) -> Request:
    scope = {
        "type": "http",
        "method": "POST",
        "path": f"/tickets/{ticket_id}/resolve",
        "headers": [],
        "query_string": b"",
    }
    return Request(scope, _receive)


def _settings(value: str) -> SimpleNamespace:
    return SimpleNamespace(ticket_user_resolved_status=value)


@pytest.mark.anyio("asyncio")
@pytest.mark.parametrize(
    ("configured", "expected"),
    [
        ("resolved", "resolved"),
        ("User Resolved", "user_resolved"),
        ("", "resolved"),
        ("not_a_status", "resolved"),
    ],
)
async def test_resolve_user_resolved_status(monkeypatch, configured, expected):
    monkeypatch.setattr(tickets_service, "get_settings", lambda: _settings(configured))

    assert await tickets_service.resolve_user_resolved_status(STATUSES) == expected


def _patch_route(monkeypatch, *, ticket: dict[str, Any], user: dict[str, Any], helpdesk: bool = False):
    monkeypatch.setattr(
        main_module,
        "_require_menu_page_access",
        AsyncMock(return_value=(user, None)),
    )
    monkeypatch.setattr(main_module, "_has_admin_technician_access", AsyncMock(return_value=helpdesk))
    monkeypatch.setattr(main_module, "_has_menu_page_access", AsyncMock(return_value=False))
    monkeypatch.setattr(portal_routes.tickets_repo, "get_ticket", AsyncMock(return_value=ticket))
    set_status = AsyncMock(return_value={**ticket, "status": "resolved"})
    monkeypatch.setattr(portal_routes.tickets_repo, "set_ticket_status", set_status)
    monkeypatch.setattr(portal_routes, "parse_csrf_form", AsyncMock(return_value={}))
    monkeypatch.setattr(
        tickets_service, "list_status_definitions", AsyncMock(return_value=STATUSES)
    )
    monkeypatch.setattr(tickets_service, "get_settings", lambda: _settings("resolved"))
    audit = AsyncMock()
    monkeypatch.setattr(portal_routes.audit_service, "record", audit)
    emit = AsyncMock()
    monkeypatch.setattr(portal_routes.tickets_service, "emit_ticket_updated_event", emit)
    broadcast = AsyncMock()
    monkeypatch.setattr(portal_routes.tickets_service, "broadcast_ticket_event", broadcast)
    return set_status, audit, emit, broadcast


@pytest.mark.anyio("asyncio")
async def test_requester_marks_ticket_resolved(monkeypatch):
    ticket = {"id": 42, "status": "open", "requester_id": 5, "company_id": 3}
    user = {"id": 5, "is_super_admin": False}
    set_status, audit, emit, broadcast = _patch_route(monkeypatch, ticket=ticket, user=user)

    response = await portal_routes.portal_ticket_mark_resolved(_request(), 42)

    assert response.status_code == 303
    assert response.headers["location"] == "/tickets/42"
    set_status.assert_awaited_once_with(42, "resolved")
    audit.assert_awaited_once()
    assert audit.await_args.kwargs["action"] == "tickets.resolved_by_user"
    assert audit.await_args.kwargs["after"] == {"status": "resolved"}
    emit.assert_awaited_once()
    assert emit.await_args.kwargs["actor_type"] == "requester"
    broadcast.assert_awaited_once_with(action="updated", ticket_id=42)


@pytest.mark.anyio("asyncio")
async def test_mark_resolved_uses_configured_status(monkeypatch):
    ticket = {"id": 42, "status": "open", "requester_id": 5, "company_id": 3}
    user = {"id": 5, "is_super_admin": False}
    set_status, *_ = _patch_route(monkeypatch, ticket=ticket, user=user)
    monkeypatch.setattr(tickets_service, "get_settings", lambda: _settings("user_resolved"))

    await portal_routes.portal_ticket_mark_resolved(_request(), 42)

    set_status.assert_awaited_once_with(42, "user_resolved")


@pytest.mark.anyio("asyncio")
@pytest.mark.parametrize("current", ["resolved", "closed"])
async def test_mark_resolved_is_noop_when_already_resolved(monkeypatch, current):
    ticket = {"id": 42, "status": current, "requester_id": 5, "company_id": 3}
    user = {"id": 5, "is_super_admin": False}
    set_status, audit, emit, _ = _patch_route(monkeypatch, ticket=ticket, user=user)

    response = await portal_routes.portal_ticket_mark_resolved(_request(), 42)

    assert response.status_code == 303
    set_status.assert_not_awaited()
    audit.assert_not_awaited()
    emit.assert_not_awaited()


@pytest.mark.anyio("asyncio")
async def test_unrelated_user_cannot_mark_resolved(monkeypatch):
    ticket = {"id": 42, "status": "open", "requester_id": 99, "company_id": 3}
    user = {"id": 5, "is_super_admin": False}
    set_status, *_ = _patch_route(monkeypatch, ticket=ticket, user=user)

    with pytest.raises(HTTPException) as excinfo:
        await portal_routes.portal_ticket_mark_resolved(_request(), 42)

    assert excinfo.value.status_code == 404
    set_status.assert_not_awaited()


def test_detail_template_renders_resolve_button_when_allowed():
    template = Path("app/templates/tickets/detail.html").read_text(encoding="utf-8")

    assert "{% if can_mark_resolved %}" in template
    assert 'action="/tickets/{{ ticket.id }}/resolve"' in template
    assert "My Issue Is Resolved" in template
