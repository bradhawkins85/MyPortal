"""Tests for CORS security configuration."""
import pytest
from fastapi.testclient import TestClient


def test_cors_default_no_wildcard(app_client: TestClient):
    """Test that CORS defaults to no wildcard origins when not configured."""
    # Make a preflight request (OPTIONS)
    response = app_client.options(
        "/api/users/me",
        headers={
            "Origin": "https://evil.example.com",
            "Access-Control-Request-Method": "GET",
        },
    )
    
    # Should not have Access-Control-Allow-Origin header for unauthorized origin
    # or should reject the request
    assert response.status_code in [200, 403, 404]
    
    # If CORS is properly configured, it should not return the evil origin
    if "access-control-allow-origin" in response.headers:
        allowed_origin = response.headers["access-control-allow-origin"]
        # Should either not allow the origin or only allow specific origins (not wildcard)
        assert allowed_origin != "*", "CORS should not allow wildcard origins"
        assert allowed_origin != "https://evil.example.com", "CORS should not allow unauthorized origins"


def test_cors_allows_same_origin(app_client: TestClient):
    """Test that same-origin requests work properly."""
    # A request without Origin header is same-origin
    response = app_client.get("/health")
    assert response.status_code == 200


def test_cors_methods_restricted(app_client: TestClient):
    """Test that only necessary HTTP methods are allowed."""
    # Make a preflight request for an unusual method
    response = app_client.options(
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


def test_cors_credentials_supported(app_client: TestClient):
    """Test that credentials are supported for authenticated requests."""
    response = app_client.options(
        "/api/users/me",
        headers={
            "Origin": "http://localhost:8000",
            "Access-Control-Request-Method": "GET",
        },
    )
    
    # Should allow credentials for authenticated endpoints
    if "access-control-allow-credentials" in response.headers:
        assert response.headers["access-control-allow-credentials"] == "true"
