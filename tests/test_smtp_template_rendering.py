"""Test that SMTP module properly renders template variables in automation payloads."""
import pytest
from unittest.mock import AsyncMock, patch

from app.services import modules as modules_service
from app.services import value_templates


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_smtp_module_renders_ticket_variables():
    """Test that ticket variables are properly rendered in SMTP module payloads."""
    # Create a ticket context similar to what automations pass
    ticket_context = {
        "ticket": {
            "id": 123,
            "number": "TKT-456",
            "ticket_number": "TKT-456",
            "subject": "Printer is broken",
            "description": "The printer in the office is not working",
            "priority": "high",
            "status": "open",
            "requester": {
                "email": "user@example.com",
                "display_name": "John Doe",
            },
            "company": {
                "name": "Acme Corp",
            },
        }
    }
    
    # Define an automation payload with template variables (as a user would)
    automation_payload = {
        "recipients": ["admin@example.com"],
        "subject": "Ticket {{ticket.number}}: {{ticket.subject}}",
        "html": "<p>New ticket from {{ticket.requester.display_name}} at {{ticket.company.name}}</p><p>{{ticket.description}}</p>",
        "text": "Ticket: {{ticket.subject}}"
    }
    
    # Render the payload (this is what happens in automations.py line 281)
    rendered_payload = await value_templates.render_value_async(automation_payload, ticket_context)
    
    # Verify rendering worked
    assert rendered_payload["subject"] == "Ticket TKT-456: Printer is broken"
    assert "John Doe" in rendered_payload["html"]
    assert "Acme Corp" in rendered_payload["html"]
    assert "not working" in rendered_payload["html"]
    assert rendered_payload["text"] == "Ticket: Printer is broken"


@pytest.mark.anyio
async def test_smtp_uses_rendered_values_not_defaults():
    """Test that SMTP module uses rendered template values, not defaults.
    
    This is the core bug fix - when template variables are rendered (even to
    empty strings), the SMTP module should use those values instead of falling
    back to defaults like "Automation notification".
    """
    # Mock the email service, webhook monitor, and webhook repo
    with patch("app.services.modules.email_service.send_email", new_callable=AsyncMock) as mock_send, \
         patch("app.services.modules.webhook_monitor.create_manual_event", new_callable=AsyncMock) as mock_event, \
         patch("app.services.modules.webhook_repo.record_attempt", new_callable=AsyncMock), \
         patch("app.services.modules.webhook_repo.mark_event_completed", new_callable=AsyncMock), \
         patch("app.services.modules.webhook_repo.get_event", new_callable=AsyncMock) as mock_get_event:
        
        mock_event.return_value = {"id": 1}
        mock_send.return_value = (True, {"id": 1})
        mock_get_event.return_value = {"id": 1, "status": "succeeded"}
        
        # Module settings (empty for this test)
        settings = {
            "from_address": "",
            "default_recipients": [],
            "subject_prefix": "",
        }
        
        # Payload with explicitly set values (already rendered by value_templates)
        payload = {
            "recipients": ["test@example.com"],
            "subject": "Ticket TKT-123: Server down",  # Rendered from template
            "html": "<p>Server is offline</p>",  # Rendered from template
            "text": "Server down urgently"  # Rendered from template
        }
        
        # Call the SMTP module directly
        result = await modules_service._invoke_smtp(settings, payload, event_future=None)
        
        # Verify the email service was called with the rendered values
        assert mock_send.called
        call_kwargs = mock_send.call_args.kwargs
        
        # The bug was that these would be replaced with defaults
        assert call_kwargs["subject"] == "Ticket TKT-123: Server down", \
            "Subject should use rendered value, not default"
        assert call_kwargs["html_body"] == "<p>Server is offline</p>", \
            "HTML body should use rendered value, not default"
        assert call_kwargs["text_body"] == "Server down urgently", \
            "Text body should use rendered value"


@pytest.mark.anyio
async def test_smtp_falls_back_to_defaults_when_keys_missing():
    """Test that SMTP module still uses defaults when keys are not provided."""
    with patch("app.services.modules.email_service.send_email", new_callable=AsyncMock) as mock_send, \
         patch("app.services.modules.webhook_monitor.create_manual_event", new_callable=AsyncMock) as mock_event, \
         patch("app.services.modules.webhook_repo.record_attempt", new_callable=AsyncMock), \
         patch("app.services.modules.webhook_repo.mark_event_completed", new_callable=AsyncMock), \
         patch("app.services.modules.webhook_repo.get_event", new_callable=AsyncMock) as mock_get_event:
        
        mock_event.return_value = {"id": 1}
        mock_send.return_value = (True, {"id": 1})
        mock_get_event.return_value = {"id": 1, "status": "succeeded"}
        
        settings = {
            "from_address": "",
            "default_recipients": ["default@example.com"],
            "subject_prefix": "",
        }
        
        # Payload with missing keys (not rendered at all)
        payload = {
            "recipients": [],  # Empty, should use default
            # subject not provided - should use default
            # html/body not provided - should use default
        }
        
        result = await modules_service._invoke_smtp(settings, payload, event_future=None)
        
        assert mock_send.called
        call_kwargs = mock_send.call_args.kwargs
        
        # Should use defaults when keys are missing
        assert call_kwargs["subject"] == "Automation notification"
        assert call_kwargs["html_body"] == "<p>Automation triggered.</p>"
        assert call_kwargs["recipients"] == ["default@example.com"]


@pytest.mark.anyio
async def test_smtp_handles_empty_string_from_template():
    """Test that SMTP module accepts empty strings from template rendering.
    
    When a template variable doesn't resolve (e.g., ticket.description is None),
    the template engine returns an empty string. The SMTP module should accept
    this as a valid value, not replace it with defaults.
    """
    with patch("app.services.modules.email_service.send_email", new_callable=AsyncMock) as mock_send, \
         patch("app.services.modules.webhook_monitor.create_manual_event", new_callable=AsyncMock) as mock_event, \
         patch("app.services.modules.webhook_repo.record_attempt", new_callable=AsyncMock), \
         patch("app.services.modules.webhook_repo.mark_event_completed", new_callable=AsyncMock), \
         patch("app.services.modules.webhook_repo.get_event", new_callable=AsyncMock) as mock_get_event:
        
        mock_event.return_value = {"id": 1}
        mock_send.return_value = (True, {"id": 1})
        mock_get_event.return_value = {"id": 1, "status": "succeeded"}
        
        settings = {
            "from_address": "",
            "default_recipients": [],
            "subject_prefix": "",
        }
        
        # Payload where templates rendered to empty strings
        payload = {
            "recipients": ["test@example.com"],
            "subject": "",  # Template rendered but value was empty
            "html": "",  # Template rendered but value was empty
        }
        
        result = await modules_service._invoke_smtp(settings, payload, event_future=None)
        
        assert mock_send.called
        call_kwargs = mock_send.call_args.kwargs
        
        # Should use the empty strings, not defaults
        assert call_kwargs["subject"] == "", \
            "Empty string from template should be preserved"
        assert call_kwargs["html_body"] == "", \
            "Empty string from template should be preserved"
