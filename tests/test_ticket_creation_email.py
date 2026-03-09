"""Tests for ticket creation email notifications."""
from __future__ import annotations

import pytest

from app.services import tickets as tickets_service
from app.services import notifications as notifications_service
from app.services import email as email_service


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


# ---------------------------------------------------------------------------
# _send_ticket_creation_email – requester with user account
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_send_creation_email_calls_notify_for_user_requester(monkeypatch):
    """When the requester has a user account, emit_notification is used."""
    captured: dict[str, object] = {}

    async def fake_emit_notification(*, event_type, user_id, metadata):
        captured["event_type"] = event_type
        captured["user_id"] = user_id
        captured["metadata"] = metadata

    monkeypatch.setattr(notifications_service, "emit_notification", fake_emit_notification)

    enriched = {
        "id": 10,
        "ticket_number": "100",
        "subject": "Printer offline",
        "requester_id": 5,
        "requester_email": "user@example.com",
    }

    await tickets_service._send_ticket_creation_email(enriched)

    assert captured["event_type"] == "tickets.created"
    assert captured["user_id"] == 5
    assert captured["metadata"]["ticket"] == enriched


@pytest.mark.anyio
async def test_send_creation_email_skips_direct_send_when_user_requester(monkeypatch):
    """Direct email sending is skipped when the requester has a user account."""
    direct_send_called = False

    async def fake_emit_notification(**kwargs):
        pass

    async def fake_send_email(**kwargs):
        nonlocal direct_send_called
        direct_send_called = True
        return True, None

    monkeypatch.setattr(notifications_service, "emit_notification", fake_emit_notification)
    monkeypatch.setattr(email_service, "send_email", fake_send_email)

    enriched = {
        "id": 11,
        "ticket_number": "101",
        "subject": "VPN issue",
        "requester_id": 7,
        "requester_email": "vpn@example.com",
    }

    await tickets_service._send_ticket_creation_email(enriched)

    assert not direct_send_called


# ---------------------------------------------------------------------------
# _send_ticket_creation_email – requester without user account (email only)
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_send_creation_email_sends_direct_when_no_requester_id(monkeypatch):
    """A direct email is sent when only an email address is available."""
    captured: dict[str, object] = {}

    async def fake_send_email(*, subject, recipients, html_body, text_body=None, **kwargs):
        captured["subject"] = subject
        captured["recipients"] = recipients
        captured["html_body"] = html_body
        captured["text_body"] = text_body
        return True, None

    monkeypatch.setattr(email_service, "send_email", fake_send_email)

    enriched = {
        "id": 20,
        "ticket_number": "200",
        "subject": "Network down",
        "requester_id": None,
        "requester_email": None,
    }

    await tickets_service._send_ticket_creation_email(
        enriched,
        requester_email_fallback="external@example.com",
    )

    assert captured["recipients"] == ["external@example.com"]
    assert "#200" in captured["subject"]
    assert "Network down" in captured["subject"]


@pytest.mark.anyio
async def test_send_creation_email_uses_enriched_email_when_no_user_id(monkeypatch):
    """Uses requester_email from enriched ticket when there is no requester_id."""
    captured_recipients: list[str] = []

    async def fake_send_email(*, recipients, **kwargs):
        captured_recipients.extend(recipients)
        return True, None

    monkeypatch.setattr(email_service, "send_email", fake_send_email)

    enriched = {
        "id": 21,
        "ticket_number": "201",
        "subject": "Hardware fault",
        "requester_id": None,
        "requester_email": "hw@example.com",
    }

    await tickets_service._send_ticket_creation_email(enriched)

    assert "hw@example.com" in captured_recipients


@pytest.mark.anyio
async def test_send_creation_email_noop_when_no_email_or_id(monkeypatch):
    """No email or notification is sent when neither id nor email is available."""
    emit_called = False
    send_called = False

    async def fake_emit(**kwargs):
        nonlocal emit_called
        emit_called = True

    async def fake_send(**kwargs):
        nonlocal send_called
        send_called = True
        return True, None

    monkeypatch.setattr(notifications_service, "emit_notification", fake_emit)
    monkeypatch.setattr(email_service, "send_email", fake_send)

    enriched = {
        "id": 30,
        "ticket_number": "300",
        "subject": "No requester",
        "requester_id": None,
        "requester_email": None,
    }

    await tickets_service._send_ticket_creation_email(enriched)

    assert not emit_called
    assert not send_called


