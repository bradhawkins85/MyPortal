"""Test for SMTP2Go module accepting both html and html_body keys."""

import asyncio
import json
import pytest

from app.services import modules


@pytest.fixture
def mock_smtp2go_dependencies(monkeypatch):
    """Mock the SMTP2Go module dependencies."""
    captured_call = {}
    
    async def mock_send_email_via_api(**kwargs):
        captured_call.update(kwargs)
        return {"email_id": "test-msg-id", "error_code": "SUCCESS"}
    
    async def mock_record_email_sent(**kwargs):
        pass
    
    async def mock_create_manual_event(**kwargs):
        return {"id": 123, "status": "pending"}
    
    async def mock_record_success(event_id, *, attempt_number, response_status, response_body):
        return {
            "id": event_id,
            "status": "succeeded",
            "attempt_count": attempt_number,
            "response_status": response_status,
            "response_body": response_body,
        }
    
    from app.services import smtp2go
    from app.services import webhook_monitor
    from app.repositories import webhook_events as webhook_repo
    
    monkeypatch.setattr(smtp2go, "send_email_via_api", mock_send_email_via_api)
    monkeypatch.setattr(smtp2go, "record_email_sent", mock_record_email_sent)
    monkeypatch.setattr(webhook_monitor, "create_manual_event", mock_create_manual_event)
    monkeypatch.setattr(modules, "_record_success", mock_record_success)
    
    return captured_call


@pytest.mark.asyncio
async def test_invoke_smtp2go_with_html_key(monkeypatch, mock_smtp2go_dependencies):
    """Test _invoke_smtp2go with legacy 'html' key."""
    
    # Test with 'html' key (legacy format)
    settings = {
        "api_key": "test-api-key",
        "enable_tracking": True,
    }
    payload = {
        "recipients": ["test@example.com"],
        "subject": "Test Subject",
        "html": "<p>Test HTML body</p>",
        "sender": "sender@example.com",
    }
    
    result = await modules._invoke_smtp2go(settings, payload)
    
    assert mock_smtp2go_dependencies["html_body"] == "<p>Test HTML body</p>"
    assert mock_smtp2go_dependencies["subject"] == "Test Subject"
    assert mock_smtp2go_dependencies["to"] == ["test@example.com"]
    assert mock_smtp2go_dependencies["sender"] == "sender@example.com"


@pytest.mark.asyncio
async def test_invoke_smtp2go_with_html_body_key(monkeypatch, mock_smtp2go_dependencies):
    """Test _invoke_smtp2go with 'html_body' key (new format)."""
    
    # Test with 'html_body' key (new format - matches smtp2go.send_email_via_api signature)
    settings = {
        "api_key": "test-api-key",
        "enable_tracking": True,
    }
    payload = {
        "recipients": ["test@example.com"],
        "subject": "Test Subject",
        "html_body": "<h1>New Reply:</h1><p>Test content</p>",
        "sender": "sender@example.com",
    }
    
    result = await modules._invoke_smtp2go(settings, payload)
    
    assert mock_smtp2go_dependencies["html_body"] == "<h1>New Reply:</h1><p>Test content</p>"
    assert mock_smtp2go_dependencies["subject"] == "Test Subject"
    assert mock_smtp2go_dependencies["to"] == ["test@example.com"]
    assert mock_smtp2go_dependencies["sender"] == "sender@example.com"


@pytest.mark.asyncio
async def test_invoke_smtp2go_with_text_and_text_body_keys(monkeypatch, mock_smtp2go_dependencies):
    """Test _invoke_smtp2go accepts both 'text' and 'text_body' keys."""
    
    # Test with 'text' key (legacy format)
    settings = {
        "api_key": "test-api-key",
        "enable_tracking": False,
    }
    payload = {
        "recipients": ["test@example.com"],
        "subject": "Test Subject",
        "html": "<p>Test HTML</p>",
        "text": "Test plain text",
        "sender": "sender@example.com",
    }
    
    result = await modules._invoke_smtp2go(settings, payload)
    
    assert mock_smtp2go_dependencies["text_body"] == "Test plain text"
    
    # Reset and test with 'text_body' key (new format)
    mock_smtp2go_dependencies.clear()
    
    payload2 = {
        "recipients": ["test@example.com"],
        "subject": "Test Subject",
        "html_body": "<p>Test HTML</p>",
        "text_body": "Test plain text body",
        "sender": "sender@example.com",
    }
    
    result = await modules._invoke_smtp2go(settings, payload2)
    
    assert mock_smtp2go_dependencies["text_body"] == "Test plain text body"


@pytest.mark.asyncio
async def test_invoke_smtp2go_html_key_precedence(monkeypatch, mock_smtp2go_dependencies):
    """Test that 'html' key takes precedence over 'html_body' and 'body' keys."""
    
    # Test with both 'html' and 'html_body' - html should take precedence
    settings = {
        "api_key": "test-api-key",
        "enable_tracking": False,
    }
    payload = {
        "recipients": ["test@example.com"],
        "subject": "Test Subject",
        "html": "<p>HTML content</p>",
        "html_body": "<p>HTML body content</p>",
        "body": "<p>Body content</p>",
        "sender": "sender@example.com",
    }
    
    result = await modules._invoke_smtp2go(settings, payload)
    
    # 'html' should take precedence over 'html_body' and 'body'
    assert mock_smtp2go_dependencies["html_body"] == "<p>HTML content</p>"
