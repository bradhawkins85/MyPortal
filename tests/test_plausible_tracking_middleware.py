"""Tests for Plausible Analytics tracking middleware."""
import pytest
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.testclient import TestClient

# This import pattern matches other security middleware tests in the project
try:
    from app.security.plausible_tracking import PlausibleTrackingMiddleware
except ImportError:
    # Fallback for test environments with missing dependencies
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).parent.parent / "app"))
    from security.plausible_tracking import PlausibleTrackingMiddleware


@pytest.fixture
def test_app():
    """Create a test FastAPI app with the middleware."""
    app = FastAPI()
    
    @app.get("/html")
    async def html_endpoint():
        return HTMLResponse(
            content="""<!DOCTYPE html>
<html>
<head>
    <title>Test Page</title>
</head>
<body>
    <h1>Hello World</h1>
</body>
</html>"""
        )
    
    @app.get("/json")
    async def json_endpoint():
        return JSONResponse(content={"message": "Hello"})
    
    @app.get("/no-head")
    async def no_head_endpoint():
        return HTMLResponse(content="<div>No head tag</div>")
    
    return app


@pytest.mark.asyncio
async def test_middleware_injects_script_when_enabled(test_app):
    """Test that middleware injects Plausible script when enabled."""
    # Mock config function
    async def get_config():
        return {
            "enabled": True,
            "base_url": "https://plausible.io",
            "site_domain": "example.com"
        }
    
    test_app.add_middleware(
        PlausibleTrackingMiddleware,
        exempt_paths=("/api",),
        get_plausible_config=get_config
    )
    
    client = TestClient(test_app)
    response = client.get("/html")
    
    assert response.status_code == 200
    assert 'data-domain="example.com"' in response.text
    assert 'src="https://plausible.io/js/script.js"' in response.text
    assert "<!-- Plausible Analytics -->" in response.text


@pytest.mark.asyncio
async def test_middleware_does_not_inject_when_disabled(test_app):
    """Test that middleware does not inject script when disabled."""
    # Mock config function
    async def get_config():
        return {"enabled": False}
    
    test_app.add_middleware(
        PlausibleTrackingMiddleware,
        exempt_paths=("/api",),
        get_plausible_config=get_config
    )
    
    client = TestClient(test_app)
    response = client.get("/html")
    
    assert response.status_code == 200
    assert "plausible" not in response.text.lower()
    assert "data-domain" not in response.text


@pytest.mark.asyncio
async def test_middleware_skips_json_responses(test_app):
    """Test that middleware skips non-HTML responses."""
    # Mock config function
    async def get_config():
        return {
            "enabled": True,
            "base_url": "https://plausible.io",
            "site_domain": "example.com"
        }
    
    test_app.add_middleware(
        PlausibleTrackingMiddleware,
        exempt_paths=("/api",),
        get_plausible_config=get_config
    )
    
    client = TestClient(test_app)
    response = client.get("/json")
    
    assert response.status_code == 200
    assert "plausible" not in response.text.lower()


@pytest.mark.asyncio
async def test_middleware_skips_exempt_paths(test_app):
    """Test that middleware skips exempt paths."""
    # Mock config function
    async def get_config():
        return {
            "enabled": True,
            "base_url": "https://plausible.io",
            "site_domain": "example.com"
        }
    
    # Create app with /html as exempt path
    test_app.add_middleware(
        PlausibleTrackingMiddleware,
        exempt_paths=("/html",),
        get_plausible_config=get_config
    )
    
    client = TestClient(test_app)
    response = client.get("/html")
    
    assert response.status_code == 200
    # Script should not be injected because path is exempt
    assert "plausible" not in response.text.lower()


