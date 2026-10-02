"""Tests for the M365 connect callback and the chained Security & Compliance consent.

The connect flow requests narrow delegated scopes (CONNECT_SCOPE) that are
only useful for granting missing application permissions and assigning the
Compliance Administrator role.  The delegated access token is never cached –
it is used in-memory and the stored access_token is always set to None.

Because AAD rejects a request that combines the EOP ``/.default`` scope with
other resources' scopes (AADSTS70011), the Security & Compliance scope is
captured in a second, chained authorization: after the main consent the
callback probes the freshly stored refresh token, and when it cannot yet
issue an SCC token the administrator is redirected to one more consent
screen (flow ``scc_consent``).
"""
from __future__ import annotations

import base64
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.main import app
from app.security.encryption import decrypt_secret
from app.services import m365 as m365_service


def _flash_from_cookie(set_cookie: str) -> dict[str, str] | None:
    """Parse the signed flash cookie into its payload dict."""
    if "_flash=" not in set_cookie:
        return None
    raw = base64.b64decode(set_cookie.split("_flash=", 1)[1].split(";", 1)[0].encode("utf-8")).decode("utf-8")
    return json.loads(raw.rsplit("|", 1)[0])


def _make_token_client(payload: dict) -> type:
    """Build a stand-in for httpx.AsyncClient returning fixed token payloads."""

    class FakeTokenClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            self.calls: list[dict] = []

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc_val, exc_tb):
            return False

        async def post(self, url, data=None, **kwargs):
            self.calls.append({"url": url, "data": data})
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = dict(payload)
            return mock_resp

    return FakeTokenClient


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
async def async_client():
    from httpx import AsyncClient, ASGITransport
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client


def _patch_connect_common(monkeypatch, update_tokens_calls, *, grant_result=True, scc_probe_ok=True):
    """Patch everything the standard connect callback touches."""
    fake_creds = {
        "tenant_id": "tenant-123",
        "client_id": "app-client-id",
        "client_secret": "secret",
    }

    async def fake_update_tokens(
        company_id,
        *,
        refresh_token,
        access_token,
        token_expires_at,
    ):
        update_tokens_calls.append(
            {
                "company_id": company_id,
                "refresh_token": refresh_token,
                "access_token": access_token,
                "token_expires_at": token_expires_at,
            }
        )
        return {}

    monkeypatch.setattr("app.main.m365_repo.update_tokens", fake_update_tokens)
    monkeypatch.setattr("app.main.m365_service.get_credentials", AsyncMock(return_value=fake_creds))
    monkeypatch.setattr(
        "app.main.m365_service.try_grant_missing_permissions",
        AsyncMock(return_value=grant_result),
    )
    monkeypatch.setattr(
        "app.main.m365_service.ensure_scc_delegated_permission",
        AsyncMock(return_value=False),
    )
    monkeypatch.setattr(
        "app.main.m365_service.validate_microsoft_id_token",
        AsyncMock(return_value={"tid": "tenant-123"}),
    )
    monkeypatch.setattr(
        "app.main.m365_service.sync_email_domains",
        AsyncMock(return_value={"domains": []}),
    )
    if scc_probe_ok:
        monkeypatch.setattr(
            "app.main.m365_service._acquire_scc_access_token",
            AsyncMock(return_value=("scc-token", "tenant-123")),
        )
    else:
        monkeypatch.setattr(
            "app.main.m365_service._acquire_scc_access_token",
            AsyncMock(side_effect=m365_service.M365Error("reauthentication required", http_status=503)),
        )
    return fake_creds


# ---------------------------------------------------------------------------
# Connect callback – access token always cleared; permissions still granted
# ---------------------------------------------------------------------------


