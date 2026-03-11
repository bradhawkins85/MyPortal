"""Tests for the tickets.details_updated automation trigger event."""
from __future__ import annotations

from typing import Any

import pytest

from app.services import automations as automations_service
from app.services import tickets as tickets_service


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def test_ticket_details_updated_in_trigger_events():
    """Confirm tickets.details_updated is listed as an available trigger event."""
    events = automations_service.list_trigger_events()
    values = [e["value"] for e in events]
    assert "tickets.details_updated" in values


def test_ticket_updated_still_in_trigger_events():
    """Confirm tickets.updated remains available (used by Post Reply)."""
    events = automations_service.list_trigger_events()
    values = [e["value"] for e in events]
    assert "tickets.updated" in values


def test_ticket_details_updated_label():
    """Confirm the label for the new event is human-readable."""
    events = automations_service.list_trigger_events()
    label_map = {e["value"]: e["label"] for e in events}
    assert label_map["tickets.details_updated"] == "Ticket Details Updated"


@pytest.mark.anyio
async def test_emit_ticket_details_updated_event_fires_correct_event(monkeypatch):
    """emit_ticket_details_updated_event should fire tickets.details_updated, not tickets.updated."""
    events_fired: list[str] = []

    async def fake_get_ticket(ticket_id: int) -> dict[str, Any]:
        return {
            "id": ticket_id,
            "subject": "Test",
            "status": "open",
            "priority": "normal",
            "company_id": None,
            "requester_id": None,
            "assigned_user_id": None,
        }

    async def fake_get_company(company_id: int) -> None:
        return None

    async def fake_get_user(user_id: int) -> None:
        return None

    async def fake_list_watchers(ticket_id: int) -> list:
        return []

    async def fake_list_replies(ticket_id: int, include_internal: bool = True) -> list:
        return []

    async def fake_handle_event(event: str, context: dict[str, Any]) -> None:
        events_fired.append(event)

    from app.repositories import tickets as tickets_repo
    from app.repositories import companies as company_repo
    from app.repositories import users as user_repo

    monkeypatch.setattr(tickets_repo, "get_ticket", fake_get_ticket)
    monkeypatch.setattr(tickets_repo, "list_watchers", fake_list_watchers)
    monkeypatch.setattr(tickets_repo, "list_replies", fake_list_replies)
    monkeypatch.setattr(company_repo, "get_company_by_id", fake_get_company)
    monkeypatch.setattr(user_repo, "get_user_by_id", fake_get_user)
    monkeypatch.setattr(automations_service, "handle_event", fake_handle_event)

    await tickets_service.emit_ticket_details_updated_event(
        1,
        actor_type="technician",
        actor={"id": 99, "email": "tech@example.com", "display_name": "Tech"},
    )

    assert events_fired == ["tickets.details_updated"], (
        f"Expected ['tickets.details_updated'], got {events_fired}"
    )


@pytest.mark.anyio
async def test_emit_ticket_updated_event_fires_tickets_updated(monkeypatch):
    """emit_ticket_updated_event should still fire tickets.updated (used by Post Reply)."""
    events_fired: list[str] = []

    async def fake_get_ticket(ticket_id: int) -> dict[str, Any]:
        return {
            "id": ticket_id,
            "subject": "Test",
            "status": "open",
            "priority": "normal",
            "company_id": None,
            "requester_id": None,
            "assigned_user_id": None,
        }

    async def fake_get_company(company_id: int) -> None:
        return None

    async def fake_get_user(user_id: int) -> None:
        return None

    async def fake_list_watchers(ticket_id: int) -> list:
        return []

    async def fake_list_replies(ticket_id: int, include_internal: bool = True) -> list:
        return []

    async def fake_handle_event(event: str, context: dict[str, Any]) -> None:
        events_fired.append(event)

    from app.repositories import tickets as tickets_repo
    from app.repositories import companies as company_repo
    from app.repositories import users as user_repo

    monkeypatch.setattr(tickets_repo, "get_ticket", fake_get_ticket)
    monkeypatch.setattr(tickets_repo, "list_watchers", fake_list_watchers)
    monkeypatch.setattr(tickets_repo, "list_replies", fake_list_replies)
    monkeypatch.setattr(company_repo, "get_company_by_id", fake_get_company)
    monkeypatch.setattr(user_repo, "get_user_by_id", fake_get_user)
    monkeypatch.setattr(automations_service, "handle_event", fake_handle_event)

    await tickets_service.emit_ticket_updated_event(
        1,
        actor_type="technician",
        actor={"id": 99, "email": "tech@example.com", "display_name": "Tech"},
    )

    assert events_fired == ["tickets.updated"], (
        f"Expected ['tickets.updated'], got {events_fired}"
    )
