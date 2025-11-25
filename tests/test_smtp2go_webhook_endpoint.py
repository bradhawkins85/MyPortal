"""Tests for SMTP2Go webhook endpoint."""

import json
import pytest
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, patch

from app.main import app


@pytest.fixture
def client():
    """Create a test client for the FastAPI app."""
    return TestClient(app)


# Sample webhook payloads from SMTP2Go based on the issue report
PROCESSED_EVENT = {
    "Message-Id": "<mail.1392590358.22776@example.org>",
    "Subject": "Mail test - please ignore",
    "auth": "exampleuser33905",
    "email_id": "1WFAMs-BI6MKi-K9",
    "event": "processed",
    "from": "exampleuser@example.org",
    "from_address": "exampleuser@example.org",
    "from_name": "",
    "id": "e18adc0cd7e1c75bd2a791697d135c8c",
    "message-id": "<mail.1392590358.22776@example.org>",
    "recipients": ["test@test.com", "test2@test2.com"],
    "sender": "exampleuser@example.org",
    "sendtime": "2015-08-04T22:39:34Z",
    "srchost": "203.0.113.22",
    "subject": "Mail test - please ignore",
    "time": "2019-07-03T22:46:33Z"
}

DELIVERED_EVENT = {
    "email_id": "1WFAMs-BI6MKi-K9",
    "event": "delivered",
    "recipient": "test@test.com",
    "timestamp": "2019-07-03T22:46:35Z",
    "message-id": "<mail.1392590358.22776@example.org>"
}

OPENED_EVENT = {
    "email_id": "1WFAMs-BI6MKi-K9",
    "event": "opened",
    "recipient": "test@test.com",
    "timestamp": "2019-07-03T22:50:00Z",
    "user_agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
    "ip": "203.0.113.1"
}

CLICKED_EVENT = {
    "email_id": "1WFAMs-BI6MKi-K9",
    "event": "clicked",
    "recipient": "test@test.com",
    "timestamp": "2019-07-03T22:51:00Z",
    "url": "https://example.com/page",
    "user_agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
    "ip": "203.0.113.1"
}

BOUNCED_EVENT = {
    "email_id": "1WFAMs-BI6MKi-K9",
    "event": "bounced",
    "recipient": "invalid@test.com",
    "timestamp": "2019-07-03T22:46:36Z",
    "bounce_type": "hard",
    "reason": "550 5.1.1 User unknown"
}

SPAM_EVENT = {
    "email_id": "1WFAMs-BI6MKi-K9",
    "event": "spam",
    "recipient": "test@test.com",
    "timestamp": "2019-07-03T22:52:00Z"
}

REJECTED_EVENT = {
    "email_id": "1WFAMs-BI6MKi-K9",
    "event": "rejected",
    "recipient": "test@test.com",
    "timestamp": "2019-07-03T22:46:34Z",
    "reason": "Invalid email address"
}


@pytest.mark.asyncio
async def test_webhook_accepts_single_event_object(client):
    """Test that webhook endpoint accepts a single event object (not a list)."""
    with patch('app.services.modules.get_module_settings', new_callable=AsyncMock) as mock_settings, \
         patch('app.services.smtp2go.process_webhook_event', new_callable=AsyncMock) as mock_process, \
         patch('app.services.webhook_monitor.log_incoming_webhook', new_callable=AsyncMock):
        
        # Mock no webhook secret configured (skip signature verification)
        mock_settings.return_value = None
        
        # Mock successful processing
        mock_process.return_value = {
            'id': 123,
            'tracking_id': 'test-tracking-id',
            'event_type': 'processed',
        }
        
        # Send processed event
        response = client.post(
            "/api/webhooks/smtp2go/events",
            json=PROCESSED_EVENT,
        )
        
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"


