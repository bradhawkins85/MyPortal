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
        assert "plausible_config" not in response.text or "undefined" not in response.text.lower()


def test_register_page_loads_without_plausible_error(client):
    """Test that the register page loads without undefined plausible_config error."""
    response = client.get("/register")
    
    # Should return 200 OK (or 303 redirect if already authenticated or if users exist)
    assert response.status_code in [200, 303]
    
    # If it's a 200, make sure the response is HTML and doesn't contain error
    if response.status_code == 200:
        assert "text/html" in response.headers.get("content-type", "")
        # Should not contain Jinja2 error about undefined plausible_config
        assert "plausible_config" not in response.text or "undefined" not in response.text.lower()
