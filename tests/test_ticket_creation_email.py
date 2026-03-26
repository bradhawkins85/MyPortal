"""Tests for ticket creation email notifications."""
from __future__ import annotations

import pytest

from app.services import tickets as tickets_service
from app.services import notifications as notifications_service
from app.services import email as email_service
from app.services import modules as modules_service
from app.services import notification_event_settings


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _default_tickets_created_setting(event_type: str) -> dict:
    """Return a representative tickets.created event setting for tests."""
    return {
        "event_type": event_type,
        "display_name": "Ticket created",
        "message_template": "Your ticket #{{ ticket.ticket_number }} has been created: {{ ticket.subject }}",
        "module_actions": [],
        "allow_channel_in_app": True,
        "allow_channel_email": True,
        "allow_channel_sms": False,
        "default_channel_in_app": True,
        "default_channel_email": True,
        "default_channel_sms": False,
    }

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
    assert captured["metadata"]["ticket"] == {
        "id": 10,
        "ticket_number": "100",
        "subject": "Printer offline",
    }


@pytest.mark.anyio
async def test_send_creation_email_notification_metadata_excludes_sensitive_ticket_fields(monkeypatch):
    """Notification metadata includes only safe ticket summary fields."""
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
        "requester": {
            "id": 5,
            "email": "user@example.com",
            "mobile_phone": "+15551234",
            "is_super_admin": False,
        },
        "latest_reply": {
            "id": 40,
            "body_text": "Internal escalation details",
            "is_internal": True,
        },
    }

    await tickets_service._send_ticket_creation_email(enriched)

    assert captured["event_type"] == "tickets.created"
    assert captured["user_id"] == 5
    assert captured["metadata"]["ticket"] == {
        "id": 10,
        "ticket_number": "100",
        "subject": "Printer offline",
    }


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

    monkeypatch.setattr(
        notification_event_settings,
        "get_event_setting",
        lambda event_type: _default_tickets_created_setting(event_type),
    )

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

    monkeypatch.setattr(
        notification_event_settings,
        "get_event_setting",
        lambda event_type: _default_tickets_created_setting(event_type),
    )

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


# ---------------------------------------------------------------------------
# _send_ticket_creation_email – template rendering for external requester
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_send_creation_email_uses_event_template_for_external_requester(monkeypatch):
    """When the requester has no account, the email body uses the notification event template."""
    captured: dict[str, object] = {}

    async def fake_get_event_setting(event_type: str):
        return {
            "event_type": event_type,
            "display_name": "Ticket created",
            "message_template": "Custom template: ticket #{{ ticket.ticket_number }} - {{ ticket.subject }}",
            "module_actions": [],
            "allow_channel_in_app": True,
            "allow_channel_email": True,
            "allow_channel_sms": False,
            "default_channel_in_app": True,
            "default_channel_email": True,
            "default_channel_sms": False,
        }

    async def fake_send_email(*, subject, recipients, html_body, text_body=None, **kwargs):
        captured["subject"] = subject
        captured["recipients"] = recipients
        captured["html_body"] = html_body
        captured["text_body"] = text_body
        return True, None

    monkeypatch.setattr(notification_event_settings, "get_event_setting", fake_get_event_setting)
    monkeypatch.setattr(email_service, "send_email", fake_send_email)

    enriched = {
        "id": 40,
        "ticket_number": "400",
        "subject": "Custom template test",
        "requester_id": None,
        "requester_email": "external@example.com",
    }

    await tickets_service._send_ticket_creation_email(enriched)

    # The rendered template should appear in the email body and subject
    expected_message = "Custom template: ticket #400 - Custom template test"
    assert captured["text_body"] == expected_message
    assert f"<p>{expected_message}</p>" == captured["html_body"]
    assert "Custom template: ticket #400" in captured["subject"]


