"""Tests for SMTP2Go webhook signature verification."""

import hashlib
import hmac
import json
import pytest
import base64
from fastapi.testclient import TestClient
from unittest.mock import AsyncMock, patch

from app.main import app


@pytest.fixture
def client():
    """Create a test client for the FastAPI app."""
    return TestClient(app)


DELIVERED_EVENT = {
    "email_id": "1WFAMs-BI6MKi-K9",
    "event": "delivered",
    "recipient": "test@test.com",
    "timestamp": "2019-07-03T22:46:35Z",
    "message-id": "<mail.1392590358.22776@example.org>"
}


def compute_signature(payload_bytes: bytes, secret: str) -> str:
    """Compute HMAC-SHA256 signature for webhook payload."""
    return hmac.new(
        secret.encode('utf-8'),
        payload_bytes,
        hashlib.sha256
    ).hexdigest()


def test_webhook_with_valid_signature(client):
    """Test webhook with valid signature."""
    webhook_secret = "test-secret-key-12345"
    # FastAPI's TestClient will serialize this, so we need to use the same serialization
    # when computing the signature
    payload_str = json.dumps(DELIVERED_EVENT, separators=(',', ':'))
    payload_bytes = payload_str.encode('utf-8')
    valid_signature = compute_signature(payload_bytes, webhook_secret)
    
    with patch('app.services.modules.get_module_settings', new_callable=AsyncMock) as mock_settings, \
         patch('app.services.smtp2go.process_webhook_event', new_callable=AsyncMock) as mock_process, \
         patch('app.services.webhook_monitor.log_incoming_webhook', new_callable=AsyncMock):
        
        # Mock module settings with webhook secret
        mock_settings.return_value = {
            'api_key': 'test-api-key',
            'webhook_secret': webhook_secret,
        }
        
        # Mock successful processing
        mock_process.return_value = {
            'id': 123,
            'tracking_id': 'test-tracking-id',
            'event_type': 'delivered',
        }
        
        # Send webhook with valid signature
        # Use content instead of json to control exact bytes sent
        response = client.post(
            "/api/webhooks/smtp2go/events",
            content=payload_bytes,
            headers={
                "X-Smtp2go-Signature": valid_signature,
                "Content-Type": "application/json",
            },
        )
        
        assert response.status_code == 200, f"Response: {response.text}"
        data = response.json()
        assert data["status"] == "success"
        assert data["processed"] == 1


def test_webhook_with_invalid_signature(client):
    """Test webhook with invalid signature."""
    webhook_secret = "test-secret-key-12345"
    invalid_signature = "0" * 64  # Invalid signature
    
    with patch('app.services.modules.get_module_settings', new_callable=AsyncMock) as mock_settings, \
         patch('app.services.webhook_monitor.log_incoming_webhook', new_callable=AsyncMock):
        
        # Mock module settings with webhook secret
        mock_settings.return_value = {
            'api_key': 'test-api-key',
            'webhook_secret': webhook_secret,
        }
        
        # Send webhook with invalid signature
        response = client.post(
            "/api/webhooks/smtp2go/events",
            json=DELIVERED_EVENT,
            headers={"X-Smtp2go-Signature": invalid_signature},
        )
        
        assert response.status_code == 401
        assert "Invalid webhook signature" in response.json()["detail"]


def test_webhook_without_signature_when_secret_configured(client):
    """Test webhook without signature when secret is configured."""
    webhook_secret = "test-secret-key-12345"
    
    with patch('app.services.modules.get_module_settings', new_callable=AsyncMock) as mock_settings, \
         patch('app.services.webhook_monitor.log_incoming_webhook', new_callable=AsyncMock):
        
        # Mock module settings with webhook secret
        mock_settings.return_value = {
            'api_key': 'test-api-key',
            'webhook_secret': webhook_secret,
        }
        
        # Send webhook without signature
        response = client.post(
            "/api/webhooks/smtp2go/events",
            json=DELIVERED_EVENT,
        )
        
        assert response.status_code == 401
        assert "Invalid webhook signature" in response.json()["detail"]