@pytest.mark.anyio("asyncio")
async def test_connect_callback_clears_access_token_when_permissions_granted(
    async_client,
    monkeypatch,
):
    """When try_grant_missing_permissions grants new permissions, the cached
    access_token is still None (never stored) and permissions are granted.
    The SCC consent probe succeeds, so no chained consent is issued."""
    state_data = {
        "company_id": 1,
        "flow": "connect",
        "tenant_id": "tenant-123",
        "client_id": "app-client-id",
        "redirect_uri": "https://portal.example/m365/callback",
    }
    update_tokens_calls: list[dict] = []
    grant_calls: list[dict] = []

    async def fake_grant(company_id, access_token):
        grant_calls.append({"company_id": company_id, "access_token": access_token})
        return True  # permissions were granted

    _patch_connect_common(monkeypatch, update_tokens_calls)
    monkeypatch.setattr("app.main.m365_service.try_grant_missing_permissions", fake_grant)

    async def fake_consume_state(request, state):
        return state_data

    async def fake_authenticated_user(request):
        return {"id": 1, "is_super_admin": True}, None

    monkeypatch.setattr("app.main._consume_m365_oauth_state", fake_consume_state)
    monkeypatch.setattr("app.main._require_authenticated_user", fake_authenticated_user)
    monkeypatch.setattr(
        "app.main.httpx.AsyncClient",
        _make_token_client(
            {
                "access_token": "delegated-token-abc",
                "refresh_token": "refresh-xyz",
                "expires_in": 3600,
            }
        ),
    )

    response = await async_client.get(
        "/m365/callback?code=auth-code&state=opaque-state",
        follow_redirects=False,
    )

    assert response.status_code == 303
    # Probe succeeded, so the flow completes normally (no chained consent).
    assert response.headers["location"] == "/m365"

    # There should be exactly one update_tokens call that stores the
    # refresh_token but never the delegated access_token.
    assert len(update_tokens_calls) == 1, (
        f"Expected 1 update_tokens call, got {len(update_tokens_calls)}"
    )

    call = update_tokens_calls[0]
    assert call["access_token"] is None, (
        "Delegated access_token must never be cached – always set to None"
    )
    assert call["refresh_token"] is not None, (
        "refresh_token must be stored (encrypted) for future delegated acquisition"
    )
    assert decrypt_secret(call["refresh_token"]) == "refresh-xyz", (
        "stored refresh_token must decrypt to the granted value"
    )
    assert call["token_expires_at"] is None, (
        "token_expires_at must be None when access_token is not cached"
    )

    # try_grant_missing_permissions must be called with the in-memory token
    assert len(grant_calls) == 1
    assert grant_calls[0]["access_token"] == "delegated-token-abc"


@pytest.mark.anyio("asyncio")
async def test_connect_callback_clears_access_token_even_when_no_new_permissions(
    async_client,
    monkeypatch,
):
    """When try_grant_missing_permissions returns False (no new grants),
    the access_token is still not cached – always None."""
    state_data = {
        "company_id": 1,
        "flow": "connect",
        "tenant_id": "tenant-123",
        "client_id": "app-client-id",
        "redirect_uri": "https://portal.example/m365/callback",
    }
    update_tokens_calls: list[dict] = []
    _patch_connect_common(monkeypatch, update_tokens_calls, grant_result=False)

    async def fake_consume_state(request, state):
        return state_data

    async def fake_authenticated_user(request):
        return {"id": 1, "is_super_admin": True}, None

    monkeypatch.setattr("app.main._consume_m365_oauth_state", fake_consume_state)
    monkeypatch.setattr("app.main._require_authenticated_user", fake_authenticated_user)
    monkeypatch.setattr(
        "app.main.httpx.AsyncClient",
        _make_token_client(
            {
                "access_token": "delegated-token-abc",
                "refresh_token": "refresh-xyz",
                "expires_in": 3600,
            }
        ),
    )

    response = await async_client.get(
        "/m365/callback?code=auth-code&state=opaque-state",
        follow_redirects=False,
    )

    assert response.status_code == 303

    # Only one update_tokens call – access_token always None
    assert len(update_tokens_calls) == 1, (
        f"Expected exactly 1 update_tokens call; got {len(update_tokens_calls)}"
    )
    assert update_tokens_calls[0]["access_token"] is None, (
        "Delegated access_token must never be cached – always None"
    )


# ---------------------------------------------------------------------------
# Connect callback – chained Security & Compliance consent
# ---------------------------------------------------------------------------


