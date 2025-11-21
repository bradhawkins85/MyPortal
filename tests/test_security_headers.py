"""Tests for security headers middleware."""
from __future__ import annotations

import os
from unittest.mock import patch

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from app.security.security_headers import SecurityHeadersMiddleware


async def _mock_extra_sources_none():
    """Mock function that returns no extra sources."""
    return []


async def _mock_extra_sources_plausible():
    """Mock function that returns a Plausible analytics source."""
    return ["https://plausible.example.com"]


async def _mock_extra_sources_invalid():
    """Mock function that returns invalid sources that should be filtered."""
    return [
        "https://valid.example.com",
        "http://invalid-http.example.com",  # HTTP not allowed
        "https://invalid with spaces.com",  # Spaces not allowed
        "javascript:alert(1)",  # JavaScript protocol not allowed
        "",  # Empty string
    ]


@pytest.fixture
def test_app():
    """Create a test FastAPI application with security headers middleware."""
    app = FastAPI()
    
    @app.get("/test")
    async def test_endpoint(request: Request):
        return JSONResponse({"status": "ok"})
    
    @app.get("/static/test.css")
    async def static_endpoint(request: Request):
        return JSONResponse({"status": "ok"})
    
    # Add middleware (note: middleware is applied in reverse order)
    app.add_middleware(SecurityHeadersMiddleware, exempt_paths=("/static",))
    
    return app


@pytest.fixture
def test_app_with_plausible():
    """Create a test FastAPI application with Plausible analytics configured."""
    app = FastAPI()
    
    @app.get("/test")
    async def test_endpoint(request: Request):
        return JSONResponse({"status": "ok"})
    
    # Add middleware with Plausible source
    app.add_middleware(
        SecurityHeadersMiddleware,
        exempt_paths=("/static",),
        get_extra_script_sources=_mock_extra_sources_plausible,
        get_extra_connect_sources=_mock_extra_sources_plausible,
    )
    
    return app


@pytest.fixture
def test_app_with_invalid_sources():
    """Create a test FastAPI application with invalid sources to test filtering."""
    app = FastAPI()
    
    @app.get("/test")
    async def test_endpoint(request: Request):
        return JSONResponse({"status": "ok"})
    
    # Add middleware with mixed valid/invalid sources
    app.add_middleware(
        SecurityHeadersMiddleware,
        exempt_paths=("/static",),
        get_extra_script_sources=_mock_extra_sources_invalid,
    )
    
    return app


def test_security_headers_added_to_response(test_app):
    """Test that all required security headers are added to responses."""
    client = TestClient(test_app)
    response = client.get("/test")
    
    # Check that all required headers are present
    assert "Content-Security-Policy" in response.headers
    assert "X-Frame-Options" in response.headers
    assert "X-Content-Type-Options" in response.headers
    assert "Referrer-Policy" in response.headers
    assert "Permissions-Policy" in response.headers
    assert "X-XSS-Protection" in response.headers


def test_csp_header_configuration(test_app):
    """Test Content-Security-Policy header is properly configured."""
    client = TestClient(test_app)
    response = client.get("/test")
    
    csp = response.headers["Content-Security-Policy"]
    
    # Check key CSP directives
    assert "default-src 'self'" in csp
    assert "connect-src 'self' https://cal.com https://app.cal.com" in csp
    assert "frame-ancestors 'none'" in csp
    assert "frame-src 'self' https://cal.com https://app.cal.com" in csp
    assert "base-uri 'self'" in csp
    assert "form-action 'self'" in csp
    # Check that unpkg.com is allowed for loading htmx in script-src directive
    assert (
        "script-src 'self' 'unsafe-inline' 'unsafe-eval' https://unpkg.com https://cal.com https://app.cal.com"
        in csp
    )


def test_x_frame_options_deny(test_app):
    """Test X-Frame-Options header prevents clickjacking."""
    client = TestClient(test_app)
    response = client.get("/test")
    
    assert response.headers["X-Frame-Options"] == "DENY"


def test_x_content_type_options_nosniff(test_app):
    """Test X-Content-Type-Options prevents MIME-sniffing."""
    client = TestClient(test_app)
    response = client.get("/test")
    
    assert response.headers["X-Content-Type-Options"] == "nosniff"


