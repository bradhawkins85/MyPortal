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


# ---------------------------------------------------------------------------
# _normalize_source_url
# ---------------------------------------------------------------------------

def test_normalize_source_url_upgrades_public_http_to_https():
    from app.services.webhook_monitor import _normalize_source_url
    assert _normalize_source_url(
        "http://portal.hawkinsit.au/api/integration-modules/trello/webhook"
    ) == "https://portal.hawkinsit.au/api/integration-modules/trello/webhook"


def test_normalize_source_url_leaves_https_unchanged():
    from app.services.webhook_monitor import _normalize_source_url
    url = "https://portal.hawkinsit.au/api/integration-modules/trello/webhook"
    assert _normalize_source_url(url) == url


def test_normalize_source_url_preserves_localhost_http():
    from app.services.webhook_monitor import _normalize_source_url
    url = "http://localhost:8000/api/integration-modules/trello/webhook"
    assert _normalize_source_url(url) == url


def test_normalize_source_url_preserves_127_0_0_1():
    from app.services.webhook_monitor import _normalize_source_url
    url = "http://127.0.0.1:8000/webhook"
    assert _normalize_source_url(url) == url


def test_normalize_source_url_preserves_dot_local():
    from app.services.webhook_monitor import _normalize_source_url
    url = "http://myhost.local/webhook"
    assert _normalize_source_url(url) == url


def test_normalize_source_url_preserves_path_and_query():
    from app.services.webhook_monitor import _normalize_source_url
    assert _normalize_source_url(
        "http://example.com/path?foo=bar"
    ) == "https://example.com/path?foo=bar"


def test_normalize_source_url_strips_default_http_port_80():
    from app.services.webhook_monitor import _normalize_source_url
    # Port 80 is the HTTP default and should be dropped in the HTTPS URL
    assert _normalize_source_url(
        "http://example.com:80/path"
    ) == "https://example.com/path"


def test_normalize_source_url_preserves_non_standard_port():
    from app.services.webhook_monitor import _normalize_source_url
    # Non-standard ports are preserved since we don't know the HTTPS equivalent
    assert _normalize_source_url(
        "http://example.com:8080/path"
    ) == "https://example.com:8080/path"


# ---------------------------------------------------------------------------
# log_incoming_webhook stores normalised (https) source_url for public hosts
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_log_incoming_webhook_normalises_http_source_url_for_public_host():
    """http:// source URLs for public hosts must be stored as https:// so that
    manual retries don't hit the proxy's HTTP->HTTPS redirect (308/301)."""
    with patch('app.repositories.webhook_events.create_event', new_callable=AsyncMock) as mock_create, \
         patch('app.repositories.webhook_events.record_attempt', new_callable=AsyncMock), \
         patch('app.repositories.webhook_events.mark_event_completed', new_callable=AsyncMock), \
         patch('app.repositories.webhook_events.get_event', new_callable=AsyncMock) as mock_get:

        mock_create.return_value = {'id': 1}
        mock_get.return_value = {'id': 1, 'status': 'succeeded'}

        await webhook_monitor.log_incoming_webhook(
            name="Trello Webhook",
            source_url="http://portal.hawkinsit.au/api/integration-modules/trello/webhook",
            response_status=200,
        )

        call_kwargs = mock_create.call_args.kwargs
        assert call_kwargs['target_url'] == "https://portal.hawkinsit.au/api/integration-modules/trello/webhook"
        assert call_kwargs['source_url'] == "https://portal.hawkinsit.au/api/integration-modules/trello/webhook"


@pytest.mark.asyncio
async def test_log_incoming_webhook_preserves_http_for_localhost():
    """http:// source URLs for localhost must not be upgraded -- local dev envs
    may not have HTTPS configured."""
    with patch('app.repositories.webhook_events.create_event', new_callable=AsyncMock) as mock_create, \
         patch('app.repositories.webhook_events.record_attempt', new_callable=AsyncMock), \
         patch('app.repositories.webhook_events.mark_event_completed', new_callable=AsyncMock), \
         patch('app.repositories.webhook_events.get_event', new_callable=AsyncMock) as mock_get:

        mock_create.return_value = {'id': 2}
        mock_get.return_value = {'id': 2, 'status': 'succeeded'}

        await webhook_monitor.log_incoming_webhook(
            name="Local Webhook",
            source_url="http://localhost:8000/api/integration-modules/trello/webhook",
            response_status=200,
        )

        call_kwargs = mock_create.call_args.kwargs
        assert call_kwargs['target_url'] == "http://localhost:8000/api/integration-modules/trello/webhook"
        assert call_kwargs['source_url'] == "http://localhost:8000/api/integration-modules/trello/webhook"


# ---------------------------------------------------------------------------
# _attempt_event follows 3xx redirects
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_attempt_event_follows_308_redirect():
    """Verify that `_attempt_event` constructs the httpx client with
    ``follow_redirects=True`` so that proxy 308/301 redirects (e.g. Traefik's
    HTTP→HTTPS redirect) are followed transparently.

    We mock httpx at the constructor level to assert the configuration flag,
    rather than simulating an actual HTTP redirect chain.  The actual redirect-
    following behaviour is an httpx internal concern; what we own is the flag.
    """
    import httpx
    from unittest.mock import MagicMock

    event = {
        "id": 99,
        "target_url": "http://portal.hawkinsit.au/api/integration-modules/trello/webhook",
        "attempt_count": 0,
        "max_attempts": 3,
        "backoff_seconds": 300,
        "headers": {},
        "payload": {"action": {"type": "createCard"}},
    }

    final_response = MagicMock(spec=httpx.Response)
    final_response.status_code = 200
    final_response.text = "ok"
    final_response.headers = {}

    async def fake_post(url, **kwargs):
        return final_response

    with patch('app.services.webhook_monitor.webhook_repo') as mock_repo, \
         patch('app.services.webhook_monitor.httpx.AsyncClient') as mock_client_cls:

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.post = fake_post
        mock_client_cls.return_value = mock_client

        mock_repo.record_attempt = AsyncMock()
        mock_repo.mark_event_completed = AsyncMock()
        mock_repo.mark_event_failed = AsyncMock()
        mock_repo.schedule_retry = AsyncMock()

        await webhook_monitor._attempt_event(event)

    # The client must be created with follow_redirects=True so that any
    # 301/308 redirect from the proxy is followed without failing delivery.
    _, client_kwargs = mock_client_cls.call_args
    assert client_kwargs.get('follow_redirects') is True

    # Delivery was recorded as succeeded
    mock_repo.mark_event_completed.assert_awaited_once()
