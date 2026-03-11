"""Tests for SMTP module accepting html_body, to, text_body, and sender payload fields."""

import asyncio
import pytest

from app.services import modules as modules_service
from app.services import email as email_service
from app.services import webhook_monitor


@pytest.fixture
def mock_smtp_dependencies(monkeypatch):
    """Mock SMTP dependencies and capture send_email parameters."""
    captured = {}

    class DummySMTP:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def ehlo(self):
            pass

        def starttls(self, **kwargs):
            pass

        def login(self, *args):
            pass

        def send_message(self, message):
            pass

    monkeypatch.setattr(email_service.smtplib, "SMTP", DummySMTP)

    async def mock_send_email(**kwargs):
        captured.update(kwargs)
        return True, {"id": 1}

    async def mock_create_manual_event(**kwargs):
        return {"id": 123}

    async def mock_record_success(*args, **kwargs):
        return {"id": 123, "status": "succeeded"}

    async def mock_record_failure(*args, **kwargs):
        return {"id": 123, "status": "failed"}

    monkeypatch.setattr(email_service, "send_email", mock_send_email)
    monkeypatch.setattr(webhook_monitor, "create_manual_event", mock_create_manual_event)
    monkeypatch.setattr(modules_service, "_record_success", mock_record_success)
    monkeypatch.setattr(modules_service, "_record_failure", mock_record_failure)

    return captured


def test_invoke_smtp_html_body_key(mock_smtp_dependencies):
    """Test that _invoke_smtp accepts 'html_body' key (used by ticket reply automation JSON)."""
    settings = {}
    payload = {
        "to": ["customer@example.com"],
        "subject": "RE: Ticket #123 - Test Issue",
        "html_body": "<p>Hi Customer,</p><p>Your ticket has been updated.</p>",
        "text_body": "Your ticket has been updated.",
        "sender": "myportal@hawkinsfamily.com.au",
    }

    result = asyncio.run(modules_service._invoke_smtp(settings, payload))

    assert mock_smtp_dependencies.get("html_body") == "<p>Hi Customer,</p><p>Your ticket has been updated.</p>"
    assert mock_smtp_dependencies.get("subject") == "RE: Ticket #123 - Test Issue"
    assert mock_smtp_dependencies.get("recipients") == ["customer@example.com"]


def test_invoke_smtp_to_key_for_recipients(mock_smtp_dependencies):
    """Test that _invoke_smtp accepts 'to' key for recipients."""
    settings = {}
    payload = {
        "to": ["user@example.com"],
        "subject": "Test",
        "html": "<p>Test</p>",
    }

    result = asyncio.run(modules_service._invoke_smtp(settings, payload))

    assert mock_smtp_dependencies.get("recipients") == ["user@example.com"]


def test_invoke_smtp_text_body_key(mock_smtp_dependencies):
    """Test that _invoke_smtp accepts 'text_body' key in addition to 'text'."""
    settings = {}
    payload = {
        "recipients": ["user@example.com"],
        "subject": "Test",
        "html": "<p>HTML content</p>",
        "text_body": "Plain text content",
    }

    result = asyncio.run(modules_service._invoke_smtp(settings, payload))

    assert mock_smtp_dependencies.get("text_body") == "Plain text content"


def test_invoke_smtp_sender_from_payload(mock_smtp_dependencies):
    """Test that _invoke_smtp uses 'sender' from payload, not just from settings."""
    settings = {"from_address": "default@example.com"}
    payload = {
        "recipients": ["user@example.com"],
        "subject": "Test",
        "html": "<p>Test</p>",
        "sender": "custom@example.com",
    }

    result = asyncio.run(modules_service._invoke_smtp(settings, payload))

    assert mock_smtp_dependencies.get("sender") == "custom@example.com"


def test_invoke_smtp_sender_falls_back_to_settings(mock_smtp_dependencies):
    """Test that _invoke_smtp falls back to settings from_address when no sender in payload."""
    settings = {"from_address": "settings@example.com"}
    payload = {
        "recipients": ["user@example.com"],
        "subject": "Test",
        "html": "<p>Test</p>",
    }

    result = asyncio.run(modules_service._invoke_smtp(settings, payload))

    assert mock_smtp_dependencies.get("sender") == "settings@example.com"


def test_invoke_smtp_html_key_takes_precedence_over_html_body(mock_smtp_dependencies):
    """Test that 'html' key takes precedence over 'html_body'."""
    settings = {}
    payload = {
        "recipients": ["user@example.com"],
        "subject": "Test",
        "html": "<p>HTML content</p>",
        "html_body": "<p>HTML body content</p>",
    }

    result = asyncio.run(modules_service._invoke_smtp(settings, payload))

    assert mock_smtp_dependencies.get("html_body") == "<p>HTML content</p>"


def test_invoke_smtp_ticket_reply_automation_format(mock_smtp_dependencies):
    """Test the exact payload format used by ticket reply automation."""
    settings = {}
    payload = {
        "html_body": "<p>Hi John,</p><p>Your ticket has a new reply.</p><p>Kind Regards,<br>Support</p>",
        "sender": "myportal@hawkinsfamily.com.au",
        "subject": "RE: Ticket #456 - Login Issue",
        "text_body": "Your ticket has a new reply.",
        "to": ["john.doe@example.com"],
    }

    result = asyncio.run(modules_service._invoke_smtp(settings, payload))

    assert mock_smtp_dependencies.get("html_body") == "<p>Hi John,</p><p>Your ticket has a new reply.</p><p>Kind Regards,<br>Support</p>"
    assert mock_smtp_dependencies.get("subject") == "RE: Ticket #456 - Login Issue"
    assert mock_smtp_dependencies.get("recipients") == ["john.doe@example.com"]
    assert mock_smtp_dependencies.get("sender") == "myportal@hawkinsfamily.com.au"
    assert mock_smtp_dependencies.get("text_body") == "Your ticket has a new reply."