# ---------------------------------------------------------------------------
# create_ticket integration – email is triggered
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_create_ticket_triggers_creation_email(monkeypatch):
    """create_ticket calls _send_ticket_creation_email after enriching the ticket."""
    from app.repositories import tickets as tickets_repo
    from app.services import automations as automations_service

    async def fake_create_ticket_repo(**kwargs):
        ticket_id = kwargs.get("id") if kwargs.get("id") is not None else 55
        return {"id": ticket_id, **{k: v for k, v in kwargs.items() if k != "id"}}

    async def fake_handle_event(event_name, context):
        return []

    async def fake_get_company(company_id):
        return None

    async def fake_get_user(user_id):
        return {"id": user_id, "email": "requester@example.com", "first_name": "Alice", "last_name": "Smith"}

    async def fake_resolve_status(value):
        return value or "open"

    captured_email_call: dict[str, object] = {}

    async def fake_send_creation_email(enriched_ticket, *, requester_email_fallback=None):
        captured_email_call["ticket_id"] = enriched_ticket.get("id")
        captured_email_call["requester_email_fallback"] = requester_email_fallback

    monkeypatch.setattr(tickets_repo, "create_ticket", fake_create_ticket_repo)
    monkeypatch.setattr(automations_service, "handle_event", fake_handle_event)
    monkeypatch.setattr(tickets_service.company_repo, "get_company_by_id", fake_get_company)
    monkeypatch.setattr(tickets_service.user_repo, "get_user_by_id", fake_get_user)
    monkeypatch.setattr(tickets_service, "resolve_status_or_default", fake_resolve_status)
    monkeypatch.setattr(tickets_service, "_send_ticket_creation_email", fake_send_creation_email)

    ticket = await tickets_service.create_ticket(
        subject="Server unreachable",
        description="Can't ping the server",
        requester_id=9,
        company_id=None,
        assigned_user_id=None,
        priority="high",
        status="open",
        category=None,
        module_slug=None,
        external_reference=None,
    )

    assert ticket["id"] == 55
    assert captured_email_call["ticket_id"] == 55
    assert captured_email_call["requester_email_fallback"] is None


@pytest.mark.anyio
async def test_create_ticket_passes_requester_email_fallback(monkeypatch):
    """create_ticket forwards requester_email to _send_ticket_creation_email."""
    from app.repositories import tickets as tickets_repo
    from app.services import automations as automations_service

    async def fake_create_ticket_repo(**kwargs):
        return {"id": 60, **{k: v for k, v in kwargs.items() if k != "id"}}

    async def fake_handle_event(event_name, context):
        return []

    async def fake_get_company(company_id):
        return None

    async def fake_get_user(user_id):
        return None

    async def fake_resolve_status(value):
        return value or "open"

    captured: dict[str, object] = {}

    async def fake_send_creation_email(enriched_ticket, *, requester_email_fallback=None):
        captured["requester_email_fallback"] = requester_email_fallback

    monkeypatch.setattr(tickets_repo, "create_ticket", fake_create_ticket_repo)
    monkeypatch.setattr(automations_service, "handle_event", fake_handle_event)
    monkeypatch.setattr(tickets_service.company_repo, "get_company_by_id", fake_get_company)
    monkeypatch.setattr(tickets_service.user_repo, "get_user_by_id", fake_get_user)
    monkeypatch.setattr(tickets_service, "resolve_status_or_default", fake_resolve_status)
    monkeypatch.setattr(tickets_service, "_send_ticket_creation_email", fake_send_creation_email)

    await tickets_service.create_ticket(
        subject="IMAP ticket",
        description="Sent via email",
        requester_id=None,
        company_id=None,
        assigned_user_id=None,
        priority="normal",
        status="open",
        category="email",
        module_slug="imap",
        external_reference=None,
        requester_email="sender@example.com",
    )

    assert captured["requester_email_fallback"] == "sender@example.com"
