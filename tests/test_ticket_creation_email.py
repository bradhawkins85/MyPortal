"""Tests for ticket creation notification routing."""
from __future__ import annotations

import pytest

from app.services import tickets as tickets_service
from app.services import notifications as notifications_service
from app.services import email as email_service
from app.services import automations as automations_service


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_send_ticket_creation_email_is_noop_for_registered_requester(monkeypatch):
    """Default ticket creation notifications are not sent for registered users."""
    emit_called = False
    send_called = False

    async def fake_emit_notification(**kwargs):
        nonlocal emit_called
        emit_called = True

    async def fake_send_email(**kwargs):
        nonlocal send_called
        send_called = True
        return True, None

    monkeypatch.setattr(notifications_service, "emit_notification", fake_emit_notification)
    monkeypatch.setattr(email_service, "send_email", fake_send_email)

    await tickets_service._send_ticket_creation_email(
        {
            "id": 10,
            "ticket_number": "100",
            "subject": "Printer offline",
            "requester_id": 5,
            "requester_email": "user@example.com",
        }
    )

    assert not emit_called
    assert not send_called


@pytest.mark.anyio
async def test_send_ticket_creation_email_is_noop_for_external_requester(monkeypatch):
    """Default ticket creation emails are not sent for external email-only requesters."""
    send_called = False

    async def fake_send_email(**kwargs):
        nonlocal send_called
        send_called = True
        return True, None

    monkeypatch.setattr(email_service, "send_email", fake_send_email)

    await tickets_service._send_ticket_creation_email(
        {
            "id": 20,
            "ticket_number": "200",
            "subject": "Network down",
            "requester_id": None,
            "requester_email": None,
        },
        requester_email_fallback="external@example.com",
    )

    assert not send_called


@pytest.mark.anyio
async def test_create_ticket_uses_ticket_automations_instead_of_default_notification(monkeypatch):
    """New-ticket notifications are delegated to the tickets.created automation event only."""
    from app.repositories import tickets as tickets_repo

    automation_events: list[str] = []
    default_notification_called = False

    async def fake_create_ticket_repo(**kwargs):
        ticket_id = kwargs.get("id") if kwargs.get("id") is not None else 55
        return {"id": ticket_id, **{k: v for k, v in kwargs.items() if k != "id"}}

    async def fake_create_reply(**kwargs):
        return {"id": 999, **kwargs}

    async def fake_enrich(ticket):
        return dict(ticket, ticket_number="T-55", requester_email="requester@example.com")

    async def fake_handle_event(event_name, context):
        automation_events.append(event_name)
        return []

    async def fake_default_notification(*args, **kwargs):
        nonlocal default_notification_called
        default_notification_called = True

    async def fake_broadcast(**kwargs):
        return None

    async def fake_resolve_status(status):
        return str(status)

    monkeypatch.setattr(tickets_service, "resolve_status_or_default", fake_resolve_status)
    monkeypatch.setattr(tickets_repo, "create_ticket", fake_create_ticket_repo)
    monkeypatch.setattr(tickets_repo, "create_reply", fake_create_reply)
    monkeypatch.setattr(tickets_service, "_enrich_ticket_context", fake_enrich)
    monkeypatch.setattr(automations_service, "handle_event", fake_handle_event)
    monkeypatch.setattr(tickets_service, "_send_ticket_creation_email", fake_default_notification)
    monkeypatch.setattr(tickets_service, "broadcast_ticket_event", fake_broadcast)

    ticket = await tickets_service.create_ticket(
        subject="Automation only",
        description="Notify through automations",
        requester_id=5,
        requester_staff_id=None,
        company_id=1,
        assigned_user_id=None,
        priority="Medium",
        status="open",
        category=None,
        module_slug=None,
        external_reference=None,
        requester_email="requester@example.com",
        send_creation_notification=True,
    )

    assert ticket["id"] == 55
    assert automation_events == ["tickets.created"]
    assert not default_notification_called