@pytest.mark.anyio("asyncio")
async def test_connect_callback_chains_scc_consent_when_probe_fails(
    async_client,
    monkeypatch,
):
    """When the fresh refresh token cannot yet issue an SCC token, the
    callback redirects the administrator to the chained SCC consent screen
    (with the EOP scope only – never mixed into the Graph scope set)."""
    state_data = {
        "company_id": 1,
        "flow": "connect",
        "tenant_id": "tenant-123",
        "client_id": "app-client-id",
        "redirect_uri": "https://portal.example/m365/callback",
    }
    update_tokens_calls: list[dict] = []
    _patch_connect_common(monkeypatch, update_tokens_calls, scc_probe_ok=False)

    scc_states: list[dict] = []

    async def fake_new_state(request, **context):
        scc_states.append(context)
        return "scc-state-token"

    async def fake_consume_state(request, state):
        return state_data

    async def fake_authenticated_user(request):
        return {"id": 1, "is_super_admin": True}, None

    monkeypatch.setattr("app.main._new_m365_oauth_state", fake_new_state)
    monkeypatch.setattr("app.main._consume_m365_oauth_state", fake_consume_state)
    monkeypatch.setattr("app.main._require_authenticated_user", fake_authenticated_user)
    monkeypatch.setattr(
        "app.main.httpx.AsyncClient",
        _make_token_client(
            {
                "access_token": "delegated-token-abc",
                "refresh_token": "refresh-xyz",
                "expires_in": 3600,
            }
        ),
    )

    response = await async_client.get(
        "/m365/callback?code=auth-code&state=opaque-state",
        follow_redirects=False,
    )

    assert response.status_code == 303
    location = response.headers["location"]
    assert location.startswith(
        "https://login.microsoftonline.com/tenant-123/oauth2/v2.0/authorize?"
    ), location
    assert "ps.compliance.protection.outlook.com%2F.default" in location
    # The chained request must not mix Graph scopes into the /.default request.
    assert "graph.microsoft.com" not in location

    assert len(scc_states) == 1, "Exactly one chained consent transaction expected"
    assert scc_states[0]["flow"] == "scc_consent"
    assert scc_states[0]["company_id"] == 1
    assert scc_states[0]["destination"] == "/m365"


