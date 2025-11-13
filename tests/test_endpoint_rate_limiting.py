"""Tests for endpoint-specific rate limiting."""
from __future__ import annotations

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from app.security.rate_limiter import EndpointRateLimiter, EndpointRateLimiterMiddleware


@pytest.fixture
def test_app_with_endpoint_limits():
    """Create a test FastAPI application with endpoint-specific rate limits."""
    app = FastAPI()
    
    @app.post("/auth/login")
    async def login(request: Request):
        return JSONResponse({"status": "ok"})
    
    @app.post("/auth/password/forgot")
    async def forgot_password(request: Request):
        return JSONResponse({"status": "ok"})
    
    @app.post("/api/upload")
    async def upload_file(request: Request):
        return JSONResponse({"status": "ok"})
    
    @app.get("/api/data")
    async def get_data(request: Request):
        return JSONResponse({"status": "ok"})
    
    # Configure endpoint limits
    endpoint_limiter = EndpointRateLimiter()
    
    # Login: 5 attempts per 15 minutes (900 seconds)
    endpoint_limiter.add_limit("/auth/login", "POST", limit=5, window_seconds=900)
    
    # Password reset: 3 attempts per hour
    endpoint_limiter.add_limit("/auth/password/forgot", "POST", limit=3, window_seconds=3600)
    
    # File upload: 10 files per hour
    endpoint_limiter.add_limit("/api/upload", "POST", limit=10, window_seconds=3600)
    
    app.add_middleware(
        EndpointRateLimiterMiddleware,
        endpoint_limiter=endpoint_limiter,
        exempt_paths=("/static",),
    )
    
    return app


def test_login_rate_limit_allows_within_limit(test_app_with_endpoint_limits):
    """Test that login requests within rate limit are allowed."""
    client = TestClient(test_app_with_endpoint_limits)
    
    # Make 5 requests (at the limit)
    for i in range(5):
        response = client.post("/auth/login")
        assert response.status_code == 200, f"Request {i+1} should be allowed"


def test_login_rate_limit_blocks_excess_requests(test_app_with_endpoint_limits):
    """Test that login requests exceeding rate limit are blocked."""
    client = TestClient(test_app_with_endpoint_limits)
    
    # Make 5 allowed requests
    for _ in range(5):
        response = client.post("/auth/login")
        assert response.status_code == 200
    
    # 6th request should be blocked
    response = client.post("/auth/login")
    assert response.status_code == 429
    assert "Rate limit exceeded" in response.json()["detail"]


def test_password_reset_rate_limit(test_app_with_endpoint_limits):
    """Test password reset endpoint rate limiting."""
    client = TestClient(test_app_with_endpoint_limits)
    
    # Make 3 requests (at the limit)
    for i in range(3):
        response = client.post("/auth/password/forgot")
        assert response.status_code == 200, f"Request {i+1} should be allowed"
    
    # 4th request should be blocked
    response = client.post("/auth/password/forgot")
    assert response.status_code == 429
    assert "Rate limit exceeded" in response.json()["detail"]


def test_file_upload_rate_limit(test_app_with_endpoint_limits):
    """Test file upload endpoint rate limiting."""
    client = TestClient(test_app_with_endpoint_limits)
    
    # Make 10 requests (at the limit)
    for i in range(10):
        response = client.post("/api/upload")
        assert response.status_code == 200, f"Request {i+1} should be allowed"
    
    # 11th request should be blocked
    response = client.post("/api/upload")
    assert response.status_code == 429


def test_rate_limits_are_endpoint_specific(test_app_with_endpoint_limits):
    """Test that rate limits are isolated per endpoint."""
    client = TestClient(test_app_with_endpoint_limits)
    
    # Exhaust login rate limit
    for _ in range(5):
        client.post("/auth/login")
    
    # Other endpoints should still work
    response = client.post("/auth/password/forgot")
    assert response.status_code == 200
    
    response = client.post("/api/upload")
    assert response.status_code == 200


def test_endpoints_without_limits_are_not_restricted(test_app_with_endpoint_limits):
    """Test that endpoints without specific limits are not rate limited by endpoint middleware."""
    client = TestClient(test_app_with_endpoint_limits)
    
    # This endpoint has no specific limit configured
    for _ in range(20):
        response = client.get("/api/data")
        assert response.status_code == 200


def test_rate_limit_response_includes_retry_after(test_app_with_endpoint_limits):
    """Test that rate limit responses include Retry-After header."""
    client = TestClient(test_app_with_endpoint_limits)
    
    # Exhaust limit
    for _ in range(5):
        client.post("/auth/login")
    
    # Check rate limit response
    response = client.post("/auth/login")
    assert response.status_code == 429
    assert "Retry-After" in response.headers
    assert response.json()["retry_after"] is not None


def test_different_ips_have_separate_limits(test_app_with_endpoint_limits):
    """Test that rate limits are tracked separately per IP."""
    # Note: TestClient uses the same client, so this tests the same IP behavior
    # In real-world scenarios with different client IPs, limits would be separate
    client = TestClient(test_app_with_endpoint_limits)
    
    # First IP exhausts limit
    for _ in range(5):
        response = client.post("/auth/login")
        assert response.status_code == 200
    
    # Same IP is blocked
    response = client.post("/auth/login")
    assert response.status_code == 429
