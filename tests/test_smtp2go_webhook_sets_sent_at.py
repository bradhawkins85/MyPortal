"""Test that email_sent_at is set when 'processed' webhook event arrives."""

import pytest
from datetime import datetime, timezone


@pytest.mark.asyncio
async def test_webhook_processed_event_sets_email_sent_at(monkeypatch):
    """Test that the 'processed' webhook event sets email_sent_at timestamp."""
    from app.services import smtp2go
    
    # Track database calls
    db_calls = []
    
    async def mock_fetch_one(query, params):
        """Return a mock ticket reply that was previously stored with smtp2go_message_id."""
        return {
            'id': 123,
            'email_tracking_id': 'test-tracking-id',
        }
    
    async def mock_execute(query, params):
        db_calls.append({'query': query, 'params': params})
        return 456  # event_id
    
    # Mock database
    from app.core import database
    monkeypatch.setattr(database.db, "fetch_one", mock_fetch_one)
    monkeypatch.setattr(database.db, "execute", mock_execute)
    
    # Test processing 'processed' event
    event_data = {
        "email_id": "smtp2go-msg-123",
        "recipient": "test@example.com",
        "timestamp": "2025-01-01T12:00:00Z",
        "event": "processed",
    }
    
    result = await smtp2go.process_webhook_event("processed", event_data)
    
    assert result is not None
    assert result['event_type'] == 'processed'
    
    # Should have 2 execute calls: insert tracking event + update ticket_replies
    assert len(db_calls) == 2
    
    # The update query should set BOTH email_processed_at AND email_sent_at
    update_call = db_calls[1]
    assert "email_processed_at" in update_call['query']
    assert "email_sent_at" in update_call['query']
    assert "UPDATE ticket_replies" in update_call['query']


@pytest.mark.asyncio
async def test_record_smtp2go_message_id_does_not_set_sent_at(monkeypatch):
    """Test that record_smtp2go_message_id does NOT set email_sent_at."""
    from app.services import smtp2go
    
    # Track database calls
    db_calls = []
    
    async def mock_execute(query, params):
        db_calls.append({'query': query, 'params': params})
        return 1
    
    # Mock database
    from app.core import database
    monkeypatch.setattr(database.db, "execute", mock_execute)
    
    # Call the function
    await smtp2go.record_smtp2go_message_id(
        ticket_reply_id=110,
        tracking_id="test-tracking-id",
        smtp2go_message_id="1vNPyV-FnQW0hPy67Z-PAWN",
    )
    
    # Verify the database was updated
    assert len(db_calls) == 1
    call = db_calls[0]
    
    # Should NOT contain email_sent_at
    assert "email_sent_at" not in call['query']
    
    # Should contain smtp2go_message_id and email_tracking_id
    assert "smtp2go_message_id" in call['query']
    assert "email_tracking_id" in call['query']
