"""Login page markup for Microsoft 365 single sign-on."""
import json

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


def _flash_cookie(message: str, variant: str = "error") -> str:
    from starlette.responses import Response

    from app.security.flash import set_flash

    response = Response()
    set_flash(response, message, variant)
    header = response.headers["set-cookie"]
    return header.split("=", 1)[1].split(";", 1)[0].strip('"')


def _form_tag(html: str) -> str:
    start = html.index("<form")
    return html[start : html.index(">", start) + 1]


def test_login_form_allows_sso_auto_redirect_by_default(client):
    assert 'data-sso-auto="1"' in _form_tag(client.get("/login").text)


def test_login_form_keeps_auto_redirect_after_unrelated_flash(client):
    client.cookies.set("_flash", _flash_cookie("Your password has been reset.", "success"))
    html = client.get("/login").text
    assert 'data-sso-auto="1"' in _form_tag(html)


@pytest.mark.parametrize(
    "message",
    [
        "No MyPortal account is linked to this Microsoft account. Contact your administrator.",
        "Microsoft sign-in is not enabled for your organisation.",
        "Your Microsoft sign-in session expired. Please try again.",
        "Microsoft sign-in was cancelled or failed. Please try again.",
        "Microsoft sign-in isn't available right now. Use your password or contact your administrator.",
        "Invalid authenticator code. Start Microsoft sign-in again.",
    ],
)
def test_login_form_stops_auto_redirect_after_sso_failure(client, message):
    client.cookies.set("_flash", _flash_cookie(message))
    html = client.get("/login").text
    assert 'data-sso-auto="0"' in _form_tag(html)
    start = html.index('id="page-flash-data"')
    payload = html[html.index(">", start) + 1 : html.index("</script>", start)]
    assert json.loads(payload)["message"] == message


def test_login_page_offers_password_instead_control_hidden(client):
    html = client.get("/login").text
    tag = _tag_with(html, "data-sso-use-password")
    assert "hidden" in tag and tag.startswith("<button") and 'type="button"' in tag
    assert "Use my password instead" in html


def _tag_with(html: str, needle: str) -> str:
    start = html.rindex("<", 0, html.index(needle))
    return html[start : html.index(">", start) + 1]
