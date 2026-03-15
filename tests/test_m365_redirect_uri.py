"""Tests for M365 OAuth redirect URI construction using PORTAL_URL."""
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient
from pydantic import AnyHttpUrl

import app.main as main_module
from app.core.database import db
from app.main import app, scheduler_service
from app.security.session import SessionData

TEST_PORTAL_URL = "https://myportal.example.com"
TEST_CLIENT_ID = "test-m365-client-id"
TEST_CLIENT_SECRET = "test-m365-secret"


@pytest.fixture(autouse=True)
def mock_startup(monkeypatch):
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


def _extract_redirect_uri(location: str) -> str:
    query_params = parse_qs(urlparse(location).query)
    assert "redirect_uri" in query_params
    return query_params["redirect_uri"][0]


def test_build_m365_redirect_uri_uses_portal_url(monkeypatch):
    """_build_m365_redirect_uri returns a PORTAL_URL-based URI when PORTAL_URL is set."""
    monkeypatch.setattr(main_module.settings, "portal_url", AnyHttpUrl(TEST_PORTAL_URL))

    calls = []

    class FakeRequest:
        def url_for(self, name: str):
            calls.append(name)
            return "http://testserver/m365/callback"

    result = main_module._build_m365_redirect_uri(FakeRequest())

    assert result == f"{TEST_PORTAL_URL}/m365/callback"
    assert calls == [], "url_for should not be called when PORTAL_URL is set"


def test_build_m365_redirect_uri_falls_back_to_request(monkeypatch):
    """_build_m365_redirect_uri falls back to request.url_for when PORTAL_URL is not set."""
    monkeypatch.setattr(main_module.settings, "portal_url", None)

    calls = []

    class FakeRequest:
        def url_for(self, name: str) -> str:
            calls.append(name)
            return "http://localhost/m365/callback"

    result = main_module._build_m365_redirect_uri(FakeRequest())

    assert result == "http://localhost/m365/callback"
    assert calls == ["m365_callback"]


def _make_session() -> SessionData:
    return SessionData(
        id=1,
        user_id=1,
        session_token="test-token",
        csrf_token="test-csrf",
        created_at=datetime.now(timezone.utc),
        expires_at=datetime.now(timezone.utc) + timedelta(hours=1),
        last_seen_at=datetime.now(timezone.utc),
        ip_address="127.0.0.1",
        user_agent="test-agent",
    )


def test_admin_csp_signin_uses_portal_url_for_redirect_uri(monkeypatch):
    """admin_csp_signin includes PORTAL_URL-based redirect_uri in the Microsoft authorize URL."""
    monkeypatch.setattr(main_module.settings, "portal_url", AnyHttpUrl(TEST_PORTAL_URL))

    async def fake_load_session(request, *, allow_inactive: bool = False):
        return _make_session()

    async def fake_get_user_by_id(user_id: int):
        return {"id": 1, "is_super_admin": True}

    async def fake_get_m365_admin_credentials():
        return (TEST_CLIENT_ID, TEST_CLIENT_SECRET)

    monkeypatch.setattr(main_module.session_manager, "load_session", fake_load_session)
    monkeypatch.setattr(main_module.user_repo, "get_user_by_id", fake_get_user_by_id)
    monkeypatch.setattr(main_module, "_get_m365_admin_credentials", fake_get_m365_admin_credentials)

    with TestClient(app) as client:
        response = client.get("/admin/csp/signin", follow_redirects=False)

    assert response.status_code == 303
    location = response.headers["location"]
    parsed = urlparse(location)
    assert parsed.netloc == "login.microsoftonline.com"
    redirect_uri = _extract_redirect_uri(location)
    assert redirect_uri == f"{TEST_PORTAL_URL}/m365/callback"


def test_admin_csp_provision_uses_portal_url_for_redirect_uri(monkeypatch):
    """admin_csp_provision includes PORTAL_URL-based redirect_uri in the Microsoft authorize URL."""
    monkeypatch.setattr(main_module.settings, "portal_url", AnyHttpUrl(TEST_PORTAL_URL))
    monkeypatch.setattr(main_module.settings, "m365_bootstrap_client_id", None)

    async def fake_load_session(request, *, allow_inactive: bool = False):
        return _make_session()

    async def fake_get_user_by_id(user_id: int):
        return {"id": 1, "is_super_admin": True}

    async def fake_get_m365_admin_credentials():
        return (TEST_CLIENT_ID, TEST_CLIENT_SECRET)

    monkeypatch.setattr(main_module.session_manager, "load_session", fake_load_session)
    monkeypatch.setattr(main_module.user_repo, "get_user_by_id", fake_get_user_by_id)
    monkeypatch.setattr(main_module, "_get_m365_admin_credentials", fake_get_m365_admin_credentials)

    with TestClient(app) as client:
        response = client.get("/admin/csp/provision", follow_redirects=False)

    assert response.status_code == 303
    location = response.headers["location"]
    parsed = urlparse(location)
    assert parsed.netloc == "login.microsoftonline.com"
    redirect_uri = _extract_redirect_uri(location)
    assert redirect_uri == f"{TEST_PORTAL_URL}/m365/callback"


