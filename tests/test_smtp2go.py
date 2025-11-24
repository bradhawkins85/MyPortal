"""Tests for SMTP2Go integration."""

import asyncio
import pytest

from app.services import smtp2go


def test_generate_tracking_id():
    """Test that tracking ID generation produces unique values."""
    tracking_id_1 = smtp2go.generate_tracking_id()
    tracking_id_2 = smtp2go.generate_tracking_id()
    
    assert tracking_id_1 != tracking_id_2
    assert len(tracking_id_1) > 20  # Should be a reasonable length
    assert isinstance(tracking_id_1, str)


def test_list_email_templates():
    """Test that email templates can be listed."""
    templates = smtp2go.list_email_templates()
    
    assert isinstance(templates, list)
    assert len(templates) > 0
    
    # Check structure of first template
    first_template = templates[0]
    assert "type" in first_template
    assert "description" in first_template
    assert "subject_template" in first_template
    assert "recommended_fields" in first_template


def test_get_email_template():
    """Test getting a specific email template."""
    template = smtp2go.get_email_template("password_reset")
    
    assert "subject_template" in template
    assert "recommended_fields" in template
    assert "example_payload" in template
    assert "description" in template


def test_get_email_template_invalid():
    """Test that getting an invalid template raises ValueError."""
    with pytest.raises(ValueError) as exc_info:
        smtp2go.get_email_template("invalid_template")
    
    assert "Unknown template type" in str(exc_info.value)


def test_format_template_payload():
    """Test formatting a template with variables."""
    payload = smtp2go.format_template_payload(
        "password_reset",
        {
            "recipient_name": "John Doe",
            "reset_link": "https://example.com/reset/abc123",
            "expiry_time": "1 hour",
        },
        ["user@example.com"],
        "noreply@example.com",
    )
    
    assert payload["to"] == ["user@example.com"]
    assert payload["sender"] == "noreply@example.com"
    assert "John Doe" in payload["subject"] or "John Doe" in payload["html_body"]
    assert "https://example.com/reset/abc123" in payload["html_body"]
    assert "1 hour" in payload["html_body"]
    assert "text_body" in payload


def test_format_template_payload_escapes_html():
    """Test that template variable substitution escapes HTML to prevent XSS."""
    payload = smtp2go.format_template_payload(
        "notification",
        {
            "title": "Test <script>alert('xss')</script>",
            "message": "Message with <b>HTML</b>",
            "action_text": "Click here",
            "action_link": "https://example.com",
        },
        ["user@example.com"],
    )
    
    # HTML should be escaped in the output
    assert "<script>" not in payload["html_body"]
    assert "&lt;script&gt;" in payload["html_body"]
    assert "<b>" not in payload["subject"]  # If title appears in subject
    # Note: The template's own HTML tags should remain, only variable content is escaped
    

@pytest.mark.asyncio
async def test_send_email_via_api_success(monkeypatch):
    """Test successful email sending via SMTP2Go API."""
    
    # Mock httpx AsyncClient
    class MockResponse:
        def __init__(self):
            self.status_code = 200
        
        def raise_for_status(self):
            pass
        
        def json(self):
            return {
                "data": {
                    "error_code": "SUCCESS",
                    "email_id": "test-message-id-123",
                }
            }
    
    class MockAsyncClient:
        def __init__(self, *args, **kwargs):
            pass
        
        async def __aenter__(self):
            return self
        
        async def __aexit__(self, *args):
            pass
        
        async def post(self, url, json=None):
            assert url == "https://api.smtp2go.com/v3/email/send"
            assert "api_key" in json
            assert "to" in json
            assert "subject" in json
            assert "sender" in json  # Verify sender is included
            return MockResponse()
    
    # Mock modules service
    async def mock_get_module_settings(slug):
        assert slug == "smtp2go"
        return {
            "api_key": "test-api-key-123",
        }
    
    from app.services import modules as modules_service
    monkeypatch.setattr(modules_service, "get_module_settings", mock_get_module_settings)
    
    # Mock httpx
    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", MockAsyncClient)
    
    # Test sending email with explicit sender
    result = await smtp2go.send_email_via_api(
        to=["test@example.com"],
        subject="Test Subject",
        html_body="<p>Test body</p>",
        text_body="Test body",
        sender="sender@example.com",  # Explicitly provide sender
    )
    
    assert result["email_id"] == "test-message-id-123"
    assert result["error_code"] == "SUCCESS"


