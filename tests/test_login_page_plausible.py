"""Test that login and register pages include plausible_config in context."""
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app import main as main_module


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


def test_login_page_shows_success_message_after_email_verification(client, monkeypatch):
    """Verified users are told that verification succeeded and they can log in."""

    async def no_session(request):
        return None

    async def existing_user_count():
        return 1

    monkeypatch.setattr(main_module.session_manager, "load_session", no_session)
    monkeypatch.setattr(main_module.user_repo, "count_users", existing_user_count)

    response = client.get("/login?verified=1")

    assert response.status_code == 200
    assert "Your account has been verified successfully. You can now log in." in response.text


def test_login_page_does_not_show_verification_message_by_default(client, monkeypatch):
    """The verification success notice only appears following verification."""

    async def no_session(request):
        return None

    async def existing_user_count():
        return 1

    monkeypatch.setattr(main_module.session_manager, "load_session", no_session)
    monkeypatch.setattr(main_module.user_repo, "count_users", existing_user_count)

    response = client.get("/login")

    assert response.status_code == 200
    assert "Your account has been verified successfully. You can now log in." not in response.text


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