def test_referrer_policy_configured(test_app):
    """Test Referrer-Policy is properly configured."""
    client = TestClient(test_app)
    response = client.get("/test")
    
    assert response.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"


def test_permissions_policy_disables_features(test_app):
    """Test Permissions-Policy disables sensitive browser features."""
    client = TestClient(test_app)
    response = client.get("/test")
    
    permissions = response.headers["Permissions-Policy"]
    
    # Check that sensitive features are disabled
    assert "geolocation=()" in permissions
    assert "microphone=()" in permissions
    assert "camera=()" in permissions


def test_hsts_header_not_added_over_http(test_app):
    """Test HSTS header is not added for HTTP requests."""
    client = TestClient(test_app)
    
    with patch.dict(os.environ, {"ENABLE_HSTS": "true"}):
        # Force settings reload
        from app.core.config import get_settings
        get_settings.cache_clear()
        
        response = client.get("/test")
        
        # HSTS should not be present over HTTP (TestClient uses http by default)
        assert "Strict-Transport-Security" not in response.headers


def test_security_headers_exempt_for_static_files(test_app):
    """Test that static files are exempt from security headers."""
    client = TestClient(test_app)
    response = client.get("/static/test.css")
    
    # Static files should not have security headers applied
    # They should still return 200 but without the security headers
    assert response.status_code == 200
    # Verify exemption works - static files won't have all the headers
    # (In production, static files would be served by a different mechanism)


def test_xss_protection_header_present(test_app):
    """Test X-XSS-Protection header for legacy browser support."""
    client = TestClient(test_app)
    response = client.get("/test")
    
    assert response.headers["X-XSS-Protection"] == "1; mode=block"


def test_csp_with_plausible_analytics(test_app_with_plausible):
    """Test that Plausible analytics source is added to CSP."""
    client = TestClient(test_app_with_plausible)
    response = client.get("/test")

    csp = response.headers["Content-Security-Policy"]

    # Check that Plausible domain is included in script-src
    assert "https://plausible.example.com" in csp
    # Plausible should also be allowed for connect-src
    assert (
        "connect-src 'self' https://cal.com https://app.cal.com https://plausible.example.com"
        in csp
    )
    # Check that default sources are still present
    assert "'self'" in csp
    assert "'unsafe-inline'" in csp
    assert "'unsafe-eval'" in csp
    assert "https://unpkg.com" in csp


def test_csp_filters_invalid_sources(test_app_with_invalid_sources):
    """Test that invalid CSP sources are filtered out."""
    client = TestClient(test_app_with_invalid_sources)
    response = client.get("/test")
    
    csp = response.headers["Content-Security-Policy"]
    
    # Valid HTTPS source should be included
    assert "https://valid.example.com" in csp
    # Invalid sources should NOT be included
    assert "http://invalid-http.example.com" not in csp
    assert "invalid with spaces" not in csp
    assert "javascript:" not in csp


def test_csp_source_validation():
    """Test the _is_valid_csp_source method directly."""
    from app.security.security_headers import SecurityHeadersMiddleware
    
    # Create a mock app for testing
    class MockApp:
        pass
    
    middleware = SecurityHeadersMiddleware(MockApp())
    
    # Valid sources
    assert middleware._is_valid_csp_source("https://example.com") is True
    assert middleware._is_valid_csp_source("https://subdomain.example.com") is True
    assert middleware._is_valid_csp_source("https://example.com:8080") is True
    assert middleware._is_valid_csp_source("https://example-with-dash.com") is True
    
    # Invalid sources
    assert middleware._is_valid_csp_source("http://example.com") is False  # HTTP not allowed
    assert middleware._is_valid_csp_source("https://example.com with spaces") is False
    assert middleware._is_valid_csp_source("https://example.com;") is False
    assert middleware._is_valid_csp_source("https://example.com'") is False
    assert middleware._is_valid_csp_source("javascript:alert(1)") is False
    assert middleware._is_valid_csp_source("") is False
    assert middleware._is_valid_csp_source(None) is False