@pytest.mark.asyncio
async def test_send_email_via_api_normalizes_message_id(monkeypatch):
    """Ensure message_id responses are normalized to email_id for tracking."""

    class MockResponse:
        def __init__(self):
            self.status_code = 200

        def raise_for_status(self):
            pass

        def json(self):
            return {
                "data": {
                    "error_code": "SUCCESS",
                    "message_id": "legacy-message-id-456",
                }
            }

    class MockAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def post(self, url, json=None):
            return MockResponse()

    async def mock_get_module_settings(slug):
        return {"api_key": "test-api-key-123"}

    from app.services import modules as modules_service
    monkeypatch.setattr(modules_service, "get_module_settings", mock_get_module_settings)

    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", MockAsyncClient)

    result = await smtp2go.send_email_via_api(
        to=["user@example.com"],
        subject="Subject",
        html_body="<p>Body</p>",
    )

    assert result["email_id"] == "legacy-message-id-456"
    assert result.get("message_id") == "legacy-message-id-456"


@pytest.mark.asyncio
async def test_send_email_via_api_failure(monkeypatch):
    """Test handling of SMTP2Go API errors."""
    
    # Mock httpx AsyncClient with error response
    class MockResponse:
        def __init__(self):
            self.status_code = 200
        
        def raise_for_status(self):
            pass
        
        def json(self):
            return {
                "data": {
                    "error_code": "AUTH_FAILED",
                    "error": "Invalid API key",
                }
            }
    
    class MockAsyncClient:
        def __init__(self, *args, **kwargs):
            pass
        
        async def __aenter__(self):
            return self
        
        async def __aexit__(self, *args):
            pass
        
        async def post(self, url, json=None):
            return MockResponse()
    
    # Mock modules service
    async def mock_get_module_settings(slug):
        return {"api_key": "invalid-key"}
    
    from app.services import modules as modules_service
    monkeypatch.setattr(modules_service, "get_module_settings", mock_get_module_settings)
    
    # Mock httpx
    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", MockAsyncClient)
    
    # Test that error is raised
    with pytest.raises(smtp2go.SMTP2GoError) as exc_info:
        await smtp2go.send_email_via_api(
            to=["test@example.com"],
            subject="Test Subject",
            html_body="<p>Test body</p>",
            sender="sender@example.com",  # Explicitly provide sender
        )
    
    assert "Invalid API key" in str(exc_info.value)
    # Verify the error is not double-wrapped
    assert "Send failed:" not in str(exc_info.value)


@pytest.mark.asyncio
async def test_send_email_via_api_not_configured(monkeypatch):
    """Test handling when SMTP2Go is not configured."""
    
    # Mock modules service returning None
    async def mock_get_module_settings(slug):
        return None
    
    from app.services import modules as modules_service
    monkeypatch.setattr(modules_service, "get_module_settings", mock_get_module_settings)
    
    # Test that error is raised
    with pytest.raises(smtp2go.SMTP2GoError) as exc_info:
        await smtp2go.send_email_via_api(
            to=["test@example.com"],
            subject="Test Subject",
            html_body="<p>Test body</p>",
        )
    
    assert "not configured" in str(exc_info.value)


@pytest.mark.asyncio
async def test_send_email_via_api_unknown_error(monkeypatch):
    """Test handling of SMTP2Go API unknown errors without double-wrapping."""
    
    # Mock httpx AsyncClient with unknown error response
    class MockResponse:
        def __init__(self):
            self.status_code = 200
        
        def raise_for_status(self):
            pass
        
        def json(self):
            return {
                "data": {
                    "error_code": "UNKNOWN",
                    "error": "Unknown error",
                }
            }
    
    class MockAsyncClient:
        def __init__(self, *args, **kwargs):
            pass
        
        async def __aenter__(self):
            return self
        
        async def __aexit__(self, *args):
            pass
        
        async def post(self, url, json=None):
            return MockResponse()
    
    # Mock modules service
    async def mock_get_module_settings(slug):
        return {"api_key": "test-api-key"}
    
    from app.services import modules as modules_service
    monkeypatch.setattr(modules_service, "get_module_settings", mock_get_module_settings)
    
    # Mock httpx
    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", MockAsyncClient)
    
    # Test that error is raised with correct message
    with pytest.raises(smtp2go.SMTP2GoError) as exc_info:
        await smtp2go.send_email_via_api(
            to=["test@example.com"],
            subject="Test Subject",
            html_body="<p>Test body</p>",
            sender="sender@example.com",
        )
    
    error_message = str(exc_info.value)
    # Verify the exact error format (not double-wrapped with "Send failed:")
    assert error_message == "SMTP2Go API error [UNKNOWN]: Unknown error"