def test_webhook_with_prefixed_signature(client):
    """Test webhook with signature that has a prefix like 'sha256='."""
    webhook_secret = "test-secret-key-12345"
    payload_str = json.dumps(DELIVERED_EVENT, separators=(',', ':'))
    payload_bytes = payload_str.encode('utf-8')
    signature = compute_signature(payload_bytes, webhook_secret)
    prefixed_signature = f"sha256={signature}"
    
    with patch('app.services.modules.get_module_settings', new_callable=AsyncMock) as mock_settings, \
         patch('app.services.smtp2go.process_webhook_event', new_callable=AsyncMock) as mock_process, \
         patch('app.services.webhook_monitor.log_incoming_webhook', new_callable=AsyncMock):
        
        # Mock module settings with webhook secret
        mock_settings.return_value = {
            'api_key': 'test-api-key',
            'webhook_secret': webhook_secret,
        }
        
        # Mock successful processing
        mock_process.return_value = {
            'id': 123,
            'tracking_id': 'test-tracking-id',
            'event_type': 'delivered',
        }
        
        # Send webhook with prefixed signature
        response = client.post(
            "/api/webhooks/smtp2go/events",
            content=payload_bytes,
            headers={
                "X-Smtp2go-Signature": prefixed_signature,
                "Content-Type": "application/json",
            },
        )
        
        assert response.status_code == 200, f"Response: {response.text}"
        data = response.json()
        assert data["status"] == "success"


def test_webhook_signature_with_list_payload(client):
    """Test webhook signature verification with a list of events."""
    webhook_secret = "test-secret-key-12345"
    payload = [DELIVERED_EVENT, DELIVERED_EVENT]
    payload_str = json.dumps(payload, separators=(',', ':'))
    payload_bytes = payload_str.encode('utf-8')
    valid_signature = compute_signature(payload_bytes, webhook_secret)
    
    with patch('app.services.modules.get_module_settings', new_callable=AsyncMock) as mock_settings, \
         patch('app.services.smtp2go.process_webhook_event', new_callable=AsyncMock) as mock_process, \
         patch('app.services.webhook_monitor.log_incoming_webhook', new_callable=AsyncMock):
        
        # Mock module settings with webhook secret
        mock_settings.return_value = {
            'api_key': 'test-api-key',
            'webhook_secret': webhook_secret,
        }
        
        # Mock successful processing
        mock_process.return_value = {
            'id': 123,
            'tracking_id': 'test-tracking-id',
            'event_type': 'delivered',
        }
        
        # Send webhook with valid signature
        response = client.post(
            "/api/webhooks/smtp2go/events",
            content=payload_bytes,
            headers={
                "X-Smtp2go-Signature": valid_signature,
                "Content-Type": "application/json",
            },
        )
        
        assert response.status_code == 200, f"Response: {response.text}"
        data = response.json()
        assert data["status"] == "success"
        assert data["processed"] == 2


def test_webhook_with_base64_signature(client):
    """Test webhook verification when SMTP2Go sends a base64 signature."""
    webhook_secret = "test-secret-key-12345"
    payload_str = json.dumps(DELIVERED_EVENT, separators=(',', ':'))
    payload_bytes = payload_str.encode('utf-8')

    digest = hmac.new(
        webhook_secret.encode('utf-8'),
        payload_bytes,
        hashlib.sha256
    ).digest()
    base64_signature = base64.b64encode(digest).decode('ascii')

    with patch('app.services.modules.get_module_settings', new_callable=AsyncMock) as mock_settings, \
         patch('app.services.smtp2go.process_webhook_event', new_callable=AsyncMock) as mock_process, \
         patch('app.services.webhook_monitor.log_incoming_webhook', new_callable=AsyncMock):

        mock_settings.return_value = {
            'api_key': 'test-api-key',
            'webhook_secret': webhook_secret,
        }

        mock_process.return_value = {
            'id': 123,
            'tracking_id': 'test-tracking-id',
            'event_type': 'delivered',
        }

        response = client.post(
            "/api/webhooks/smtp2go/events",
            content=payload_bytes,
            headers={
                "X-Smtp2go-Signature": base64_signature,
                "Content-Type": "application/json",
            },
        )

        assert response.status_code == 200, f"Response: {response.text}"
        data = response.json()
        assert data["status"] == "success"


def test_webhook_with_uppercase_hex_signature(client):
    """Test webhook verification with uppercase hex digest."""
    webhook_secret = "test-secret-key-12345"
    payload_str = json.dumps(DELIVERED_EVENT, separators=(',', ':'))
    payload_bytes = payload_str.encode('utf-8')
    signature = compute_signature(payload_bytes, webhook_secret).upper()

    with patch('app.services.modules.get_module_settings', new_callable=AsyncMock) as mock_settings, \
         patch('app.services.smtp2go.process_webhook_event', new_callable=AsyncMock) as mock_process, \
         patch('app.services.webhook_monitor.log_incoming_webhook', new_callable=AsyncMock):

        mock_settings.return_value = {
            'api_key': 'test-api-key',
            'webhook_secret': webhook_secret,
        }

        mock_process.return_value = {
            'id': 123,
            'tracking_id': 'test-tracking-id',
            'event_type': 'delivered',
        }

        response = client.post(
            "/api/webhooks/smtp2go/events",
            content=payload_bytes,
            headers={
                "X-Smtp2go-Signature": signature,
                "Content-Type": "application/json",
            },
        )

        assert response.status_code == 200, f"Response: {response.text}"
        data = response.json()
        assert data["status"] == "success"


