"""Test for SMTP2Go processed event webhook handling.

This test specifically verifies the fix for the issue:
"Incoming email tracking webhooks from SMTP2Go not working"

The issue was that "processed" events from SMTP2Go were not being handled,
resulting in "Event processing failed - unknown email ID or message not tracked" errors.
"""

import pytest
from unittest.mock import AsyncMock, patch
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client():
    """Create a test client for the FastAPI app."""
    return TestClient(app)


@pytest.mark.asyncio
async def test_processed_event_creates_tracking_entry(client):
    """Test that a 'processed' event from SMTP2Go creates a tracking entry."""
    processed_event = {
        "Message-Id": "<test@example.org>",
        "Subject": "Test Email",
        "email_id": "test-smtp2go-id-123",
        "event": "processed",
        "from": "sender@example.org",
        "recipients": ["recipient@example.com"],
        "timestamp": "2024-01-15T10:30:00Z"
    }
    
    with patch('app.services.modules.get_module_settings', new_callable=AsyncMock) as mock_settings, \
         patch('app.services.smtp2go.process_webhook_event', new_callable=AsyncMock) as mock_process, \
         patch('app.services.webhook_monitor.log_incoming_webhook', new_callable=AsyncMock):
        
        # Mock no webhook secret configured
        mock_settings.return_value = None
        
        # Mock successful processing - simulate finding a tracked email
        mock_process.return_value = {
            'id': 999,
            'tracking_id': 'test-tracking-id-abc',
            'event_type': 'processed',
            'occurred_at': '2024-01-15T10:30:00+00:00'
        }
        
        # Send the processed event
        response = client.post(
            "/api/webhooks/smtp2go/events",
            json=processed_event,
        )
        
        # Verify successful processing
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert data["processed"] == 1
        
        # Verify the event was passed to process_webhook_event with correct parameters
        mock_process.assert_called_once_with('processed', processed_event)


@pytest.mark.asyncio
async def test_rejected_event_creates_tracking_entry(client):
    """Test that a 'rejected' event from SMTP2Go creates a tracking entry."""
    rejected_event = {
        "email_id": "test-smtp2go-id-456",
        "event": "rejected",
        "recipient": "baduser@example.com",
        "timestamp": "2024-01-15T10:31:00Z",
        "reason": "Invalid email address"
    }
    
    with patch('app.services.modules.get_module_settings', new_callable=AsyncMock) as mock_settings, \
         patch('app.services.smtp2go.process_webhook_event', new_callable=AsyncMock) as mock_process, \
         patch('app.services.webhook_monitor.log_incoming_webhook', new_callable=AsyncMock):
        
        # Mock no webhook secret configured
        mock_settings.return_value = None
        
        # Mock successful processing
        mock_process.return_value = {
            'id': 1000,
            'tracking_id': 'test-tracking-id-def',
            'event_type': 'rejected',
            'occurred_at': '2024-01-15T10:31:00+00:00'
        }
        
        # Send the rejected event
        response = client.post(
            "/api/webhooks/smtp2go/events",
            json=rejected_event,
        )
        
        # Verify successful processing
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert data["processed"] == 1
        
        # Verify the event was passed to process_webhook_event with correct parameters
        mock_process.assert_called_once_with('rejected', rejected_event)


@pytest.mark.asyncio
async def test_processed_event_not_found_is_recorded(client):
    """Test that a 'processed' event for unknown email is still recorded."""
    processed_event = {
        "email_id": "unknown-smtp2go-id",
        "event": "processed",
        "timestamp": "2024-01-15T10:32:00Z"
    }

    with patch('app.services.modules.get_module_settings', new_callable=AsyncMock) as mock_settings, \
         patch('app.services.smtp2go.process_webhook_event', new_callable=AsyncMock) as mock_process, \
         patch('app.services.webhook_monitor.log_incoming_webhook', new_callable=AsyncMock):

        # Mock no webhook secret configured
        mock_settings.return_value = None

        # Mock successful recording even without a matching ticket reply
        mock_process.return_value = {
            'id': 321,
            'tracking_id': 'unknown-smtp2go-id',
            'event_type': 'processed',
        }

        # Send the processed event
        response = client.post(
            "/api/webhooks/smtp2go/events",
            json=processed_event,
        )

        # Verify successful processing
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert data["processed"] == 1
