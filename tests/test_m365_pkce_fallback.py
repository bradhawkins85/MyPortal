"""Tests for PKCE fallback in M365 discover and provision OAuth flows.

When no admin credentials (M365_ADMIN_CLIENT_ID / M365_ADMIN_CLIENT_SECRET)
are configured the discover and provision routes should fall back to PKCE
with the configured PKCE public client (get_pkce_client_id()) instead of
returning an error.  This mirrors the CSP admin provision bootstrap approach
and avoids AADSTS700016 errors caused by using deprecated public clients.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

import app.main as main_module
from app.core.database import db
from app.main import app, scheduler_service, oauth_state_serializer
from app.security.session import SessionData
from app.services import m365 as m365_service


# ---------------------------------------------------------------------------
# Shared fixtures and helpers
# ---------------------------------------------------------------------------

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


def _parse_auth_url_params(location: str) -> dict[str, str]:
    """Return the query string parameters from a Microsoft authorize redirect."""
    qs = parse_qs(urlparse(location).query)
    return {k: v[0] for k, v in qs.items()}


PKCE_CLIENT_ID = "my-pkce-client-id"


# ---------------------------------------------------------------------------
# Tests: m365_discover PKCE fallback
# ---------------------------------------------------------------------------

def test_m365_discover_uses_pkce_when_no_admin_credentials(monkeypatch):
    """m365_discover redirects with PKCE params when no admin credentials are configured."""
    monkeypatch.setattr(main_module.settings, "portal_url", None)

    async def fake_load_license_context(request, **kwargs):
        return {"id": 1, "is_super_admin": True}, None, None, 1, None

    async def fake_no_admin_creds():
        return (None, None)

    monkeypatch.setattr(main_module, "_load_license_context", fake_load_license_context)
    monkeypatch.setattr(main_module, "_get_m365_admin_credentials", fake_no_admin_creds)
    monkeypatch.setattr(m365_service, "get_pkce_client_id", lambda: PKCE_CLIENT_ID)

    with TestClient(app) as client:
        response = client.get("/m365/discover", follow_redirects=False)

    assert response.status_code == 303
    location = response.headers["location"]
    params = _parse_auth_url_params(location)

    assert params["client_id"] == PKCE_CLIENT_ID
    assert "code_challenge" in params, "PKCE code_challenge should be present"
    assert params.get("code_challenge_method") == "S256"
    assert urlparse(location).netloc == "login.microsoftonline.com"


def test_m365_discover_no_pkce_when_admin_credentials_present(monkeypatch):
    """m365_discover uses admin client (no PKCE) when credentials are configured."""
    monkeypatch.setattr(main_module.settings, "portal_url", None)

    async def fake_load_license_context(request, **kwargs):
        return {"id": 1, "is_super_admin": True}, None, None, 1, None

    async def fake_admin_creds():
        return ("admin-client-id", "admin-client-secret")

    monkeypatch.setattr(main_module, "_load_license_context", fake_load_license_context)
    monkeypatch.setattr(main_module, "_get_m365_admin_credentials", fake_admin_creds)

    with TestClient(app) as client:
        response = client.get("/m365/discover", follow_redirects=False)

    assert response.status_code == 303
    location = response.headers["location"]
    params = _parse_auth_url_params(location)

    assert params["client_id"] == "admin-client-id"
    assert "code_challenge" not in params, "PKCE should NOT be used when admin creds present"


# ---------------------------------------------------------------------------
# Tests: admin_company_m365_discover PKCE fallback
# ---------------------------------------------------------------------------

def test_admin_company_m365_discover_uses_pkce_when_no_admin_credentials(monkeypatch):
    """admin_company_m365_discover uses PKCE when no admin credentials are configured."""
    monkeypatch.setattr(main_module.settings, "portal_url", None)

    async def fake_load_session(request, *, allow_inactive: bool = False):
        return _make_session()

    async def fake_get_user_by_id(user_id: int):
        return {"id": 1, "is_super_admin": True}

    async def fake_no_admin_creds():
        return (None, None)

    monkeypatch.setattr(main_module.session_manager, "load_session", fake_load_session)
    monkeypatch.setattr(main_module.user_repo, "get_user_by_id", fake_get_user_by_id)
    monkeypatch.setattr(main_module, "_get_m365_admin_credentials", fake_no_admin_creds)
    monkeypatch.setattr(m365_service, "get_pkce_client_id", lambda: PKCE_CLIENT_ID)

    with TestClient(app) as client:
        response = client.get("/admin/companies/5/m365-discover", follow_redirects=False)

    assert response.status_code == 303
    location = response.headers["location"]
    params = _parse_auth_url_params(location)

    assert params["client_id"] == PKCE_CLIENT_ID
    assert "code_challenge" in params
    assert params.get("code_challenge_method") == "S256"


# ---------------------------------------------------------------------------
# Tests: m365_provision PKCE fallback
# ---------------------------------------------------------------------------

def test_m365_provision_uses_pkce_when_no_admin_credentials(monkeypatch):
    """m365_provision uses PKCE when no admin credentials are configured."""
    monkeypatch.setattr(main_module.settings, "portal_url", None)

    async def fake_load_license_context(request, **kwargs):
        return {"id": 1, "is_super_admin": True}, None, None, 1, None

    async def fake_no_admin_creds():
        return (None, None)

    monkeypatch.setattr(main_module, "_load_license_context", fake_load_license_context)
    monkeypatch.setattr(main_module, "_get_m365_admin_credentials", fake_no_admin_creds)
    monkeypatch.setattr(m365_service, "get_pkce_client_id", lambda: PKCE_CLIENT_ID)

    with TestClient(app) as client:
        response = client.get(
            "/m365/provision",
            params={"tenant_id": "contoso.onmicrosoft.com"},
            follow_redirects=False,
        )

    assert response.status_code == 303
    location = response.headers["location"]
    params = _parse_auth_url_params(location)

    assert params["client_id"] == PKCE_CLIENT_ID
    assert "code_challenge" in params
    assert params.get("code_challenge_method") == "S256"
    assert params.get("prompt") == "consent"
    # Auth URL must target the specific tenant (tenant ID appears in the path)
    assert urlparse(location).netloc == "login.microsoftonline.com"
    assert urlparse(location).path.split("/")[1] == "contoso.onmicrosoft.com"


def test_m365_provision_uses_pkce_even_when_admin_credentials_present(monkeypatch):
    """m365_provision always uses PKCE even when admin credentials are configured.

    The CSP admin app may not have a service principal in the customer tenant,
    which causes AADSTS700016 during the OAuth redirect.  Using PKCE
    unconditionally prevents this and ensures the enterprise app is created.
    """
    monkeypatch.setattr(main_module.settings, "portal_url", None)

    async def fake_load_license_context(request, **kwargs):
        return {"id": 1, "is_super_admin": True}, None, None, 1, None

    async def fake_admin_creds():
        return ("admin-client-id", "admin-secret")

    monkeypatch.setattr(main_module, "_load_license_context", fake_load_license_context)
    monkeypatch.setattr(main_module, "_get_m365_admin_credentials", fake_admin_creds)
    monkeypatch.setattr(m365_service, "get_pkce_client_id", lambda: PKCE_CLIENT_ID)

    with TestClient(app) as client:
        response = client.get(
            "/m365/provision",
            params={"tenant_id": "contoso.onmicrosoft.com"},
            follow_redirects=False,
        )

    assert response.status_code == 303
    location = response.headers["location"]
    params = _parse_auth_url_params(location)

    assert params["client_id"] == PKCE_CLIENT_ID, "Provision must use PKCE client, not admin credentials"
    assert "code_challenge" in params, "PKCE code_challenge must be present"
    assert params.get("code_challenge_method") == "S256"


# ---------------------------------------------------------------------------
# Tests: admin_company_m365_provision PKCE fallback
# ---------------------------------------------------------------------------

def test_admin_company_m365_provision_uses_pkce_when_no_admin_credentials(monkeypatch):
    """admin_company_m365_provision uses PKCE when no admin credentials are configured."""
    monkeypatch.setattr(main_module.settings, "portal_url", None)

    async def fake_load_session(request, *, allow_inactive: bool = False):
        return _make_session()

    async def fake_get_user_by_id(user_id: int):
        return {"id": 1, "is_super_admin": True}

    async def fake_no_admin_creds():
        return (None, None)

    monkeypatch.setattr(main_module.session_manager, "load_session", fake_load_session)
    monkeypatch.setattr(main_module.user_repo, "get_user_by_id", fake_get_user_by_id)
    monkeypatch.setattr(main_module, "_get_m365_admin_credentials", fake_no_admin_creds)
    monkeypatch.setattr(m365_service, "get_pkce_client_id", lambda: PKCE_CLIENT_ID)

    with TestClient(app) as client:
        response = client.get(
            "/admin/companies/10/m365-provision",
            params={"tenant_id": "contoso.onmicrosoft.com"},
            follow_redirects=False,
        )

    assert response.status_code == 303
    location = response.headers["location"]
    params = _parse_auth_url_params(location)

    assert params["client_id"] == PKCE_CLIENT_ID
    assert "code_challenge" in params
    assert params.get("code_challenge_method") == "S256"
    assert urlparse(location).netloc == "login.microsoftonline.com"
    assert urlparse(location).path.split("/")[1] == "contoso.onmicrosoft.com"


def test_admin_company_m365_provision_uses_pkce_even_when_admin_credentials_present(monkeypatch):
    """admin_company_m365_provision always uses PKCE even when admin credentials are configured."""
    monkeypatch.setattr(main_module.settings, "portal_url", None)

    async def fake_load_session(request, *, allow_inactive: bool = False):
        return _make_session()

    async def fake_get_user_by_id(user_id: int):
        return {"id": 1, "is_super_admin": True}

    async def fake_admin_creds():
        return ("admin-client-id", "admin-secret")

    monkeypatch.setattr(main_module.session_manager, "load_session", fake_load_session)
    monkeypatch.setattr(main_module.user_repo, "get_user_by_id", fake_get_user_by_id)
    monkeypatch.setattr(main_module, "_get_m365_admin_credentials", fake_admin_creds)
    monkeypatch.setattr(m365_service, "get_pkce_client_id", lambda: PKCE_CLIENT_ID)

    with TestClient(app) as client:
        response = client.get(
            "/admin/companies/10/m365-provision",
            params={"tenant_id": "contoso.onmicrosoft.com"},
            follow_redirects=False,
        )

    assert response.status_code == 303
    location = response.headers["location"]
    params = _parse_auth_url_params(location)

    assert params["client_id"] == PKCE_CLIENT_ID, "Provision must use PKCE client, not admin credentials"
    assert "code_challenge" in params
    assert params.get("code_challenge_method") == "S256"
    assert urlparse(location).netloc == "login.microsoftonline.com"
    assert urlparse(location).path.split("/")[1] == "contoso.onmicrosoft.com"


# ---------------------------------------------------------------------------
# Tests: PKCE code_verifier is stored in state
# ---------------------------------------------------------------------------

def test_m365_provision_stores_code_verifier_in_state(monkeypatch):
    """m365_provision always stores code_verifier in the signed OAuth state.

    PKCE is unconditionally used for per-tenant provision; the code_verifier
    must always be present so the callback can exchange the code without a
    client secret.
    """
    monkeypatch.setattr(main_module.settings, "portal_url", None)

    async def fake_load_license_context(request, **kwargs):
        return {"id": 1, "is_super_admin": True}, None, None, 1, None

    monkeypatch.setattr(main_module, "_load_license_context", fake_load_license_context)
    monkeypatch.setattr(m365_service, "get_pkce_client_id", lambda: PKCE_CLIENT_ID)

    with TestClient(app) as client:
        response = client.get(
            "/m365/provision",
            params={"tenant_id": "contoso.onmicrosoft.com"},
            follow_redirects=False,
        )

    location = response.headers["location"]
    params = _parse_auth_url_params(location)
    state_value = params["state"]
    state_data = oauth_state_serializer.loads(state_value)

    assert "code_verifier" in state_data, "code_verifier must always be stored in signed state"
    assert len(state_data["code_verifier"]) > 0


def test_m365_discover_pkce_stores_code_verifier_in_state(monkeypatch):
    """m365_discover stores code_verifier in the signed OAuth state when using PKCE."""
    monkeypatch.setattr(main_module.settings, "portal_url", None)

    async def fake_load_license_context(request, **kwargs):
        return {"id": 1, "is_super_admin": True}, None, None, 1, None

    async def fake_no_admin_creds():
        return (None, None)

    monkeypatch.setattr(main_module, "_load_license_context", fake_load_license_context)
    monkeypatch.setattr(main_module, "_get_m365_admin_credentials", fake_no_admin_creds)
    monkeypatch.setattr(m365_service, "get_pkce_client_id", lambda: PKCE_CLIENT_ID)

    with TestClient(app) as client:
        response = client.get("/m365/discover", follow_redirects=False)

    location = response.headers["location"]
    params = _parse_auth_url_params(location)
    state_value = params["state"]
    state_data = oauth_state_serializer.loads(state_value)

    assert "code_verifier" in state_data
    assert len(state_data["code_verifier"]) > 0


# ---------------------------------------------------------------------------
# Tests: code_verifier is always in state for provision (even with admin creds)
# ---------------------------------------------------------------------------

def test_m365_provision_always_stores_code_verifier_in_state_even_with_admin_creds(monkeypatch):
    """m365_provision stores code_verifier in state even when admin credentials are configured."""
    monkeypatch.setattr(main_module.settings, "portal_url", None)

    async def fake_load_license_context(request, **kwargs):
        return {"id": 1, "is_super_admin": True}, None, None, 1, None

    async def fake_admin_creds():
        return ("admin-client-id", "admin-secret")

    monkeypatch.setattr(main_module, "_load_license_context", fake_load_license_context)
    monkeypatch.setattr(main_module, "_get_m365_admin_credentials", fake_admin_creds)
    monkeypatch.setattr(m365_service, "get_pkce_client_id", lambda: PKCE_CLIENT_ID)

    with TestClient(app) as client:
        response = client.get(
            "/m365/provision",
            params={"tenant_id": "contoso.onmicrosoft.com"},
            follow_redirects=False,
        )

    location = response.headers["location"]
    params = _parse_auth_url_params(location)
    state_data = oauth_state_serializer.loads(params["state"])

    assert "code_verifier" in state_data, "code_verifier must be in state for PKCE token exchange"
    assert len(state_data["code_verifier"]) > 0