def _extract_prompt(location: str) -> str:
    query_params = parse_qs(urlparse(location).query)
    assert "prompt" in query_params
    return query_params["prompt"][0]


def _extract_scope(location: str) -> str:
    query_params = parse_qs(urlparse(location).query)
    assert "scope" in query_params
    return query_params["scope"][0]


def test_m365_connect_scope_does_not_mix_default_with_resource_scopes(monkeypatch):
    """/m365/connect must not combine .default with resource-specific scopes (AADSTS70011)."""
    monkeypatch.setattr(main_module.settings, "portal_url", AnyHttpUrl(TEST_PORTAL_URL))

    async def fake_load_license_context(request, **kwargs):
        user = {"id": 1, "is_super_admin": True, "company_id": 1}
        return user, None, None, 1, None

    async def fake_get_credentials(company_id: int):
        return {
            "client_id": TEST_CLIENT_ID,
            "client_secret": TEST_CLIENT_SECRET,
            "tenant_id": "test-tenant-id",
        }

    monkeypatch.setattr(main_module, "_load_license_context", fake_load_license_context)
    monkeypatch.setattr(main_module.m365_service, "get_credentials", fake_get_credentials)

    with TestClient(app) as client:
        response = client.get("/m365/connect", follow_redirects=False)

    assert response.status_code == 303
    location = response.headers["location"]
    scope = _extract_scope(location)
    assert ".default" in scope
    assert "User.Read.All" not in scope
    assert "Directory.Read.All" not in scope


def test_m365_provision_uses_consent_prompt(monkeypatch):
    """m365_provision uses prompt=consent (not admin_consent) in the authorize URL."""
    monkeypatch.setattr(main_module.settings, "portal_url", AnyHttpUrl(TEST_PORTAL_URL))

    async def fake_load_license_context(request, **kwargs):
        user = {"id": 1, "is_super_admin": True, "company_id": 1}
        return user, None, None, 1, None

    async def fake_get_m365_admin_credentials():
        return (TEST_CLIENT_ID, TEST_CLIENT_SECRET)

    monkeypatch.setattr(main_module, "_load_license_context", fake_load_license_context)
    monkeypatch.setattr(main_module, "_get_m365_admin_credentials", fake_get_m365_admin_credentials)

    with TestClient(app) as client:
        response = client.get(
            "/m365/provision",
            params={"tenant_id": "test-tenant-id"},
            follow_redirects=False,
        )

    assert response.status_code == 303
    location = response.headers["location"]
    parsed = urlparse(location)
    assert parsed.netloc == "login.microsoftonline.com"
    prompt = _extract_prompt(location)
    assert prompt == "consent", f"Expected prompt=consent, got prompt={prompt!r}"


def test_admin_company_m365_provision_uses_consent_prompt(monkeypatch):
    """admin_company_m365_provision uses prompt=consent (not admin_consent) in the authorize URL."""
    monkeypatch.setattr(main_module.settings, "portal_url", AnyHttpUrl(TEST_PORTAL_URL))

    async def fake_load_session(request, *, allow_inactive: bool = False):
        return _make_session()

    async def fake_get_user_by_id(user_id: int):
        return {"id": 1, "is_super_admin": True}

    async def fake_get_m365_admin_credentials():
        return (TEST_CLIENT_ID, TEST_CLIENT_SECRET)

    monkeypatch.setattr(main_module.session_manager, "load_session", fake_load_session)
    monkeypatch.setattr(main_module.user_repo, "get_user_by_id", fake_get_user_by_id)
    monkeypatch.setattr(main_module, "_get_m365_admin_credentials", fake_get_m365_admin_credentials)

    with TestClient(app) as client:
        response = client.get(
            "/admin/companies/42/m365-provision",
            params={"tenant_id": "test-tenant-id"},
            follow_redirects=False,
        )

    assert response.status_code == 303
    location = response.headers["location"]
    parsed = urlparse(location)
    assert parsed.netloc == "login.microsoftonline.com"
    prompt = _extract_prompt(location)
    assert prompt == "consent", f"Expected prompt=consent, got prompt={prompt!r}"
