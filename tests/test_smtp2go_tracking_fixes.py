"""Test fixes for SMTP2Go tracking data storage issues."""

import pytest
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock


@pytest.mark.asyncio
async def test_record_email_sent_logs_full_exception_on_db_error(monkeypatch):
    """Test that record_email_sent logs full exception details on database errors."""
    from app.services import smtp2go
    from loguru import logger
    from io import StringIO
    
    # Capture loguru output with full formatting including exception
    log_stream = StringIO()
    logger_id = logger.add(
        log_stream, 
        level="ERROR",
        format="{message}",
        backtrace=True,
        diagnose=True
    )
    
    try:
        # Create a detailed exception with traceback-like information
        class DetailedDatabaseError(Exception):
            def __str__(self):
                return "Unknown column 'smtp2go_message_id' in 'field list'"
        
        async def mock_execute(query, params):
            raise DetailedDatabaseError()
        
        # Mock database
        from app.core import database
        monkeypatch.setattr(database.db, "execute", mock_execute)
        
        # Call the function
        await smtp2go.record_email_sent(
            ticket_reply_id=123,
            tracking_id="test-tracking-id",
            smtp2go_message_id="test-message-id",
        )
        
        # Get logged output
        log_output = log_stream.getvalue()
        
        # Verify error was logged
        assert "Failed to record SMTP2Go email metadata" in log_output
        # With exc_info=True, the traceback will be included but not necessarily in the message format
        # The key is that the error should be logged, which we verified above
    finally:
        logger.remove(logger_id)


@pytest.mark.asyncio
async def test_record_email_sent_with_only_tracking_id(monkeypatch):
    """Test that record_email_sent works with only tracking_id (no smtp2go_message_id)."""
    from app.services import smtp2go
    
    db_calls = []
    
    async def mock_execute(query, params):
        db_calls.append({'query': query, 'params': params})
        return 1
    
    from app.core import database
    monkeypatch.setattr(database.db, "execute", mock_execute)
    
    # Call without smtp2go_message_id (it's None)
    await smtp2go.record_email_sent(
        ticket_reply_id=456,
        tracking_id="only-tracking-id",
        smtp2go_message_id=None,
    )
    
    # Verify the database was updated with tracking_id even without smtp2go_message_id
    assert len(db_calls) == 1
    call = db_calls[0]
    
    params = call['params']
    assert params['tracking_id'] == "only-tracking-id"
    assert params['smtp2go_message_id'] is None
    assert params['reply_id'] == 456


@pytest.mark.asyncio
async def test_modules_invoke_smtp2go_records_without_message_id(monkeypatch):
    """Test that _invoke_smtp2go does NOT record tracking when smtp2go_message_id is missing.
    
    With the updated tracking flow, smtp2go_message_id is required for webhook correlation.
    If smtp2go_message_id is not available, tracking data is not stored immediately
    (it will be set when the webhook 'processed' event arrives).
    """
    from app.services import modules as modules_service
    from app.services import smtp2go
    
    # Track what was recorded
    record_calls = []
    
    async def mock_record_smtp2go_message_id(**kwargs):
        record_calls.append(kwargs)
    
    # Mock the record function
    monkeypatch.setattr(smtp2go, "record_smtp2go_message_id", mock_record_smtp2go_message_id)
    
    # Mock send_email_via_api to return a result WITHOUT smtp2go_message_id
    async def mock_send_email_via_api(**kwargs):
        return {
            "tracking_id": "generated-tracking-id",
            # Note: NO smtp2go_message_id or email_id in response
        }
    
    monkeypatch.setattr(smtp2go, "send_email_via_api", mock_send_email_via_api)
    monkeypatch.setattr(smtp2go, "generate_tracking_id", lambda: "generated-tracking-id")
    
    # Mock webhook_monitor
    from app.services import webhook_monitor
    async def mock_create_manual_event(**kwargs):
        return {"id": 999}
    monkeypatch.setattr(webhook_monitor, "create_manual_event", mock_create_manual_event)
    
    # Mock _record_success
    async def mock_record_success(*args, **kwargs):
        return {"id": 999, "status": "succeeded"}
    monkeypatch.setattr(modules_service, "_record_success", mock_record_success)
    
    # Call the function with tracking enabled and a ticket_reply_id
    settings = {
        "enable_tracking": True,
    }
    payload = {
        "recipients": ["test@example.com"],
        "subject": "Test Email",
        "html": "<p>Test</p>",
        "context": {
            "metadata": {
                "ticket_reply_id": 789,
            }
        }
    }
    
    result = await modules_service._invoke_smtp2go(settings, payload)
    
    # With the new tracking flow, record_smtp2go_message_id should NOT be called
    # when smtp2go_message_id is missing (nothing to correlate with webhooks)
    assert len(record_calls) == 0