@pytest.mark.asyncio
async def test_send_email_via_api_missing_sender(monkeypatch):
    """Test that missing sender raises appropriate error."""
    
    # Mock modules service
    async def mock_get_module_settings(slug):
        return {"api_key": "test-api-key-123"}
    
    from app.services import modules as modules_service
    monkeypatch.setattr(modules_service, "get_module_settings", mock_get_module_settings)
    
    # Mock settings with no smtp_user
    from app.core.config import get_settings
    settings = get_settings()
    original_smtp_user = settings.smtp_user
    settings.smtp_user = None
    
    try:
        # Test that error is raised when sender is missing
        with pytest.raises(smtp2go.SMTP2GoError) as exc_info:
            await smtp2go.send_email_via_api(
                to=["test@example.com"],
                subject="Test Subject",
                html_body="<p>Test body</p>",
            )
        
        assert "Sender email address is required" in str(exc_info.value)
    finally:
        settings.smtp_user = original_smtp_user


@pytest.mark.asyncio
async def test_send_email_via_api_400_error(monkeypatch):
    """Test detailed logging for 400 Bad Request errors."""
    
    # Mock httpx AsyncClient with 400 response
    class MockResponse:
        def __init__(self):
            self.status_code = 400
            self.text = '{"error": "Invalid sender"}'
        
        def raise_for_status(self):
            pass
        
        def json(self):
            return {"error": "Invalid sender"}
    
    class MockAsyncClient:
        def __init__(self, *args, **kwargs):
            pass
        
        async def __aenter__(self):
            return self
        
        async def __aexit__(self, *args):
            pass
        
        async def post(self, url, json=None):
            return MockResponse()
    
    # Mock modules service
    async def mock_get_module_settings(slug):
        return {"api_key": "test-api-key"}
    
    from app.services import modules as modules_service
    monkeypatch.setattr(modules_service, "get_module_settings", mock_get_module_settings)
    
    # Mock httpx
    import httpx
    monkeypatch.setattr(httpx, "AsyncClient", MockAsyncClient)
    
    # Test that 400 error is properly handled
    with pytest.raises(smtp2go.SMTP2GoError) as exc_info:
        await smtp2go.send_email_via_api(
            to=["test@example.com"],
            subject="Test Subject",
            html_body="<p>Test body</p>",
            sender="sender@example.com",
        )
    
    assert "400 Bad Request" in str(exc_info.value)


@pytest.mark.asyncio
async def test_process_webhook_event_delivered(monkeypatch):
    """Test processing of delivery webhook event."""
    
    # Mock database queries
    fetch_one_result = {
        'id': 123,
        'email_tracking_id': 'test-tracking-id',
    }
    
    execute_results = []
    
    async def mock_fetch_one(query, params):
        return fetch_one_result
    
    async def mock_execute(query, params):
        execute_results.append({'query': query, 'params': params})
        return 456  # event_id
    
    from app.core import database
    monkeypatch.setattr(database.db, "fetch_one", mock_fetch_one)
    monkeypatch.setattr(database.db, "execute", mock_execute)
    
    # Test processing delivery event
    event_data = {
        "email_id": "smtp2go-msg-123",
        "recipient": "test@example.com",
        "timestamp": "2025-01-01T12:00:00Z",
        "event": "delivered",
    }
    
    result = await smtp2go.process_webhook_event("delivered", event_data)
    
    assert result is not None
    assert result['tracking_id'] == 'test-tracking-id'
    assert result['event_type'] == 'delivered'
    assert len(execute_results) == 2  # Insert event + update reply


@pytest.mark.asyncio
async def test_process_webhook_event_opened(monkeypatch):
    """Test processing of open webhook event."""
    
    # Mock database queries
    fetch_one_result = {
        'id': 123,
        'email_tracking_id': 'test-tracking-id',
    }
    
    execute_results = []
    
    async def mock_fetch_one(query, params):
        return fetch_one_result
    
    async def mock_execute(query, params):
        execute_results.append({'query': query, 'params': params})
        return 456  # event_id
    
    from app.core import database
    monkeypatch.setattr(database.db, "fetch_one", mock_fetch_one)
    monkeypatch.setattr(database.db, "execute", mock_execute)
    
    # Test processing open event
    event_data = {
        "email_id": "smtp2go-msg-123",
        "recipient": "test@example.com",
        "timestamp": "2025-01-01T12:05:00Z",
        "event": "opened",
        "user_agent": "Mozilla/5.0",
        "ip": "203.0.113.1",
    }
    
    result = await smtp2go.process_webhook_event("opened", event_data)
    
    assert result is not None
    assert result['tracking_id'] == 'test-tracking-id'
    assert result['event_type'] == 'open'
    assert len(execute_results) == 2  # Insert event + update reply with open count


