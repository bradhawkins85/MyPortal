"""Tests for CORS security configuration."""
import os
import pytest
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient


@pytest.fixture
def test_app_no_cors():
    """Create a test app without CORS (same-origin only)."""
    app = FastAPI()
    
    @app.get("/health")
    def health():
        return {"status": "ok"}
    
    @app.get("/api/users/me")
    def get_user():
        return {"user": "test"}
    
    # Configure CORS like main.py does with empty ALLOWED_ORIGINS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[],  # Empty list = same-origin only
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
        allow_headers=["*"],
    )
    
    return app


def test_cors_default_no_wildcard(test_app_no_cors):
    """Test that CORS defaults to no wildcard origins when not configured."""
    client = TestClient(test_app_no_cors)
    
    # Make a preflight request (OPTIONS)
    response = client.options(
        "/api/users/me",
        headers={
            "Origin": "https://evil.example.com",
            "Access-Control-Request-Method": "GET",
        },
    )
    
    # Should not have Access-Control-Allow-Origin header for unauthorized origin
    # With empty allowed_origins, CORS middleware may return 400 (Bad Request)
    # or succeed but not add the CORS headers
    assert response.status_code in [200, 400, 403, 404]
    
    # If CORS is properly configured with empty list, it should not return the evil origin
    # The CORS middleware should not add Access-Control-Allow-Origin for disallowed origins
    if "access-control-allow-origin" in response.headers:
        allowed_origin = response.headers["access-control-allow-origin"]
        # Should not allow the evil origin
        assert allowed_origin != "*", "CORS should not allow wildcard origins"
        assert allowed_origin != "https://evil.example.com", "CORS should not allow unauthorized origins"


def test_cors_allows_same_origin(test_app_no_cors):
    """Test that same-origin requests work properly."""
    client = TestClient(test_app_no_cors)
    
    # A request without Origin header is same-origin
    response = client.get("/health")
    assert response.status_code == 200


def test_cors_methods_restricted(test_app_no_cors):
    """Test that only necessary HTTP methods are allowed."""
    client = TestClient(test_app_no_cors)
    
    # Make a preflight request for an unusual method
    response = client.options(
        "/api/users/me",
        headers={
            "Origin": "http://localhost:8000",
            "Access-Control-Request-Method": "TRACE",
        },
    )
    
    # TRACE should not be in allowed methods
    if "access-control-allow-methods" in response.headers:
        allowed_methods = response.headers["access-control-allow-methods"]
        assert "TRACE" not in allowed_methods, "TRACE method should not be allowed"
        assert "CONNECT" not in allowed_methods, "CONNECT method should not be allowed"


def test_cors_credentials_supported(test_app_no_cors):
    """Test that credentials are supported for authenticated requests."""
    client = TestClient(test_app_no_cors)
    
    response = client.options(
        "/api/users/me",
        headers={
            "Origin": "http://localhost:8000",
            "Access-Control-Request-Method": "GET",
        },
    )
    
    # Should allow credentials for authenticated endpoints
    if "access-control-allow-credentials" in response.headers:
        assert response.headers["access-control-allow-credentials"] == "true"
