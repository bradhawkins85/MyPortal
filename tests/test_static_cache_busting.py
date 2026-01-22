"""Test static file cache-busting to prevent browser caching issues."""

import pytest
from pathlib import Path
from fastapi.testclient import TestClient

import app.main as main_module
from app.core.database import db
from app.main import app, scheduler_service


@pytest.fixture(autouse=True)
def mock_startup(monkeypatch):
    """Mock startup dependencies for testing."""
    async def fake_connect():
        return None

    async def fake_disconnect():
        return None

    async def fake_run_migrations():
        return None

    async def fake_start():
        return None

    async def fake_stop():
        return None

    monkeypatch.setattr(db, "connect", fake_connect)
    monkeypatch.setattr(db, "disconnect", fake_disconnect)
    monkeypatch.setattr(db, "run_migrations", fake_run_migrations)
    monkeypatch.setattr(scheduler_service, "start", fake_start)
    monkeypatch.setattr(scheduler_service, "stop", fake_stop)
    monkeypatch.setattr(main_module.settings, "enable_csrf", False)


def test_static_url_adds_version_query_string():
    """Test that static_url helper adds version query string for cache-busting."""
    # Import the function after app initialization
    from app.main import _static_url, _APP_VERSION
    
    # Test CSS file
    css_url = _static_url("/static/css/app.css")
    if _APP_VERSION:
        assert f"?v={_APP_VERSION}" in css_url
        assert css_url == f"/static/css/app.css?v={_APP_VERSION}"
    else:
        assert css_url == "/static/css/app.css"
    
    # Test JS file
    js_url = _static_url("/static/js/main.js")
    if _APP_VERSION:
        assert f"?v={_APP_VERSION}" in js_url
        assert js_url == f"/static/js/main.js?v={_APP_VERSION}"
    else:
        assert js_url == "/static/js/main.js"


def test_static_url_handles_existing_query_params():
    """Test that static_url appends version to URLs with existing query params."""
    from app.main import _static_url, _APP_VERSION
    
    url_with_params = _static_url("/static/css/app.css?theme=dark")
    if _APP_VERSION:
        assert f"&v={_APP_VERSION}" in url_with_params
        assert url_with_params == f"/static/css/app.css?theme=dark&v={_APP_VERSION}"
    else:
        assert url_with_params == "/static/css/app.css?theme=dark"


def test_base_template_uses_versioned_css():
    """Test that base.html template includes versioned CSS URLs."""
    from app.main import _APP_VERSION
    
    with TestClient(app) as client:
        # Make a request to a page that uses base.html
        response = client.get("/auth/login")
        assert response.status_code == 200
        
        html = response.text
        
        # Check that CSS is versioned
        if _APP_VERSION:
            assert f'/static/css/app.css?v={_APP_VERSION}' in html
        else:
            # If no version, should still have the CSS link
            assert '/static/css/app.css' in html


def test_base_template_uses_versioned_js():
    """Test that base.html template includes versioned JS URLs."""
    from app.main import _APP_VERSION
    
    with TestClient(app) as client:
        # Make a request to a page that uses base.html
        response = client.get("/auth/login")
        assert response.status_code == 200
        
        html = response.text
        
        # Check that main JS files are versioned
        if _APP_VERSION:
            assert f'/static/js/pwa.js?v={_APP_VERSION}' in html
            assert f'/static/js/main.js?v={_APP_VERSION}' in html
        else:
            # If no version, should still have the JS links
            assert '/static/js/pwa.js' in html
            assert '/static/js/main.js' in html


def test_version_loaded_from_file():
    """Test that version is loaded from version.txt file."""
    from app.main import _APP_VERSION
    
    version_file = Path(__file__).resolve().parent.parent / "version.txt"
    
    if version_file.is_file():
        expected_version = version_file.read_text().strip()
        assert _APP_VERSION == expected_version
        assert len(_APP_VERSION) > 0
    else:
        # If version.txt doesn't exist, version should be empty
        assert _APP_VERSION == ""


def test_static_url_available_in_jinja2_templates():
    """Test that static_url is available as a Jinja2 global function."""
    from app.main import templates
    
    # Check that static_url is registered as a global
    assert "static_url" in templates.env.globals
    assert callable(templates.env.globals["static_url"])