@pytest.mark.asyncio
async def test_webhook_signature_verification_can_be_disabled(client):
    """Ensure signature checks can be disabled even when a secret is configured."""
    with patch('app.services.modules.get_module_settings', new_callable=AsyncMock) as mock_settings, \
         patch('app.api.routes.smtp2go_webhooks.verify_webhook_signature', new_callable=AsyncMock) as mock_verify, \
         patch('app.services.smtp2go.process_webhook_event', new_callable=AsyncMock) as mock_process, \
         patch('app.services.webhook_monitor.log_incoming_webhook', new_callable=AsyncMock):

        mock_settings.return_value = {
            'webhook_secret': 'super-secret',
            'disable_webhook_signature_verification': True,
        }
        mock_process.return_value = {
            'id': 123,
            'tracking_id': 'test-tracking-id',
            'event_type': 'processed',
        }

        response = client.post(
            "/api/webhooks/smtp2go/events",
            json=PROCESSED_EVENT,
            headers={"X-Smtp2go-Signature": "invalid"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        mock_verify.assert_not_awaited()


@pytest.mark.asyncio
async def test_webhook_processed_event(client):
    """Test webhook with processed event."""
    with patch('app.services.modules.get_module_settings', new_callable=AsyncMock) as mock_settings, \
         patch('app.services.smtp2go.process_webhook_event', new_callable=AsyncMock) as mock_process, \
         patch('app.services.webhook_monitor.log_incoming_webhook', new_callable=AsyncMock):
        
        mock_settings.return_value = None
        mock_process.return_value = {'id': 123, 'event_type': 'processed'}
        
        response = client.post(
            "/api/webhooks/smtp2go/events",
            json=PROCESSED_EVENT,
        )
        
        assert response.status_code == 200
        mock_process.assert_called_once_with('processed', PROCESSED_EVENT)


@pytest.mark.asyncio
async def test_webhook_delivered_event(client):
    """Test webhook with delivered event."""
    with patch('app.services.modules.get_module_settings', new_callable=AsyncMock) as mock_settings, \
         patch('app.services.smtp2go.process_webhook_event', new_callable=AsyncMock) as mock_process, \
         patch('app.services.webhook_monitor.log_incoming_webhook', new_callable=AsyncMock):
        
        mock_settings.return_value = None
        mock_process.return_value = {'id': 123, 'event_type': 'delivered'}
        
        response = client.post(
            "/api/webhooks/smtp2go/events",
            json=DELIVERED_EVENT,
        )
        
        assert response.status_code == 200
        mock_process.assert_called_once_with('delivered', DELIVERED_EVENT)


@pytest.mark.asyncio
async def test_webhook_opened_event(client):
    """Test webhook with opened event."""
    with patch('app.services.modules.get_module_settings', new_callable=AsyncMock) as mock_settings, \
         patch('app.services.smtp2go.process_webhook_event', new_callable=AsyncMock) as mock_process, \
         patch('app.services.webhook_monitor.log_incoming_webhook', new_callable=AsyncMock):
        
        mock_settings.return_value = None
        mock_process.return_value = {'id': 123, 'event_type': 'open'}
        
        response = client.post(
            "/api/webhooks/smtp2go/events",
            json=OPENED_EVENT,
        )
        
        assert response.status_code == 200
        mock_process.assert_called_once_with('opened', OPENED_EVENT)


@pytest.mark.asyncio
async def test_webhook_records_events_when_processing_fails(client):
    """Ensure webhook events are persisted via fallback when processing fails."""
    with patch('app.services.modules.get_module_settings', new_callable=AsyncMock) as mock_settings, \
         patch('app.services.smtp2go.process_webhook_event', new_callable=AsyncMock) as mock_process, \
         patch('app.services.smtp2go.record_raw_webhook_event', new_callable=AsyncMock) as mock_fallback, \
         patch('app.services.webhook_monitor.log_incoming_webhook', new_callable=AsyncMock):

        mock_settings.return_value = None
        mock_process.return_value = None  # Simulate failure to process
        mock_fallback.return_value = {'id': 456, 'tracking_id': 'fallback-id', 'event_type': 'bounce'}

        response = client.post(
            "/api/webhooks/smtp2go/events",
            json=BOUNCED_EVENT,
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert data["processed"] == 1

        mock_process.assert_called_once_with('bounced', BOUNCED_EVENT)
        mock_fallback.assert_called_once_with('bounced', BOUNCED_EVENT)


@pytest.mark.asyncio
async def test_webhook_clicked_event(client):
    """Test webhook with clicked event."""
    with patch('app.services.modules.get_module_settings', new_callable=AsyncMock) as mock_settings, \
         patch('app.services.smtp2go.process_webhook_event', new_callable=AsyncMock) as mock_process, \
         patch('app.services.webhook_monitor.log_incoming_webhook', new_callable=AsyncMock):
        
        mock_settings.return_value = None
        mock_process.return_value = {'id': 123, 'event_type': 'click'}
        
        response = client.post(
            "/api/webhooks/smtp2go/events",
            json=CLICKED_EVENT,
        )
        
        assert response.status_code == 200
        mock_process.assert_called_once_with('clicked', CLICKED_EVENT)


@pytest.mark.asyncio
async def test_webhook_bounced_event(client):
    """Test webhook with bounced event."""
    with patch('app.services.modules.get_module_settings', new_callable=AsyncMock) as mock_settings, \
         patch('app.services.smtp2go.process_webhook_event', new_callable=AsyncMock) as mock_process, \
         patch('app.services.webhook_monitor.log_incoming_webhook', new_callable=AsyncMock):
        
        mock_settings.return_value = None
        mock_process.return_value = {'id': 123, 'event_type': 'bounce'}
        
        response = client.post(
            "/api/webhooks/smtp2go/events",
            json=BOUNCED_EVENT,
        )
        
        assert response.status_code == 200
        mock_process.assert_called_once_with('bounced', BOUNCED_EVENT)


@pytest.mark.asyncio
async def test_webhook_spam_event(client):
    """Test webhook with spam event."""
    with patch('app.services.modules.get_module_settings', new_callable=AsyncMock) as mock_settings, \
         patch('app.services.smtp2go.process_webhook_event', new_callable=AsyncMock) as mock_process, \
         patch('app.services.webhook_monitor.log_incoming_webhook', new_callable=AsyncMock):
        
        mock_settings.return_value = None
        mock_process.return_value = {'id': 123, 'event_type': 'spam'}
        
        response = client.post(
            "/api/webhooks/smtp2go/events",
            json=SPAM_EVENT,
        )
        
        assert response.status_code == 200
        mock_process.assert_called_once_with('spam', SPAM_EVENT)


@pytest.mark.asyncio
async def test_webhook_rejected_event(client):
    """Test webhook with rejected event."""
    with patch('app.services.modules.get_module_settings', new_callable=AsyncMock) as mock_settings, \
         patch('app.services.smtp2go.process_webhook_event', new_callable=AsyncMock) as mock_process, \
         patch('app.services.webhook_monitor.log_incoming_webhook', new_callable=AsyncMock):
        
        mock_settings.return_value = None
        mock_process.return_value = {'id': 123, 'event_type': 'rejected'}
        
        response = client.post(
            "/api/webhooks/smtp2go/events",
            json=REJECTED_EVENT,
        )
        
        assert response.status_code == 200
        mock_process.assert_called_once_with('rejected', REJECTED_EVENT)


@pytest.mark.asyncio
async def test_webhook_processing_failure(client):
    """Test webhook when event processing fails."""
    with patch('app.services.modules.get_module_settings', new_callable=AsyncMock) as mock_settings, \
         patch('app.services.smtp2go.process_webhook_event', new_callable=AsyncMock) as mock_process, \
         patch('app.services.webhook_monitor.log_incoming_webhook', new_callable=AsyncMock):
        
        mock_settings.return_value = None
        # Simulate processing failure (returns None)
        mock_process.return_value = None
        
        response = client.post(
            "/api/webhooks/smtp2go/events",
            json=DELIVERED_EVENT,
        )
        
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "failed"
        assert "error" in data


@pytest.mark.asyncio
async def test_webhook_processing_exception(client):
    """Test webhook when event processing raises an exception."""
    with patch('app.services.modules.get_module_settings', new_callable=AsyncMock) as mock_settings, \
         patch('app.services.smtp2go.process_webhook_event', new_callable=AsyncMock) as mock_process, \
         patch('app.services.webhook_monitor.log_incoming_webhook', new_callable=AsyncMock):
        
        mock_settings.return_value = None
        # Simulate processing exception
        mock_process.side_effect = Exception("Database error")
        
        response = client.post(
            "/api/webhooks/smtp2go/events",
            json=DELIVERED_EVENT,
        )
        
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "failed"
        assert "error" in data
        assert "Database error" in data["error"]


@pytest.mark.asyncio
async def test_webhook_accepts_list_payload(client):
    """Test that webhook endpoint accepts and processes a list payload."""
    with patch('app.services.modules.get_module_settings', new_callable=AsyncMock) as mock_settings, \
         patch('app.services.smtp2go.process_webhook_event', new_callable=AsyncMock) as mock_process, \
         patch('app.services.webhook_monitor.log_incoming_webhook', new_callable=AsyncMock):

        mock_settings.return_value = None
        mock_process.side_effect = [
            {'id': 1, 'event_type': 'delivered', 'tracking_id': 'track-1'},
            {'id': 2, 'event_type': 'open', 'tracking_id': 'track-1'},
        ]

        response = client.post(
            "/api/webhooks/smtp2go/events",
            json=[DELIVERED_EVENT, OPENED_EVENT],
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        assert data["processed"] == 2
        mock_process.assert_any_call('delivered', DELIVERED_EVENT)
        mock_process.assert_any_call('opened', OPENED_EVENT)
