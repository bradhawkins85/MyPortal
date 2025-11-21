"""Tests for Plausible tracking middleware."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI, Request, Response
from fastapi.testclient import TestClient
from starlette.middleware.base import BaseHTTPMiddleware

from app.security.plausible_tracking import PlausibleTrackingMiddleware
from app.security.session import SessionData


@pytest.fixture
def test_app():
    """Create a test FastAPI app with the middleware."""
    app = FastAPI()
    
    # Mock module settings function
    def mock_get_module_settings():
        return {
            "enabled": True,
            "settings": {
                "base_url": "https://plausible.example.com",
                "site_domain": "myportal.example.com",
                "api_key": "test-api-key",
                "pepper": "test-pepper",
                "send_pii": False,
                "track_pageviews": True,
            },
        }
    
    app.add_middleware(
        PlausibleTrackingMiddleware,
        exempt_paths=("/api", "/static", "/health"),
        get_module_settings=mock_get_module_settings,
    )
    
    @app.get("/")
    async def index():
        return {"message": "Home"}
    
    @app.get("/dashboard")
    async def dashboard():
        return {"message": "Dashboard"}
    
    @app.get("/api/test")
    async def api_test():
        return {"message": "API"}
    
    @app.get("/health")
    async def health():
        return {"status": "ok"}
    
    return app


@pytest.mark.asyncio
async def test_middleware_skips_unauthenticated_requests(test_app):
    """Test that middleware does not track unauthenticated requests."""
    client = TestClient(test_app)
    
    with patch("app.security.plausible_tracking.session_manager") as mock_session_manager:
        # No session
        mock_session_manager.load_session = AsyncMock(return_value=None)
        
        with patch("httpx.AsyncClient.post") as mock_post:
            response = client.get("/dashboard")
            
            assert response.status_code == 200
            # Should not send any events for unauthenticated users
            mock_post.assert_not_called()


@pytest.mark.asyncio
async def test_middleware_tracks_authenticated_pageviews(test_app):
    """Test that middleware tracks pageviews for authenticated users."""
    client = TestClient(test_app)
    
    # Mock session with authenticated user
    mock_session = SessionData(
        id="test-session-id",
        user_id=123,
        csrf_token="test-csrf",
    )
    
    with patch("app.security.plausible_tracking.session_manager") as mock_session_manager:
        mock_session_manager.load_session = AsyncMock(return_value=mock_session)
        
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_response = AsyncMock()
            mock_response.raise_for_status = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock()
            mock_client_class.return_value = mock_client
            
            response = client.get("/dashboard")
            
            assert response.status_code == 200
            # Should send event for authenticated user
            mock_client.post.assert_called_once()
            
            # Verify event data structure
            call_args = mock_client.post.call_args
            assert call_args[0][0] == "https://plausible.example.com/api/event"
            event_data = call_args[1]["json"]
            assert event_data["domain"] == "myportal.example.com"
            assert event_data["name"] == "pageview"
            assert "user_id" in event_data["props"]
            # Verify user ID is hashed (not raw user_id)
            assert event_data["props"]["user_id"].startswith("hash_")


@pytest.mark.asyncio
async def test_middleware_skips_exempt_paths(test_app):
    """Test that middleware skips exempt paths like /api and /static."""
    client = TestClient(test_app)
    
    mock_session = SessionData(
        id="test-session-id",
        user_id=123,
        csrf_token="test-csrf",
    )
    
    with patch("app.security.plausible_tracking.session_manager") as mock_session_manager:
        mock_session_manager.load_session = AsyncMock(return_value=mock_session)
        
        with patch("httpx.AsyncClient.post") as mock_post:
            # Test API endpoint
            response = client.get("/api/test")
            assert response.status_code == 200
            mock_post.assert_not_called()
            
            # Test health endpoint
            response = client.get("/health")
            assert response.status_code == 200
            mock_post.assert_not_called()


@pytest.mark.asyncio
async def test_middleware_skips_non_get_requests(test_app):
    """Test that middleware only tracks GET requests (pageviews)."""
    # Add a POST endpoint
    @test_app.post("/submit")
    async def submit():
        return {"message": "Submitted"}
    
    client = TestClient(test_app)
    
    mock_session = SessionData(
        id="test-session-id",
        user_id=123,
        csrf_token="test-csrf",
    )
    
    with patch("app.security.plausible_tracking.session_manager") as mock_session_manager:
        mock_session_manager.load_session = AsyncMock(return_value=mock_session)
        
        with patch("httpx.AsyncClient.post") as mock_post:
            response = client.post("/submit")
            assert response.status_code == 200
            # Should not track POST requests
            mock_post.assert_not_called()


@pytest.mark.asyncio
async def test_middleware_skips_error_responses(test_app):
    """Test that middleware does not track failed requests."""
    @test_app.get("/error")
    async def error():
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Not found")
    
    client = TestClient(test_app)
    
    mock_session = SessionData(
        id="test-session-id",
        user_id=123,
        csrf_token="test-csrf",
    )
    
    with patch("app.security.plausible_tracking.session_manager") as mock_session_manager:
        mock_session_manager.load_session = AsyncMock(return_value=mock_session)
        
        with patch("httpx.AsyncClient.post") as mock_post:
            response = client.get("/error")
            assert response.status_code == 404
            # Should not track error responses
            mock_post.assert_not_called()


@pytest.mark.asyncio
async def test_middleware_when_module_disabled(test_app):
    """Test that middleware does not track when module is disabled."""
    # Create app with disabled module
    app = FastAPI()
    
    def mock_get_module_settings_disabled():
        return {
            "enabled": False,  # Module disabled
            "settings": {},
        }
    
    app.add_middleware(
        PlausibleTrackingMiddleware,
        exempt_paths=("/api", "/static"),
        get_module_settings=mock_get_module_settings_disabled,
    )
    
    @app.get("/")
    async def index():
        return {"message": "Home"}
    
    client = TestClient(app)
    
    mock_session = SessionData(
        id="test-session-id",
        user_id=123,
        csrf_token="test-csrf",
    )
    
    with patch("app.security.plausible_tracking.session_manager") as mock_session_manager:
        mock_session_manager.load_session = AsyncMock(return_value=mock_session)
        
        with patch("httpx.AsyncClient.post") as mock_post:
            response = client.get("/")
            assert response.status_code == 200
            # Should not track when module disabled
            mock_post.assert_not_called()


@pytest.mark.asyncio
async def test_middleware_handles_missing_configuration(test_app):
    """Test that middleware handles missing configuration gracefully."""
    app = FastAPI()
    
    def mock_get_module_settings_incomplete():
        return {
            "enabled": True,
            "settings": {
                # Missing base_url and site_domain
                "api_key": "test-key",
            },
        }
    
    app.add_middleware(
        PlausibleTrackingMiddleware,
        exempt_paths=("/api",),
        get_module_settings=mock_get_module_settings_incomplete,
    )
    
    @app.get("/")
    async def index():
        return {"message": "Home"}
    
    client = TestClient(app)
    
    mock_session = SessionData(
        id="test-session-id",
        user_id=123,
        csrf_token="test-csrf",
    )
    
    with patch("app.security.plausible_tracking.session_manager") as mock_session_manager:
        mock_session_manager.load_session = AsyncMock(return_value=mock_session)
        
        with patch("httpx.AsyncClient.post") as mock_post:
            response = client.get("/")
            assert response.status_code == 200
            # Should not track when configuration is incomplete
            mock_post.assert_not_called()


@pytest.mark.asyncio
async def test_middleware_sends_pii_when_configured(test_app):
    """Test that middleware sends raw user ID when send_pii is enabled."""
    app = FastAPI()
    
    def mock_get_module_settings_with_pii():
        return {
            "enabled": True,
            "settings": {
                "base_url": "https://plausible.example.com",
                "site_domain": "myportal.example.com",
                "api_key": "test-api-key",
                "pepper": "test-pepper",
                "send_pii": True,  # PII enabled
                "track_pageviews": True,
            },
        }
    
    app.add_middleware(
        PlausibleTrackingMiddleware,
        exempt_paths=("/api",),
        get_module_settings=mock_get_module_settings_with_pii,
    )
    
    @app.get("/")
    async def index():
        return {"message": "Home"}
    
    client = TestClient(app)
    
    mock_session = SessionData(
        id="test-session-id",
        user_id=456,
        csrf_token="test-csrf",
    )
    
    with patch("app.security.plausible_tracking.session_manager") as mock_session_manager:
        mock_session_manager.load_session = AsyncMock(return_value=mock_session)
        
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            mock_response = AsyncMock()
            mock_response.raise_for_status = AsyncMock()
            mock_client.post = AsyncMock(return_value=mock_response)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock()
            mock_client_class.return_value = mock_client
            
            response = client.get("/")
            
            assert response.status_code == 200
            mock_client.post.assert_called_once()
            
            # Verify raw user ID is sent (not hashed)
            call_args = mock_client.post.call_args
            event_data = call_args[1]["json"]
            assert event_data["props"]["user_id"] == "user_456"
            # Should NOT be hashed when PII is enabled
            assert not event_data["props"]["user_id"].startswith("hash_")


@pytest.mark.asyncio
async def test_middleware_continues_on_plausible_error(test_app):
    """Test that middleware doesn't fail the request if Plausible is down."""
    client = TestClient(test_app)
    
    mock_session = SessionData(
        id="test-session-id",
        user_id=123,
        csrf_token="test-csrf",
    )
    
    with patch("app.security.plausible_tracking.session_manager") as mock_session_manager:
        mock_session_manager.load_session = AsyncMock(return_value=mock_session)
        
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = AsyncMock()
            # Simulate Plausible API error
            mock_client.post = AsyncMock(side_effect=Exception("Connection failed"))
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock()
            mock_client_class.return_value = mock_client
            
            # Request should still succeed even if tracking fails
            response = client.get("/dashboard")
            assert response.status_code == 200
            assert response.json() == {"message": "Dashboard"}


def test_hash_user_id_with_pepper():
    """Test that user ID hashing produces consistent results."""
    from app.security.plausible_tracking import PlausibleTrackingMiddleware
    
    middleware = PlausibleTrackingMiddleware(None)
    
    # Same user ID and pepper should produce same hash
    hash1 = middleware._hash_user_id(123, "secret-pepper")
    hash2 = middleware._hash_user_id(123, "secret-pepper")
    assert hash1 == hash2
    
    # Different user IDs should produce different hashes
    hash3 = middleware._hash_user_id(456, "secret-pepper")
    assert hash1 != hash3
    
    # Different peppers should produce different hashes
    hash4 = middleware._hash_user_id(123, "different-pepper")
    assert hash1 != hash4
    
    # Verify hash format
    assert hash1.startswith("hash_")
    assert len(hash1) > 5  # Should have content after prefix


def test_hash_user_id_with_default_pepper():
    """Test that user ID hashing works with default pepper."""
    from app.security.plausible_tracking import PlausibleTrackingMiddleware
    
    middleware = PlausibleTrackingMiddleware(None)
    
    # Should use default pepper when none provided
    hash1 = middleware._hash_user_id(123, "")
    assert hash1.startswith("hash_")
    assert len(hash1) > 5