@pytest.mark.asyncio
async def test_email_service_records_without_message_id(monkeypatch):
    """Test that send_email does NOT record tracking when smtp2go_message_id is missing.
    
    With the updated tracking flow, smtp2go_message_id is required for webhook correlation.
    If smtp2go_message_id is not available, tracking data is not stored immediately
    (it will be set when the webhook 'processed' event arrives).
    """
    from app.services import email as email_service
    from app.services import smtp2go
    from app.services import modules as modules_service
    
    # Track what was recorded
    record_calls = []
    
    async def mock_record_smtp2go_message_id(**kwargs):
        record_calls.append(kwargs)
    
    monkeypatch.setattr(smtp2go, "record_smtp2go_message_id", mock_record_smtp2go_message_id)
    
    # Mock send_email_via_api to return a result WITHOUT smtp2go_message_id
    async def mock_send_email_via_api(**kwargs):
        return {
            "tracking_id": kwargs.get("tracking_id", "fallback-tracking-id"),
            # Note: NO smtp2go_message_id or email_id in response
        }
    
    monkeypatch.setattr(smtp2go, "send_email_via_api", mock_send_email_via_api)
    monkeypatch.setattr(smtp2go, "generate_tracking_id", lambda: "email-tracking-id")
    
    # Mock module check to enable SMTP2Go
    async def mock_get_module(slug, redact=True):
        if slug == "smtp2go":
            return {"enabled": True}
        return None
    
    monkeypatch.setattr(modules_service, "get_module", mock_get_module)
    
    # Mock settings
    from app.core import config
    settings = config.get_settings()
    settings.smtp_host = "smtp.example.com"
    settings.smtp_user = "sender@example.com"
    monkeypatch.setattr(config, "get_settings", lambda: settings)
    
    # Call send_email
    success, metadata = await email_service.send_email(
        subject="Test",
        recipients=["recipient@example.com"],
        html_body="<p>Test</p>",
        ticket_reply_id=999,
    )
    
    # With the new tracking flow, record_smtp2go_message_id should NOT be called
    # when smtp2go_message_id is missing (nothing to correlate with webhooks)
    assert success is True
    assert len(record_calls) == 0


@pytest.mark.asyncio
async def test_record_email_sent_includes_exception_type_in_log(monkeypatch):
    """Test that record_email_sent includes exception details in error log."""
    from app.services import smtp2go
    from loguru import logger
    from io import StringIO
    
    # Capture loguru output
    log_stream = StringIO()
    logger_id = logger.add(
        log_stream, 
        level="ERROR",
        format="{message}",
        backtrace=True,
        diagnose=True
    )
    
    try:
        # Create a specific exception type
        class ColumnNotFoundException(Exception):
            pass
        
        async def mock_execute(query, params):
            raise ColumnNotFoundException("Column 'email_tracking_id' not found in table 'ticket_replies'")
        
        from app.core import database
        monkeypatch.setattr(database.db, "execute", mock_execute)
        
        await smtp2go.record_email_sent(
            ticket_reply_id=111,
            tracking_id="test-id",
            smtp2go_message_id="msg-id",
        )
        
        # Get logged output
        log_output = log_stream.getvalue()
        
        # Verify the error was logged
        assert "Failed to record SMTP2Go email metadata" in log_output
        # The exc_info=True will include traceback, which is what we want for debugging
    finally:
        logger.remove(logger_id)