@pytest.mark.anyio("asyncio")
async def test_scc_consent_callback_stores_cumulative_refresh_token(
    async_client,
    monkeypatch,
):
    """The scc_consent callback exchanges the code with the SCC scope only
    and persists the cumulative (rotated) refresh token."""
    state_data = {
        "company_id": 1,
        "flow": "scc_consent",
        "tenant_id": "tenant-123",
        "client_id": "app-client-id",
        "redirect_uri": "https://portal.example/m365/callback",
        "destination": "/m365",
    }
    update_tokens = AsyncMock(return_value={})
    monkeypatch.setattr("app.main.m365_repo.update_tokens", update_tokens)
    monkeypatch.setattr(
        "app.main.m365_service.get_credentials",
        AsyncMock(
            return_value={
                "tenant_id": "tenant-123",
                "client_id": "app-client-id",
                "client_secret": "secret",
            }
        ),
    )
    monkeypatch.setattr(
        "app.main.m365_service.validate_microsoft_id_token",
        AsyncMock(return_value={"tid": "tenant-123"}),
    )

    async def fake_consume_state(request, state):
        return state_data

    async def fake_authenticated_user(request):
        return {"id": 1, "is_super_admin": True}, None

    monkeypatch.setattr("app.main._consume_m365_oauth_state", fake_consume_state)
    monkeypatch.setattr("app.main._require_authenticated_user", fake_authenticated_user)
    monkeypatch.setattr(
        "app.main.httpx.AsyncClient",
        _make_token_client(
            {
                "access_token": "scc-token-1",
                "refresh_token": "refresh-cumulative",
                "expires_in": 3600,
            }
        ),
    )

    response = await async_client.get(
        "/m365/callback?code=scc-code&state=opaque-state",
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/m365"

    flash = _flash_from_cookie(response.headers.get("set-cookie", ""))
    assert flash is not None and flash["variant"] == "success"
    assert "Security & Compliance access was captured" in flash["message"]

    # The cumulative refresh token is persisted; the delegated access token
    # is never cached.
    update_tokens.assert_awaited_once()
    kwargs = update_tokens.await_args.kwargs
    assert kwargs["refresh_token"] is not None
    assert decrypt_secret(kwargs["refresh_token"]) == "refresh-cumulative"
    assert kwargs["access_token"] is None


@pytest.mark.anyio("asyncio")
async def test_scc_consent_denied_reports_actionable_error(async_client, monkeypatch):
    """A denied/expired SCC consent leaves the company connected; the error
    flash must say the connection is unaffected."""
    state_data = {
        "company_id": 1,
        "flow": "scc_consent",
        "tenant_id": "tenant-123",
        "client_id": "app-client-id",
        "redirect_uri": "https://portal.example/m365/callback",
    }

    async def fake_consume_state(request, state):
        return state_data

    async def fake_authenticated_user(request):
        return {"id": 1, "is_super_admin": True}, None

    monkeypatch.setattr("app.main._consume_m365_oauth_state", fake_consume_state)
    monkeypatch.setattr("app.main._require_authenticated_user", fake_authenticated_user)

    response = await async_client.get(
        "/m365/callback?error=access_denied&error_description=User%20denied&state=opaque-state",
        follow_redirects=False,
    )

    assert response.status_code == 303
    assert response.headers["location"] == "/m365"
    flash = _flash_from_cookie(response.headers.get("set-cookie", ""))
    assert flash is not None and flash["variant"] == "error"
    assert "Security & Compliance access was not captured" in flash["message"]
    assert "unaffected" in flash["message"]
    # The AAD error code/description is preserved so the operator can tell a
    # user denial from a configuration problem that retrying never fixes.
    assert flash["message"].endswith("AAD error: User denied")


# ---------------------------------------------------------------------------
# CONNECT_SCOPE constant – must include permissions for try_grant_missing_permissions
# ---------------------------------------------------------------------------


def test_connect_scope_includes_approle_assignment_write():
    """CONNECT_SCOPE must include AppRoleAssignment.ReadWrite.All for granting app roles."""
    assert "AppRoleAssignment.ReadWrite.All" in m365_service.CONNECT_SCOPE


def test_connect_scope_includes_application_write_for_self_owner_registration():
    """CONNECT_SCOPE must allow repair of the app's self-owner relationship."""
    assert "https://graph.microsoft.com/Application.ReadWrite.All" in (
        m365_service.CONNECT_SCOPE
    )


def test_connect_scope_includes_directory_read():
    """CONNECT_SCOPE must include Directory.Read.All for service principal lookups."""
    assert "Directory.Read.All" in m365_service.CONNECT_SCOPE


def test_connect_scope_includes_offline_access():
    """CONNECT_SCOPE must include offline_access for refresh token."""
    assert "offline_access" in m365_service.CONNECT_SCOPE


def test_connect_scope_does_not_use_default():
    """CONNECT_SCOPE must NOT use Graph /.default (which only grants pre-configured permissions)."""
    assert "https://graph.microsoft.com/.default" not in m365_service.CONNECT_SCOPE


def test_connect_scope_excludes_scc_default_scope():
    """CONNECT_SCOPE must NOT contain the EOP /.default scope.

    AAD rejects an authorization request that combines one resource's
    ``/.default`` with another resource's specific scopes (AADSTS70011:
    ".default scope can't be combined with resource-specific scopes").  The
    EOP scope is captured in the chained SCC_CONNECT_SCOPE request instead.
    """
    assert "ps.compliance.protection.outlook.com" not in m365_service.CONNECT_SCOPE
    assert ".default" not in m365_service.CONNECT_SCOPE


def test_scc_connect_scope_is_standalone():
    """SCC_CONNECT_SCOPE must carry only the EOP /.default scope plus the
    standard OIDC scopes – it is its own authorization request."""
    assert "https://ps.compliance.protection.outlook.com/.default" in (
        m365_service.SCC_CONNECT_SCOPE
    )
    assert "offline_access" in m365_service.SCC_CONNECT_SCOPE
    assert "graph.microsoft.com" not in m365_service.SCC_CONNECT_SCOPE