def test_webhook_with_timestamp_signature(client):
    """Test webhook verification with SMTP2Go's timestamp-based signature format."""
    webhook_secret = "test-secret-key-12345"
    payload_str = json.dumps(DELIVERED_EVENT, separators=(',', ':'))
    payload_bytes = payload_str.encode('utf-8')
    timestamp = "1700000000"
    
    # Compute signature the way SMTP2Go does: HMAC-SHA256(secret, "<timestamp>.<payload>")
    signed_payload = f"{timestamp}.".encode('utf-8') + payload_bytes
    signature = hmac.new(
        webhook_secret.encode('utf-8'),
        signed_payload,
        hashlib.sha256
    ).hexdigest()
    
    # Format: t=<timestamp>,v1=<signature>
    timestamp_signature = f"t={timestamp},v1={signature}"

    with patch('app.services.modules.get_module_settings', new_callable=AsyncMock) as mock_settings, \
         patch('app.services.smtp2go.process_webhook_event', new_callable=AsyncMock) as mock_process, \
         patch('app.services.webhook_monitor.log_incoming_webhook', new_callable=AsyncMock):

        mock_settings.return_value = {
            'api_key': 'test-api-key',
            'webhook_secret': webhook_secret,
        }

        mock_process.return_value = {
            'id': 123,
            'tracking_id': 'test-tracking-id',
            'event_type': 'delivered',
        }

        response = client.post(
            "/api/webhooks/smtp2go/events",
            content=payload_bytes,
            headers={
                "X-Smtp2go-Signature": timestamp_signature,
                "Content-Type": "application/json",
            },
        )

        assert response.status_code == 200, f"Response: {response.text}"
        data = response.json()
        assert data["status"] == "success"


def test_webhook_with_invalid_timestamp_signature(client):
    """Test webhook rejection with invalid timestamp-based signature."""
    webhook_secret = "test-secret-key-12345"
    payload_str = json.dumps(DELIVERED_EVENT, separators=(',', ':'))
    payload_bytes = payload_str.encode('utf-8')
    
    # Invalid signature in timestamp format
    invalid_timestamp_signature = "t=1700000000,v1=" + ("0" * 64)

    with patch('app.services.modules.get_module_settings', new_callable=AsyncMock) as mock_settings, \
         patch('app.services.webhook_monitor.log_incoming_webhook', new_callable=AsyncMock):

        mock_settings.return_value = {
            'api_key': 'test-api-key',
            'webhook_secret': webhook_secret,
        }

        response = client.post(
            "/api/webhooks/smtp2go/events",
            content=payload_bytes,
            headers={
                "X-Smtp2go-Signature": invalid_timestamp_signature,
                "Content-Type": "application/json",
            },
        )

        assert response.status_code == 401
        assert "Invalid webhook signature" in response.json()["detail"]


def test_webhook_with_timestamp_signature_uppercase(client):
    """Test webhook verification with uppercase timestamp-based signature."""
    webhook_secret = "test-secret-key-12345"
    payload_str = json.dumps(DELIVERED_EVENT, separators=(',', ':'))
    payload_bytes = payload_str.encode('utf-8')
    timestamp = "1700000000"
    
    # Compute signature and convert to uppercase
    signed_payload = f"{timestamp}.".encode('utf-8') + payload_bytes
    signature = hmac.new(
        webhook_secret.encode('utf-8'),
        signed_payload,
        hashlib.sha256
    ).hexdigest().upper()
    
    timestamp_signature = f"t={timestamp},v1={signature}"

    with patch('app.services.modules.get_module_settings', new_callable=AsyncMock) as mock_settings, \
         patch('app.services.smtp2go.process_webhook_event', new_callable=AsyncMock) as mock_process, \
         patch('app.services.webhook_monitor.log_incoming_webhook', new_callable=AsyncMock):

        mock_settings.return_value = {
            'api_key': 'test-api-key',
            'webhook_secret': webhook_secret,
        }

        mock_process.return_value = {
            'id': 123,
            'tracking_id': 'test-tracking-id',
            'event_type': 'delivered',
        }

        response = client.post(
            "/api/webhooks/smtp2go/events",
            content=payload_bytes,
            headers={
                "X-Smtp2go-Signature": timestamp_signature,
                "Content-Type": "application/json",
            },
        )

        assert response.status_code == 200, f"Response: {response.text}"
        data = response.json()
        assert data["status"] == "success"