@pytest.mark.anyio
async def test_send_creation_email_uses_requester_fallback_in_template(monkeypatch):
    """Fallback email address is available to templates when requester is unknown."""
    captured: dict[str, object] = {}

    async def fake_get_event_setting(event_type: str):
        return {
            "event_type": event_type,
            "display_name": "Ticket created",
            "message_template": "{{ ticket.requester_email }}",
            "module_actions": [],
            "allow_channel_in_app": True,
            "allow_channel_email": True,
            "allow_channel_sms": False,
            "default_channel_in_app": True,
            "default_channel_email": True,
            "default_channel_sms": False,
        }

    async def fake_send_email(*, subject, recipients, html_body, text_body=None, **kwargs):
        captured["subject"] = subject
        captured["recipients"] = recipients
        captured["html_body"] = html_body
        captured["text_body"] = text_body
        return True, None

    monkeypatch.setattr(notification_event_settings, "get_event_setting", fake_get_event_setting)
    monkeypatch.setattr(email_service, "send_email", fake_send_email)

    enriched = {
        "id": 401,
        "ticket_number": "401",
        "subject": "Fallback template test",
        "requester_id": None,
        "requester_email": None,
    }

    await tickets_service._send_ticket_creation_email(
        enriched,
        requester_email_fallback="unknown@example.com",
    )

    assert captured["recipients"] == ["unknown@example.com"]
    assert captured["text_body"] == "unknown@example.com"
    assert captured["html_body"] == "<p>unknown@example.com</p>"
    assert "unknown@example.com" in captured["subject"]


@pytest.mark.anyio
async def test_send_creation_email_falls_back_when_template_unavailable(monkeypatch):
    """When event setting lookup fails, a sensible fallback message is used."""
    captured: dict[str, object] = {}

    async def failing_get_event_setting(event_type: str):
        raise RuntimeError("Database unavailable")

    async def fake_send_email(*, subject, recipients, html_body, text_body=None, **kwargs):
        captured["subject"] = subject
        captured["recipients"] = recipients
        captured["text_body"] = text_body
        return True, None

    monkeypatch.setattr(notification_event_settings, "get_event_setting", failing_get_event_setting)
    monkeypatch.setattr(email_service, "send_email", fake_send_email)

    enriched = {
        "id": 41,
        "ticket_number": "401",
        "subject": "Fallback test",
        "requester_id": None,
        "requester_email": "fallback@example.com",
    }

    await tickets_service._send_ticket_creation_email(enriched)

    assert captured["recipients"] == ["fallback@example.com"]
    assert "#401" in captured["subject"]
    assert "Fallback test" in captured["subject"]
    assert "#401" in captured["text_body"]
    assert "Fallback test" in captured["text_body"]


# ---------------------------------------------------------------------------
# _send_ticket_creation_email – module_actions for external requester
# ---------------------------------------------------------------------------


@pytest.mark.anyio
async def test_send_creation_email_skips_direct_send_when_email_module_action_succeeds(monkeypatch):
    """When an email-delivery module action succeeds, no fallback direct email is sent."""
    direct_send_called = False
    module_triggered: list[str] = []

    async def fake_get_event_setting(event_type: str):
        return {
            "event_type": event_type,
            "display_name": "Ticket created",
            "message_template": "Ticket #{{ ticket.ticket_number }} created",
            "module_actions": [{"module": "smtp2go", "payload": {"to": ["{{ ticket.requester_email }}"]}}],
            "allow_channel_in_app": True,
            "allow_channel_email": True,
            "allow_channel_sms": False,
            "default_channel_in_app": True,
            "default_channel_email": True,
            "default_channel_sms": False,
        }

    async def fake_trigger_module(slug, payload, *, background=True, **kwargs):
        module_triggered.append(slug)
        return {"status": "success"}

    async def fake_send_email(**kwargs):
        nonlocal direct_send_called
        direct_send_called = True
        return True, None

    monkeypatch.setattr(notification_event_settings, "get_event_setting", fake_get_event_setting)
    monkeypatch.setattr(modules_service, "trigger_module", fake_trigger_module)
    monkeypatch.setattr(email_service, "send_email", fake_send_email)

    enriched = {
        "id": 50,
        "ticket_number": "500",
        "subject": "Module action test",
        "requester_id": None,
        "requester_email": "ext@example.com",
    }

    await tickets_service._send_ticket_creation_email(enriched)

    # smtp2go module should have been triggered
    assert "smtp2go" in module_triggered
    # Direct fallback email should NOT have been sent
    assert not direct_send_called


