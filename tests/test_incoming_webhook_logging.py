"""Tests for incoming webhook logging functionality."""

import pytest
from unittest.mock import AsyncMock, patch

from app.services import webhook_monitor


@pytest.mark.asyncio
async def test_log_incoming_webhook_success():
    """Test logging a successful incoming webhook."""
    with patch('app.repositories.webhook_events.create_event', new_callable=AsyncMock) as mock_create, \
         patch('app.repositories.webhook_events.record_attempt', new_callable=AsyncMock) as mock_record, \
         patch('app.repositories.webhook_events.mark_event_completed', new_callable=AsyncMock) as mock_complete, \
         patch('app.repositories.webhook_events.get_event', new_callable=AsyncMock) as mock_get:
        
        # Mock the event creation
        mock_create.return_value = {'id': 123}
        mock_get.return_value = {
            'id': 123,
            'name': 'Test Webhook',
            'direction': 'incoming',
            'status': 'succeeded',
        }
        
        result = await webhook_monitor.log_incoming_webhook(
            name="Test Webhook",
            source_url="https://example.com/webhook",
            payload={"test": "data"},
            headers={"Content-Type": "application/json"},
            response_status=200,
            response_body="OK",
        )
        
        # Verify create_event was called with correct parameters
        mock_create.assert_called_once()
        call_kwargs = mock_create.call_args.kwargs
        assert call_kwargs['name'] == "Test Webhook"
        assert call_kwargs['direction'] == 'incoming'
        assert call_kwargs['source_url'] == "https://example.com/webhook"
        assert call_kwargs['payload'] == {"test": "data"}
        
        # Verify record_attempt was called
        mock_record.assert_called_once()
        attempt_kwargs = mock_record.call_args.kwargs
        assert attempt_kwargs['event_id'] == 123
        assert attempt_kwargs['status'] == 'succeeded'
        assert attempt_kwargs['response_status'] == 200
        
        # Verify mark_event_completed was called
        mock_complete.assert_called_once_with(
            123,
            attempt_number=1,
            response_status=200,
            response_body="OK",
        )
        
        # Verify result
        assert result['id'] == 123
        assert result['status'] == 'succeeded'


@pytest.mark.asyncio
async def test_log_incoming_webhook_failure():
    """Test logging a failed incoming webhook."""
    with patch('app.repositories.webhook_events.create_event', new_callable=AsyncMock) as mock_create, \
         patch('app.repositories.webhook_events.record_attempt', new_callable=AsyncMock) as mock_record, \
         patch('app.repositories.webhook_events.mark_event_failed', new_callable=AsyncMock) as mock_fail, \
         patch('app.repositories.webhook_events.get_event', new_callable=AsyncMock) as mock_get:
        
        mock_create.return_value = {'id': 456}
        mock_get.return_value = {
            'id': 456,
            'name': 'Failed Webhook',
            'direction': 'incoming',
            'status': 'failed',
        }
        
        result = await webhook_monitor.log_incoming_webhook(
            name="Failed Webhook",
            source_url="https://example.com/webhook",
            payload={"test": "data"},
            headers={"Content-Type": "application/json"},
            response_status=500,
            response_body="Internal Server Error",
            error_message="Processing failed",
        )
        
        # Verify create_event was called
        mock_create.assert_called_once()
        
        # Verify record_attempt was called with failed status
        mock_record.assert_called_once()
        attempt_kwargs = mock_record.call_args.kwargs
        assert attempt_kwargs['status'] == 'failed'
        assert attempt_kwargs['error_message'] == "Processing failed"
        
        # Verify mark_event_failed was called
        mock_fail.assert_called_once()
        
        # Verify result
        assert result['status'] == 'failed'


@pytest.mark.asyncio
async def test_log_incoming_webhook_redacts_sensitive_headers():
    """Test that sensitive headers are redacted in incoming webhook logs."""
    with patch('app.repositories.webhook_events.create_event', new_callable=AsyncMock) as mock_create, \
         patch('app.repositories.webhook_events.record_attempt', new_callable=AsyncMock) as mock_record, \
         patch('app.repositories.webhook_events.mark_event_completed', new_callable=AsyncMock), \
         patch('app.repositories.webhook_events.get_event', new_callable=AsyncMock) as mock_get:
        
        mock_create.return_value = {'id': 789}
        mock_get.return_value = {'id': 789, 'status': 'succeeded'}
        
        await webhook_monitor.log_incoming_webhook(
            name="Webhook with Auth",
            source_url="https://example.com/webhook",
            payload={"test": "data"},
            headers={
                "Content-Type": "application/json",
                "Authorization": "Bearer secret-token",
                "X-API-Key": "api-key-value",
            },
            response_status=200,
        )
        
        # Verify that sensitive headers were redacted
        mock_record.assert_called_once()
        attempt_kwargs = mock_record.call_args.kwargs
        request_headers = attempt_kwargs['request_headers']
        
        assert request_headers['Content-Type'] == 'application/json'
        assert request_headers['Authorization'] == '***REDACTED***'
        assert request_headers['X-API-Key'] == '***REDACTED***'


@pytest.mark.asyncio
async def test_enqueue_event_sets_direction_outgoing():
    """Test that enqueue_event sets direction to outgoing."""
    with patch('app.repositories.webhook_events.create_event', new_callable=AsyncMock) as mock_create, \
         patch('app.repositories.webhook_events.mark_in_progress', new_callable=AsyncMock), \
         patch('app.repositories.webhook_events.get_event', new_callable=AsyncMock) as mock_get, \
         patch('app.services.webhook_monitor._attempt_event', new_callable=AsyncMock):
        
        mock_create.return_value = {'id': 999}
        mock_get.return_value = {'id': 999, 'status': 'succeeded', 'direction': 'outgoing'}
        
        result = await webhook_monitor.enqueue_event(
            name="Outgoing Webhook",
            target_url="https://api.example.com/webhook",
            payload={"data": "test"},
        )
        
        # Verify direction was set to outgoing
        mock_create.assert_called_once()
        call_kwargs = mock_create.call_args.kwargs
        assert call_kwargs['direction'] == 'outgoing'
        assert result['direction'] == 'outgoing'


@pytest.mark.asyncio
async def test_create_manual_event_sets_direction_outgoing():
    """Test that create_manual_event sets direction to outgoing."""
    with patch('app.repositories.webhook_events.create_event', new_callable=AsyncMock) as mock_create, \
         patch('app.repositories.webhook_events.mark_in_progress', new_callable=AsyncMock), \
         patch('app.repositories.webhook_events.get_event', new_callable=AsyncMock) as mock_get:
        
        mock_create.return_value = {'id': 888}
        mock_get.return_value = {'id': 888, 'status': 'in_progress', 'direction': 'outgoing'}
        
        result = await webhook_monitor.create_manual_event(
            name="Manual Outgoing",
            target_url="https://api.example.com/manual",
        )
        
        # Verify direction was set to outgoing
        mock_create.assert_called_once()
        call_kwargs = mock_create.call_args.kwargs
        assert call_kwargs['direction'] == 'outgoing'
        assert result['direction'] == 'outgoing'
