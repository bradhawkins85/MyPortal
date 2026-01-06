"""Test CSP form-action directive configuration."""
import pytest
from app.security.security_headers import SecurityHeadersMiddleware


@pytest.mark.asyncio
async def test_http_url_validation():
    """Test that HTTP URLs are accepted in CSP validation."""
    middleware = SecurityHeadersMiddleware(None)
    
    # Test HTTPS URLs
    assert middleware._is_valid_csp_source("https://portal.hawkinsit.au")
    assert middleware._is_valid_csp_source("https://portal.hawkinsit.au:443")
    assert middleware._is_valid_csp_source("https://portal.hawkinsit.au:8000")
    
    # Test HTTP URLs (should now be accepted)
    assert middleware._is_valid_csp_source("http://portal.hawkinsit.au")
    assert middleware._is_valid_csp_source("http://localhost:8000")
    assert middleware._is_valid_csp_source("http://127.0.0.1:8000")
    
    # Test invalid URLs
    assert not middleware._is_valid_csp_source("ftp://portal.hawkinsit.au")
    assert not middleware._is_valid_csp_source("portal.hawkinsit.au")
    assert not middleware._is_valid_csp_source("https://portal hawkinsit.au")
    assert not middleware._is_valid_csp_source("https://portal.hawkinsit.au; script-src 'unsafe-eval'")
    assert not middleware._is_valid_csp_source("")
    assert not middleware._is_valid_csp_source(None)


@pytest.mark.asyncio
async def test_csp_includes_portal_url(monkeypatch):
    """Test that portal_url is included in CSP form-action directive."""
    from pydantic import AnyHttpUrl
    
    # Mock settings with a portal URL
    class MockSettings:
        portal_url = AnyHttpUrl("https://portal.hawkinsit.au")
        enable_hsts = False
    
    middleware = SecurityHeadersMiddleware(None)
    middleware._settings = MockSettings()
    
    # Create a mock request and response
    from starlette.responses import Response
    from starlette.datastructures import URL
    
    class MockRequest:
        url = URL("https://portal.hawkinsit.au/cart")
        
    async def call_next(request):
        return Response(content="test", status_code=200)
    
    response = await middleware.dispatch(MockRequest(), call_next)
    
    # Check that the CSP header includes both 'self' and the portal URL
    csp_header = response.headers.get("Content-Security-Policy", "")
    assert "form-action 'self' https://portal.hawkinsit.au" in csp_header


@pytest.mark.asyncio
async def test_csp_with_http_portal_url(monkeypatch):
    """Test that HTTP portal URLs are included in CSP."""
    from pydantic import AnyHttpUrl
    
    # Mock settings with an HTTP portal URL (for development)
    class MockSettings:
        portal_url = AnyHttpUrl("http://localhost:8000")
        enable_hsts = False
    
    middleware = SecurityHeadersMiddleware(None)
    middleware._settings = MockSettings()
    
    # Create a mock request and response
    from starlette.responses import Response
    from starlette.datastructures import URL
    
    class MockRequest:
        url = URL("http://localhost:8000/cart")
        
    async def call_next(request):
        return Response(content="test", status_code=200)
    
    response = await middleware.dispatch(MockRequest(), call_next)
    
    # Check that the CSP header includes both 'self' and the HTTP portal URL
    csp_header = response.headers.get("Content-Security-Policy", "")
    assert "form-action 'self' http://localhost:8000" in csp_header
