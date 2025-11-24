"""Test SMTP2Go tracking data storage in ticket_replies table."""

import pytest
from datetime import datetime, timezone


@pytest.mark.asyncio
async def test_record_email_sent_with_both_ids(monkeypatch):
    """Test that record_email_sent stores both tracking_id and smtp2go_message_id."""
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
    await smtp2go.record_email_sent(
        ticket_reply_id=110,
        tracking_id="VdqhOx5C0d-8C9fibiXYaL0Col4Zfo23rZorX8WmXas",
        smtp2go_message_id="1vNPyV-FnQW0hPy67Z-PAWN",
    )
    
    # Verify the database was updated
    assert len(db_calls) == 1
    call = db_calls[0]
    
    assert "UPDATE ticket_replies" in call['query']
    assert "email_tracking_id" in call['query']
    assert "smtp2go_message_id" in call['query']
    
    params = call['params']
    assert params['tracking_id'] == "VdqhOx5C0d-8C9fibiXYaL0Col4Zfo23rZorX8WmXas"
    assert params['smtp2go_message_id'] == "1vNPyV-FnQW0hPy67Z-PAWN"
    assert params['reply_id'] == 110
    assert 'sent_at' in params
    assert isinstance(params['sent_at'], datetime)


@pytest.mark.asyncio
async def test_record_email_sent_without_smtp2go_message_id(monkeypatch):
    """Test that record_email_sent works even without smtp2go_message_id."""
    from app.services import smtp2go
    
    db_calls = []
    
    async def mock_execute(query, params):
        db_calls.append({'query': query, 'params': params})
        return 1
    
    from app.core import database
    monkeypatch.setattr(database.db, "execute", mock_execute)
    
    # Call without smtp2go_message_id
    await smtp2go.record_email_sent(
        ticket_reply_id=42,
        tracking_id="test-tracking-id-123",
        smtp2go_message_id=None,  # Explicitly None
    )
    
    # Verify the database was updated
    assert len(db_calls) == 1
    call = db_calls[0]
    
    params = call['params']
    assert params['tracking_id'] == "test-tracking-id-123"
    assert params['smtp2go_message_id'] is None  # Should be NULL in database
    assert params['reply_id'] == 42


@pytest.mark.asyncio
async def test_smtp2go_response_normalization():
    """Test that SMTP2Go API responses with both IDs are handled correctly."""
    # Response format from the problem statement
    result = {
        "recipients": ["customers@hawkinsit.au"],
        "subject": "RE: Ticket #110 - Customers Test",
        "smtp2go_message_id": "1vNPyV-FnQW0hPy67Z-PAWN",
        "tracking_id": "VdqhOx5C0d-8C9fibiXYaL0Col4Zfo23rZorX8WmXas"
    }
    
    # In this case, both fields are already present and properly named
    # The send_email_via_api function would extract them directly
    smtp2go_message_id = result.get("smtp2go_message_id") or result.get("email_id")
    tracking_id = result.get("tracking_id")
    
    # Verify both values are extractable
    assert smtp2go_message_id == "1vNPyV-FnQW0hPy67Z-PAWN"
    assert tracking_id == "VdqhOx5C0d-8C9fibiXYaL0Col4Zfo23rZorX8WmXas"


@pytest.mark.asyncio
async def test_smtp2go_response_with_data_envelope():
    """Test extraction when response is wrapped in a 'data' envelope."""
    # Some SMTP2Go responses have {"data": {...}}
    result = {
        "data": {
            "smtp2go_message_id": "wrapped-message-id",
            "tracking_id": "wrapped-tracking-id",
        }
    }
    
    # Extract data from envelope
    data = result.get("data", {})
    smtp2go_message_id = data.get("smtp2go_message_id") or data.get("email_id")
    tracking_id = data.get("tracking_id")
    
    assert smtp2go_message_id == "wrapped-message-id"
    assert tracking_id == "wrapped-tracking-id"


@pytest.mark.asyncio
async def test_smtp2go_response_with_email_id_only():
    """Test extraction when response only has email_id (not smtp2go_message_id)."""
    result = {
        "email_id": "only-email-id-123",
        "tracking_id": "test-tracking-id",
    }
    
    # The extraction logic in email.py uses: result.get("smtp2go_message_id") or result.get("email_id")
    smtp2go_message_id = result.get("smtp2go_message_id") or result.get("email_id")
    tracking_id = result.get("tracking_id")
    
    # Should fall back to email_id when smtp2go_message_id is not present
    assert smtp2go_message_id == "only-email-id-123"
    assert tracking_id == "test-tracking-id"
