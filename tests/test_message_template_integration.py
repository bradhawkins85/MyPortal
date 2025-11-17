"""Integration test for message template rendering in automation scenarios."""
import pytest
from unittest.mock import patch, AsyncMock


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_ntfy_notification_with_message_template():
    """Test that ntfy notifications can use message templates with the {{slug}} syntax."""
    from app.services import modules as modules_service
    from app.repositories import integration_modules as module_repo
    from app.services import webhook_monitor
    from app.services import message_templates
    import httpx
    
    # Mock ntfy module configuration
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
    
    # Mock message templates
    mock_templates = [
        {
            "slug": "email.new.ticket.subject",
            "name": "Email New Ticket Subject",
            "content": "New Ticket #{{ticket.number}} - {{ticket.subject}}",
            "content_type": "text/plain",
        },
    ]
    
    # Track HTTP requests
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
    
    # Mock for recording success/failure
    webhook_repo_patches = []
    
    async def fake_record_attempt(**kwargs):
        return {"id": 1, "status": "succeeded"}
    
    async def fake_mark_completed(**kwargs):
        return {"id": 1, "status": "succeeded"}
    
    async def fake_get_event(event_id):
        return {"id": event_id, "status": "succeeded"}
    
    with patch.object(module_repo, 'get_module', fake_get_module), \
         patch.object(httpx, 'AsyncClient', FakeAsyncClient), \
         patch.object(webhook_monitor, 'create_manual_event', fake_create_manual_event), \
         patch.object(message_templates, 'iter_templates', return_value=mock_templates), \
         patch('app.repositories.webhook_events.record_attempt', fake_record_attempt), \
         patch('app.repositories.webhook_events.mark_event_completed', fake_mark_completed), \
         patch('app.repositories.webhook_events.get_event', fake_get_event):
        
        # Render the payload with templates
        from app.services import value_templates
        
        # Simulate automation payload with a message template reference
        payload = {
            "title": "{{email.new.ticket.subject}}",  # Using direct slug reference
            "message": "A new ticket has been created",
            "ticket": {
                "number": "TKT-999",
                "subject": "Server is down",
            }
        }
        
        # Build context for rendering
        context = {"ticket": payload["ticket"]}
        
        # Render the payload (as automations would do)
        rendered_payload = await value_templates.render_value_async(payload, context)
        
        # Trigger ntfy module
        result = await modules_service.trigger_module(
            "ntfy",
            rendered_payload,
            background=False,
        )
        
        # Verify the request was made
        assert len(http_requests) == 1, "Should have made exactly one HTTP request"
        request = http_requests[0]
        
        # Verify the title header contains the rendered template
        title_header = request["headers"].get("Title", "")
        assert "TKT-999" in title_header, f"Title should contain ticket number, got: {title_header}"
        assert "Server is down" in title_header, f"Title should contain ticket subject, got: {title_header}"
        assert "New Ticket #TKT-999" in title_header, f"Title should contain rendered template, got: {title_header}"


@pytest.mark.anyio
async def test_smtp_notification_with_message_template():
    """Test that email notifications can use message templates with the {{slug}} syntax."""
    from app.services import modules as modules_service
    from app.repositories import integration_modules as module_repo
    from app.services import webhook_monitor
    from app.services import email as email_service
    from app.services import message_templates
    
    # Mock smtp module configuration
    async def fake_get_module(slug: str):
        if slug == "smtp":
            return {
                "slug": "smtp",
                "enabled": True,
                "settings": {
                    "from_address": "noreply@example.com",
                    "default_recipients": [],
                    "subject_prefix": "[Portal]",
                },
            }
        return None
    
    # Mock message templates
    mock_templates = [
        {
            "slug": "email.new.ticket.subject",
            "name": "Email New Ticket Subject",
            "content": "New Ticket #{{ticket.number}} - {{ticket.subject}}",
            "content_type": "text/plain",
        },
        {
            "slug": "email.new.ticket.body",
            "name": "Email New Ticket Body",
            "content": "<p>Ticket {{ticket.number}} has been created: {{ticket.subject}}</p>",
            "content_type": "text/html",
        },
    ]
    
    # Track emails sent
    sent_emails = []
    
    async def fake_send_email(**kwargs):
        sent_emails.append(kwargs)
        return True, {"id": 1}
    
    # Mock webhook monitor
    async def fake_create_manual_event(**kwargs):
        return {"id": 1}
    
    # Mock for recording success
    async def fake_record_attempt(**kwargs):
        return {"id": 1, "status": "succeeded"}
    
    async def fake_mark_completed(**kwargs):
        return {"id": 1, "status": "succeeded"}
    
    async def fake_get_event(event_id):
        return {"id": event_id, "status": "succeeded"}
    
    with patch.object(module_repo, 'get_module', fake_get_module), \
         patch.object(email_service, 'send_email', fake_send_email), \
         patch.object(webhook_monitor, 'create_manual_event', fake_create_manual_event), \
         patch.object(message_templates, 'iter_templates', return_value=mock_templates), \
         patch('app.repositories.webhook_events.record_attempt', fake_record_attempt), \
         patch('app.repositories.webhook_events.mark_event_completed', fake_mark_completed), \
         patch('app.repositories.webhook_events.get_event', fake_get_event):
        
        # Render the payload with templates
        from app.services import value_templates
        
        # Simulate automation payload with message template references
        payload = {
            "subject": "{{email.new.ticket.subject}}",  # Using direct slug reference
            "html": "{{email.new.ticket.body}}",  # Using direct slug reference
            "recipients": ["admin@example.com"],
            "ticket": {
                "number": "TKT-888",
                "subject": "Network connectivity issue",
            }
        }
        
        # Build context for rendering
        context = {"ticket": payload["ticket"]}
        
        # Render the payload (as automations would do)
        rendered_payload = await value_templates.render_value_async(payload, context)
        
        # Trigger smtp module
        result = await modules_service.trigger_module(
            "smtp",
            rendered_payload,
            background=False,
        )
        
        # Verify an email was sent
        assert len(sent_emails) == 1, "Should have sent exactly one email"
        email = sent_emails[0]
        
        # Verify the subject contains the rendered template
        subject = email["subject"]
        assert "TKT-888" in subject, f"Subject should contain ticket number, got: {subject}"
        assert "Network connectivity issue" in subject, f"Subject should contain ticket subject, got: {subject}"
        
        # Verify the HTML body contains the rendered template
        html_body = email["html_body"]
        assert "TKT-888" in html_body, f"Body should contain ticket number, got: {html_body}"
        assert "Network connectivity issue" in html_body, f"Body should contain ticket subject, got: {html_body}"
        assert "<p>" in html_body, f"Body should contain HTML from template, got: {html_body}"
