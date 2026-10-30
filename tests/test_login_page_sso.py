"""Login page markup for Microsoft 365 single sign-on."""
import pytest
from fastapi.testclient import TestClient

from app import main as main_module
from app.main import app


@pytest.fixture
def client(monkeypatch):
    async def noop():
        return None

    async def no_session(request):
        return None

    async def existing_user_count():
        return 1

    monkeypatch.setattr(main_module.session_manager, "load_session", no_session)
    monkeypatch.setattr(main_module.user_repo, "count_users", existing_user_count)
    return TestClient(app)


def test_login_page_has_microsoft_link_without_js(client):
    html = client.get("/login").text
    assert 'href="/auth/microsoft/start"' in html
    assert "Sign in with Microsoft" in html
    assert "data-sso-login" in html
    assert "login_sso.js" in html


def test_login_page_microsoft_link_carries_next(client):
    html = client.get("/login?next=/tickets").text
    assert 'href="/auth/microsoft/start?next=%2Ftickets"' in html


def test_login_page_microsoft_link_drops_unsafe_next(client):
    html = client.get("/login?next=https://evil.example.com/").text
    assert 'href="/auth/microsoft/start"' in html
    assert "evil.example.com" not in html


def test_login_page_continue_link_is_hidden_by_default(client):
    html = client.get("/login").text
    continue_tag = next(
        line for line in html.splitlines() if "data-sso-continue" in line
    )
    assert "hidden" in continue_tag
    assert "Continue with Microsoft" in html
    assert "Your organisation uses Microsoft sign-in." in html


def test_login_page_microsoft_link_encodes_next_query(client):
    html = client.get("/login?next=/tickets%3Fstatus%3Dopen%26a%3Db").text
    assert 'href="/auth/microsoft/start?next=%2Ftickets%3Fstatus%3Dopen%26a%3Db"' in html
