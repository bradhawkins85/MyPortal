"""Test that login and register pages include plausible_config in context."""
import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client():
    """Create a test client."""
    return TestClient(app)


def test_login_page_loads_without_plausible_error(client):
    """Test that the login page loads without undefined plausible_config error."""
    response = client.get("/login")
    
    # Should return 200 OK (or 303 redirect if already authenticated)
    assert response.status_code in [200, 303]
    
    # If it's a 200, make sure the response is HTML and doesn't contain error
    if response.status_code == 200:
        assert "text/html" in response.headers.get("content-type", "")
        # Should not contain Jinja2 error about undefined plausible_config
        response_lower = response.text.lower()
        assert "plausible_config" not in response_lower or "undefined" not in response_lower
        # Specifically check that we don't have the exact error message
        assert "'plausible_config' is undefined" not in response.text


def test_register_page_loads_without_plausible_error(client):
    """Test that the register page loads without undefined plausible_config error."""
    response = client.get("/register")
    
    # Should return 200 OK (or 303 redirect if already authenticated or if users exist)
    assert response.status_code in [200, 303]
    
    # If it's a 200, make sure the response is HTML and doesn't contain error
    if response.status_code == 200:
        assert "text/html" in response.headers.get("content-type", "")
        # Should not contain Jinja2 error about undefined plausible_config
        response_lower = response.text.lower()
        assert "plausible_config" not in response_lower or "undefined" not in response_lower
        # Specifically check that we don't have the exact error message
        assert "'plausible_config' is undefined" not in response.text
