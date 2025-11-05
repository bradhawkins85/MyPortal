"""Test that ticket variables work in notification templates and module actions."""
import pytest

from app.services import notifications as notifications_service
from app.services import notification_event_settings


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_notification_exposes_ticket_from_metadata(monkeypatch):
    """Test that ticket data in metadata is exposed directly in notification context."""
    
    captured_contexts = []
    
    async def fake_get_event_setting(event_type):
        return {
            "event_type": event_type,
            "display_name": "Test Event",
            "message_template": "Ticket {{ticket.number}}: {{ticket.subject}}",
            "module_actions": [
                {
                    "module": "smtp",
                    "payload": {
                        "subject": "Alert for {{ticket.number}}",
                        "body": "Ticket {{ticket.number}} - {{ticket.subject}}",
                    }
                }
            ],
            "allow_channel_in_app": True,
            "allow_channel_email": False,
            "allow_channel_sms": False,
            "default_channel_in_app": True,
            "default_channel_email": False,
            "default_channel_sms": False,
        }
    
    async def fake_create_notification(**kwargs):
        # Store the message that was rendered
        captured_contexts.append({"message": kwargs.get("message")})
    
    async def fake_trigger_module(module_slug, payload, **kwargs):
        # Capture the rendered payload
        captured_contexts.append({"module": module_slug, "payload": payload})
        return {"status": "ok"}
    
    monkeypatch.setattr(
        notification_event_settings,
        "get_event_setting",
        fake_get_event_setting
    )
    monkeypatch.setattr(
        notifications_service.notifications_repo,
        "create_notification",
        fake_create_notification
    )
    monkeypatch.setattr(
        notifications_service.modules_service,
        "trigger_module",
        fake_trigger_module
    )
    
    # Call emit_notification with ticket data in metadata
    await notifications_service.emit_notification(
        event_type="test.ticket.event",
        message="A ticket event occurred",
        metadata={
            "ticket": {
                "id": 123,
                "number": "TKT-456",
                "ticket_number": "TKT-456",
                "subject": "Test ticket",
            }
        }
    )
    
    # Verify the message was rendered with ticket variables
    assert len(captured_contexts) == 2
    message_context = captured_contexts[0]
    assert message_context["message"] == "Ticket TKT-456: Test ticket"
    
    # Verify the module action payload was rendered with ticket variables
    module_context = captured_contexts[1]
    assert module_context["module"] == "smtp"
    assert module_context["payload"]["subject"] == "Alert for TKT-456"
    assert module_context["payload"]["body"] == "Ticket TKT-456 - Test ticket"


@pytest.mark.anyio
async def test_notification_works_without_ticket_in_metadata(monkeypatch):
    """Test that notifications still work when ticket is not in metadata."""
    
    captured_message = None
    
    async def fake_get_event_setting(event_type):
        return {
            "event_type": event_type,
            "display_name": "Test Event",
            "message_template": "{{ message }}",
            "module_actions": [],
            "allow_channel_in_app": True,
            "allow_channel_email": False,
            "allow_channel_sms": False,
            "default_channel_in_app": True,
            "default_channel_email": False,
            "default_channel_sms": False,
        }
    
    async def fake_create_notification(**kwargs):
        nonlocal captured_message
        captured_message = kwargs.get("message")
    
    monkeypatch.setattr(
        notification_event_settings,
        "get_event_setting",
        fake_get_event_setting
    )
    monkeypatch.setattr(
        notifications_service.notifications_repo,
        "create_notification",
        fake_create_notification
    )
    
    # Call emit_notification without ticket data
    await notifications_service.emit_notification(
        event_type="test.general.event",
        message="A general event occurred",
        metadata={"some_other_key": "value"}
    )
    
    # Verify the message was rendered correctly
    assert captured_message == "A general event occurred"
