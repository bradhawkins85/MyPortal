"""Test that variables and message templates work correctly in notifications and automations."""
import pytest
from unittest.mock import AsyncMock, MagicMock
from datetime import datetime, timezone


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_email_subject_renders_variables(monkeypatch):
    """Test that email subject renders variables from the message template."""
    from app.services import notifications as notifications_service
    from app.services import notification_event_settings
    from app.services import email as email_service
    from app.repositories import users as user_repo
    from app.repositories import notifications as notifications_repo
    from app.repositories import notification_preferences as preferences_repo
    
    # Mock the event setting to return a notification config with variables in message_template
    async def fake_get_event_setting(event_type: str):
        return {
            "event_type": event_type,
            "display_name": "Test Notification",
            "message_template": "Ticket {{ticket.number}} was updated: {{ticket.subject}}",
            "module_actions": [],
            "allow_channel_in_app": True,
            "allow_channel_email": True,
            "allow_channel_sms": False,
            "default_channel_in_app": True,
            "default_channel_email": True,
            "default_channel_sms": False,
        }
    
    # Mock user lookup
    async def fake_get_user_by_id(user_id: int):
        return {
            "id": user_id,
            "email": "test@example.com",
            "first_name": "Test",
            "last_name": "User",
        }
    
    # Track the email that was sent
    sent_emails = []
    
    async def fake_send_email(**kwargs):
        sent_emails.append(kwargs)
        return True, {"id": 1}
    
    # Mock notification repository methods
    async def fake_create_notification(**kwargs):
        return {"id": 1, **kwargs}
    
    async def fake_get_preference(user_id, event_type):
        return None  # Use defaults
    
    monkeypatch.setattr(notification_event_settings, "get_event_setting", fake_get_event_setting)
    monkeypatch.setattr(user_repo, "get_user_by_id", fake_get_user_by_id)
    monkeypatch.setattr(email_service, "send_email", fake_send_email)
    monkeypatch.setattr(notifications_repo, "create_notification", fake_create_notification)
    monkeypatch.setattr(preferences_repo, "get_preference", fake_get_preference)
    
    # Emit a notification with ticket context
    context = {
        "ticket": {
            "id": 123,
            "number": "TKT-456",
            "ticket_number": "TKT-456",
            "subject": "Printer broken",
        }
    }
    
    await notifications_service.emit_notification(
        event_type="tickets.updated",
        message="Test notification",
        user_id=1,
        metadata=context,
    )
    
    # Verify an email was sent
    assert len(sent_emails) == 1, "Expected exactly one email to be sent"
    sent_email = sent_emails[0]
    
    # The subject should now include the rendered message with variables replaced
    subject = sent_email["subject"]
    
    # After fix: subject should include ticket number from rendered message
    assert "TKT-456" in subject, f"Subject should include ticket number, got: {subject}"
    assert "Printer broken" in subject or "Ticket TKT-456" in subject, f"Subject should include rendered message content, got: {subject}"
    
    # Body should also have variables rendered
    text_body = sent_email.get("text_body", "")
    assert "TKT-456" in text_body, "Body should include ticket number"
    assert "Printer broken" in text_body, "Body should include ticket subject"


@pytest.mark.anyio
async def test_ntfy_notification_renders_variables(monkeypatch):
    """Test that ntfy notifications render variables correctly."""
    from app.services import modules as modules_service
    from app.repositories import integration_modules as module_repo
    from app.services import webhook_monitor
    import httpx
    
    # Mock module configuration
    async def fake_get_module(slug: str):
        if slug == "ntfy":
            return {
                "slug": "ntfy",
                "enabled": True,
                "settings": {
                    "base_url": "https://ntfy.sh",
                    "topic": "test-topic",
                    "auth_token": "",
                },
            }
        return None
    
    # Track HTTP requests made
    http_requests = []
    
    class FakeResponse:
        status_code = 200
        text = '{"id": "test123"}'
        
        def raise_for_status(self):
            pass
    
    class FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass
        
        async def __aenter__(self):
            return self
        
        async def __aexit__(self, *args):
            pass
        
        async def post(self, url, **kwargs):
            http_requests.append({
                "url": url,
                "data": kwargs.get("data"),
                "headers": kwargs.get("headers", {}),
            })
            return FakeResponse()
    
    # Mock webhook monitor
    async def fake_create_manual_event(**kwargs):
        return {"id": 1}
    
    async def fake_record_manual_success(**kwargs):
        return {"id": 1, "status": "succeeded"}
    
    monkeypatch.setattr(module_repo, "get_module", fake_get_module)
    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)
    monkeypatch.setattr(webhook_monitor, "create_manual_event", fake_create_manual_event)
    monkeypatch.setattr(webhook_monitor, "record_manual_success", fake_record_manual_success)
    
    # Trigger ntfy module with variable-containing payload
    payload = {
        "message": "Ticket {{ticket.number}} updated",
        "title": "Alert for {{ticket.subject}}",
        "ticket": {
            "number": "TKT-789",
            "subject": "Network issue",
        }
    }
    
    # Render variables before calling module (simulating automation behavior)
    from app.services import value_templates
    context = {"ticket": payload["ticket"]}
    rendered_payload = await value_templates.render_value_async(payload, context)
    
    result = await modules_service.trigger_module(
        "ntfy",
        rendered_payload,
        background=False,
    )
    
    # Verify the request was made with rendered variables
    assert len(http_requests) == 1
    request = http_requests[0]
    
    # The message should have variables rendered
    message_data = request["data"]
    assert b"TKT-789" in message_data
    
    # The title header should have variables rendered
    title_header = request["headers"].get("Title", "")
    assert "Network issue" in title_header


@pytest.mark.anyio
async def test_message_template_reference_in_automation(monkeypatch):
    """Test that message template references like {{template.my-template}} work."""
    from app.services import value_templates
    from app.services import message_templates
    
    # Create a mock template cache
    original_iter_templates = message_templates.iter_templates
    
    def fake_iter_templates():
        return [
            {
                "slug": "ticket-created",
                "name": "Ticket Created Template",
                "content": "New ticket {{ticket.number}}: {{ticket.subject}}",
                "content_type": "text/plain",
            }
        ]
    
    monkeypatch.setattr(message_templates, "iter_templates", fake_iter_templates)
    
    # Test rendering a string that references the template
    context = {
        "ticket": {
            "number": "TKT-999",
            "subject": "Server down",
        }
    }
    
    # Test template reference using {{template.slug}} syntax
    template_string = "Message: {{template.ticket-created}}"
    result = await value_templates.render_string_async(template_string, context)
    
    # The result should have the template content with variables rendered
    assert "TKT-999" in result
    assert "Server down" in result
    assert "New ticket" in result