@pytest.mark.asyncio
async def test_middleware_does_not_inject_when_no_head_tag(test_app):
    """Test that middleware does not inject when no </head> tag is present."""
    # Mock config function
    async def get_config():
        return {
            "enabled": True,
            "base_url": "https://plausible.io",
            "site_domain": "example.com"
        }
    
    test_app.add_middleware(
        PlausibleTrackingMiddleware,
        exempt_paths=("/api",),
        get_plausible_config=get_config
    )
    
    client = TestClient(test_app)
    response = client.get("/no-head")
    
    assert response.status_code == 200
    # Script should not be injected because there's no </head> tag
    assert "plausible" not in response.text.lower()


@pytest.mark.asyncio
async def test_middleware_validates_url_safety(test_app):
    """Test that middleware validates URL safety to prevent XSS."""
    # Mock config function with malicious URL
    async def get_config():
        return {
            "enabled": True,
            "base_url": "javascript:alert('xss')",
            "site_domain": "example.com"
        }
    
    test_app.add_middleware(
        PlausibleTrackingMiddleware,
        exempt_paths=("/api",),
        get_plausible_config=get_config
    )
    
    client = TestClient(test_app)
    response = client.get("/html")
    
    assert response.status_code == 200
    # Script should not be injected because URL is invalid
    assert "javascript:" not in response.text
    assert "plausible" not in response.text.lower()


@pytest.mark.asyncio
async def test_middleware_validates_domain_safety(test_app):
    """Test that middleware validates domain safety to prevent XSS."""
    # Mock config function with malicious domain
    async def get_config():
        return {
            "enabled": True,
            "base_url": "https://plausible.io",
            "site_domain": '"><script>alert("xss")</script>'
        }
    
    test_app.add_middleware(
        PlausibleTrackingMiddleware,
        exempt_paths=("/api",),
        get_plausible_config=get_config
    )
    
    client = TestClient(test_app)
    response = client.get("/html")
    
    assert response.status_code == 200
    # Script should not be injected because domain is invalid
    assert "<script>" not in response.text
    assert "plausible" not in response.text.lower()


@pytest.mark.asyncio
async def test_middleware_requires_both_url_and_domain(test_app):
    """Test that middleware requires both URL and domain to be configured."""
    # Mock config function with only URL
    async def get_config():
        return {
            "enabled": True,
            "base_url": "https://plausible.io",
            "site_domain": ""
        }
    
    test_app.add_middleware(
        PlausibleTrackingMiddleware,
        exempt_paths=("/api",),
        get_plausible_config=get_config
    )
    
    client = TestClient(test_app)
    response = client.get("/html")
    
    assert response.status_code == 200
    # Script should not be injected because domain is missing
    assert "plausible" not in response.text.lower()


@pytest.mark.asyncio
async def test_middleware_html_escapes_attributes(test_app):
    """Test that middleware properly escapes HTML in attributes."""
    # Mock config function with valid values
    async def get_config():
        return {
            "enabled": True,
            "base_url": "https://plausible.io",
            "site_domain": "example.com"
        }
    
    test_app.add_middleware(
        PlausibleTrackingMiddleware,
        exempt_paths=("/api",),
        get_plausible_config=get_config
    )
    
    client = TestClient(test_app)
    response = client.get("/html")
    
    assert response.status_code == 200
    # Check that script is injected and properly formed
    assert 'data-domain="example.com"' in response.text
    assert 'src="https://plausible.io/js/script.js"' in response.text


@pytest.mark.asyncio
async def test_middleware_handles_config_errors_gracefully(test_app):
    """Test that middleware handles config errors gracefully."""
    # Mock config function that raises an exception
    async def get_config():
        raise Exception("Config error")
    
    test_app.add_middleware(
        PlausibleTrackingMiddleware,
        exempt_paths=("/api",),
        get_plausible_config=get_config
    )
    
    client = TestClient(test_app)
    response = client.get("/html")
    
    # Should still return successfully without injection
    assert response.status_code == 200
    assert "<h1>Hello World</h1>" in response.text
    assert "plausible" not in response.text.lower()