@pytest.mark.anyio
async def test_send_creation_email_skips_when_requester_email_blank(monkeypatch):
    """No notification is attempted when requester email is blank/whitespace."""
    module_triggered: list[str] = []
    direct_send_called = False

    async def fake_get_event_setting(event_type: str):
        return {
            "event_type": event_type,
            "display_name": "Ticket created",
            "message_template": "Ticket created",
            "module_actions": [{"module": "smtp2go", "payload": {"to": ["{{ ticket.requester_email }}"]}}],
            "allow_channel_in_app": True,
            "allow_channel_email": True,
            "allow_channel_sms": False,
            "default_channel_in_app": True,
            "default_channel_email": True,
            "default_channel_sms": False,
        }

    async def fake_trigger_module(slug, payload, *, background=True, **kwargs):
        module_triggered.append(slug)
        return {"status": "success"}

    async def fake_send_email(**kwargs):
        nonlocal direct_send_called
        direct_send_called = True
        return True, None

    monkeypatch.setattr(notification_event_settings, "get_event_setting", fake_get_event_setting)
    monkeypatch.setattr(modules_service, "trigger_module", fake_trigger_module)
    monkeypatch.setattr(email_service, "send_email", fake_send_email)

    enriched = {
        "id": 51,
        "ticket_number": "501",
        "subject": "Blank requester test",
        "requester_id": None,
        "requester_email": "   ",
    }

    await tickets_service._send_ticket_creation_email(enriched)

    assert not module_triggered
    assert not direct_send_called


@pytest.mark.anyio
async def test_send_creation_email_uses_fallback_when_email_module_action_fails(monkeypatch):
    """When the email-delivery module action fails, the fallback direct email is sent."""
    captured: dict[str, object] = {}

    async def fake_get_event_setting(event_type: str):
        return {
            "event_type": event_type,
            "display_name": "Ticket created",
            "message_template": "Ticket #{{ ticket.ticket_number }} created: {{ ticket.subject }}",
            "module_actions": [{"module": "smtp2go", "payload": {}}],
            "allow_channel_in_app": True,
            "allow_channel_email": True,
            "allow_channel_sms": False,
            "default_channel_in_app": True,
            "default_channel_email": True,
            "default_channel_sms": False,
        }

    async def failing_trigger_module(slug, payload, *, background=True, **kwargs):
        raise RuntimeError("smtp2go API unreachable")

    async def fake_send_email(*, subject, recipients, html_body, text_body=None, **kwargs):
        captured["recipients"] = recipients
        captured["text_body"] = text_body
        return True, None

    monkeypatch.setattr(notification_event_settings, "get_event_setting", fake_get_event_setting)
    monkeypatch.setattr(modules_service, "trigger_module", failing_trigger_module)
    monkeypatch.setattr(email_service, "send_email", fake_send_email)

    enriched = {
        "id": 51,
        "ticket_number": "501",
        "subject": "Fallback on failure",
        "requester_id": None,
        "requester_email": "ext2@example.com",
    }

    await tickets_service._send_ticket_creation_email(enriched)

    # Fallback direct email should have been sent
    assert captured.get("recipients") == ["ext2@example.com"]
    assert "501" in str(captured.get("text_body"))
    assert "Fallback on failure" in str(captured.get("text_body"))


@pytest.mark.anyio
async def test_send_creation_email_fires_module_and_fallback_for_non_email_action(monkeypatch):
    """Non-email module actions fire AND the fallback direct email is also sent."""
    module_triggered: list[str] = []
    direct_send_called = False

    async def fake_get_event_setting(event_type: str):
        return {
            "event_type": event_type,
            "display_name": "Ticket created",
            "message_template": "Ticket #{{ ticket.ticket_number }} created",
            "module_actions": [{"module": "ntfy", "payload": {"topic": "tickets"}}],
            "allow_channel_in_app": True,
            "allow_channel_email": True,
            "allow_channel_sms": False,
            "default_channel_in_app": True,
            "default_channel_email": True,
            "default_channel_sms": False,
        }

    async def fake_trigger_module(slug, payload, *, background=True, **kwargs):
        module_triggered.append(slug)
        return {"status": "success"}

    async def fake_send_email(**kwargs):
        nonlocal direct_send_called
        direct_send_called = True
        return True, None

    monkeypatch.setattr(notification_event_settings, "get_event_setting", fake_get_event_setting)
    monkeypatch.setattr(modules_service, "trigger_module", fake_trigger_module)
    monkeypatch.setattr(email_service, "send_email", fake_send_email)

    enriched = {
        "id": 52,
        "ticket_number": "502",
        "subject": "Non-email module test",
        "requester_id": None,
        "requester_email": "ext3@example.com",
    }

    await tickets_service._send_ticket_creation_email(enriched)

    # ntfy module should have been triggered
    assert "ntfy" in module_triggered
    # Direct email should also be sent (ntfy is not an email-delivery module)
    assert direct_send_called