@pytest.mark.asyncio
async def test_process_webhook_event_clicked(monkeypatch):
    """Test processing of click webhook event."""
    
    # Mock database queries
    fetch_one_result = {
        'id': 123,
        'email_tracking_id': 'test-tracking-id',
    }
    
    execute_results = []
    
    async def mock_fetch_one(query, params):
        return fetch_one_result
    
    async def mock_execute(query, params):
        execute_results.append({'query': query, 'params': params})
        return 456  # event_id
    
    from app.core import database
    monkeypatch.setattr(database.db, "fetch_one", mock_fetch_one)
    monkeypatch.setattr(database.db, "execute", mock_execute)
    
    # Test processing click event
    event_data = {
        "email_id": "smtp2go-msg-123",
        "recipient": "test@example.com",
        "timestamp": "2025-01-01T12:10:00Z",
        "event": "clicked",
        "url": "https://example.com/page",
        "user_agent": "Mozilla/5.0",
        "ip": "203.0.113.1",
    }
    
    result = await smtp2go.process_webhook_event("clicked", event_data)
    
    assert result is not None
    assert result['tracking_id'] == 'test-tracking-id'
    assert result['event_type'] == 'click'
    assert len(execute_results) == 1  # Only insert event (no reply update for clicks)


@pytest.mark.asyncio
async def test_process_webhook_event_unknown_message(monkeypatch):
    """Test handling of webhook for unknown message."""
    
    # Mock database returning None
    async def mock_fetch_one(query, params):
        return None
    
    from app.core import database
    monkeypatch.setattr(database.db, "fetch_one", mock_fetch_one)
    
    # Test processing event for unknown message
    event_data = {
        "email_id": "unknown-msg-id",
        "recipient": "test@example.com",
        "timestamp": "2025-01-01T12:00:00Z",
    }
    
    result = await smtp2go.process_webhook_event("delivered", event_data)
    
    assert result is None


def test_send_email_uses_smtp2go_when_enabled(monkeypatch):
    """Test that send_email uses SMTP2Go when module is enabled."""
    from app.services import email as email_service
    from app.services import modules as modules_service
    from app.core.config import get_settings
    
    settings = get_settings()
    monkeypatch.setattr(settings, "smtp_host", "smtp.example.com")
    
    captured_smtp2go_call = {}
    captured_smtp_call = {}
    
    # Mock SMTP2Go module as enabled
    async def mock_get_module(slug, *, redact=True):
        if slug == "smtp2go":
            return {"slug": slug, "enabled": True}
        return None
    
    monkeypatch.setattr(modules_service, "get_module", mock_get_module)
    
    # Mock SMTP2Go send_email_via_api
    async def mock_smtp2go_send(**kwargs):
        captured_smtp2go_call.update(kwargs)
        return {"email_id": "test-msg-id", "error_code": "SUCCESS"}
    
    monkeypatch.setattr(smtp2go, "send_email_via_api", mock_smtp2go_send)
    
    # Mock SMTP2Go record_email_sent
    async def mock_record_sent(**kwargs):
        pass
    
    monkeypatch.setattr(smtp2go, "record_email_sent", mock_record_sent)
    
    # Mock SMTP (shouldn't be called)
    class DummySMTP:
        def __init__(self, *args, **kwargs):
            captured_smtp_call['called'] = True
            raise AssertionError("SMTP relay should not be called when SMTP2Go is enabled")
    
    monkeypatch.setattr(email_service.smtplib, "SMTP", DummySMTP)
    
    # Test sending email
    result = asyncio.run(email_service.send_email(
        subject="Test Subject",
        recipients=["test@example.com"],
        html_body="<p>Test body</p>",
        text_body="Test body",
    ))
    
    sent, metadata = result
    assert sent is True
    assert metadata["provider"] == "smtp2go"
    assert captured_smtp2go_call["subject"] == "Test Subject"
    assert "called" not in captured_smtp_call  # SMTP relay not called
