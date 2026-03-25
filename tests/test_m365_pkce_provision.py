"""Tests verifying that M365 provision routes always use PKCE.

The provision routes must use PKCE (public-client authorization code flow)
so that the customer's Global Admin can grant consent without requiring the
CSP/partner admin app to have a service principal in the customer tenant
(avoids AADSTS700016 errors during re-provisioning).
"""
from __future__ import annotations

import pytest
from datetime import datetime
from contextlib import asynccontextmanager
from types import MappingProxyType, SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import parse_qs, urlparse, urlencode

from httpx import AsyncClient as HttpxAsyncClient
from starlette.requests import Request
from itsdangerous import BadSignature

from app.main import app, m365_callback
from app.services import m365 as m365_service


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _signed_state(payload: dict) -> str:
    """Produce a signed OAuth state string using the app serialiser."""
    from app.main import oauth_state_serializer  # type: ignore[attr-defined]
    return oauth_state_serializer.dumps(payload)


def _pkce_client_id() -> str:
    return m365_service.get_pkce_client_id()


def _autoprovision_creds() -> dict:
    return MappingProxyType(
        {
            "client_id": "admin-client",
            "client_secret": "admin-secret",
            "tenant_id": "admin-tenant",
            "app_object_id": "app-obj",
            "client_secret_key_id": "secret-key",
            "client_secret_expires_at": None,
        }
    )


@asynccontextmanager
async def _mock_cursor():
    yield AsyncMock()


class _MockDatabaseConnection:
    def cursor(self, *args, **kwargs):
        return _mock_cursor()


@asynccontextmanager
async def _mock_db_acquire():
    yield _MockDatabaseConnection()


def test_parse_client_secret_expires_with_datetime():
    dt = datetime(2024, 1, 2, 3, 4, 5)
    assert m365_service._parse_client_secret_expires(dt) is dt


def test_parse_client_secret_expires_with_iso_string():
    parsed = m365_service._parse_client_secret_expires("2024-01-02T03:04:05Z")
    assert isinstance(parsed, datetime)
    assert parsed == datetime(2024, 1, 2, 3, 4, 5)


def test_parse_client_secret_expires_with_none():
    assert m365_service._parse_client_secret_expires(None) is None


# ---------------------------------------------------------------------------
# Tests for m365_provision route
# ---------------------------------------------------------------------------

@pytest.mark.anyio("asyncio")
async def test_m365_provision_uses_pkce(async_client: HttpxAsyncClient):
    """GET /m365/provision always uses the PKCE public client."""
    with (
        patch("app.main._load_license_context", new_callable=AsyncMock) as mock_ctx,
        patch(
            "app.main.m365_service.get_effective_pkce_client_id_for_company",
            new_callable=AsyncMock,
            return_value="custom-pkce-client-id",
        ),
    ):
        mock_ctx.return_value = (
            {"id": 1, "is_super_admin": True},  # user
            {},                                  # membership
            None,                                # ??
            42,                                  # company_id
            None,                                # redirect
        )

        response = await async_client.get(
            "/m365/provision?tenant_id=test-tenant-id",
            follow_redirects=False,
        )

    assert response.status_code == 303
    location = response.headers["location"]
    parsed = urlparse(location)
    qs = parse_qs(parsed.query)

    # Must use the /organizations multi-tenant endpoint to avoid AADSTS700016
    # (the PKCE client does not need to be registered in every customer tenant).
    assert "organizations" in parsed.path, "Should use /organizations endpoint, not /{tenant_id}"
    assert "test-tenant-id" not in parsed.path, "Should NOT hardcode tenant_id in the auth URL path"

    # Must pass domain_hint to guide the admin to the correct customer tenant
    assert qs.get("domain_hint", [None])[0] == "test-tenant-id", \
        "Should include domain_hint with tenant_id to guide admin to correct tenant"

    # Must use PKCE client, not admin credentials
    assert qs.get("client_id", [None])[0] == "custom-pkce-client-id"

    # Must include PKCE parameters
    assert "code_challenge" in qs, "Should include code_challenge for PKCE"
    assert qs.get("code_challenge_method", [None])[0] == "S256"


@pytest.mark.anyio("asyncio")
async def test_m365_provision_uses_pkce_even_when_admin_credentials_present(
    async_client: HttpxAsyncClient,
):
    """Provision route uses PKCE even when admin credentials are configured."""
    with (
        patch("app.main._load_license_context", new_callable=AsyncMock) as mock_ctx,
        patch(
            "app.main._get_m365_admin_credentials",
            new_callable=AsyncMock,
            return_value=("admin-client-id", "admin-client-secret"),
        ),
        patch(
            "app.main.m365_service.get_effective_pkce_client_id_for_company",
            new_callable=AsyncMock,
            return_value="custom-pkce-client-id",
        ),
    ):
        mock_ctx.return_value = (
            {"id": 1, "is_super_admin": True},
            {},
            None,
            42,
            None,
        )

        response = await async_client.get(
            "/m365/provision?tenant_id=test-tenant-id",
            follow_redirects=False,
        )

    assert response.status_code == 303
    location = response.headers["location"]
    parsed = urlparse(location)
    qs = parse_qs(parsed.query)

    # Must NOT use the admin client ID
    assert qs.get("client_id", [None])[0] != "admin-client-id"
    # Must use PKCE client
    assert qs.get("client_id", [None])[0] == "custom-pkce-client-id"
    # Must include PKCE parameters
    assert "code_challenge" in qs
    # Must use /organizations endpoint
    assert "organizations" in parsed.path


@pytest.mark.anyio("asyncio")
async def test_provision_auto_provisions_pkce_when_missing(async_client: HttpxAsyncClient):
    """Provision flow auto-creates a PKCE public client when none is cached."""
    with (
        patch("app.main._load_license_context", new_callable=AsyncMock) as mock_ctx,
        patch("app.main.db.acquire", new=_mock_db_acquire),
        patch("app.core.database.db.fetch_one", new_callable=AsyncMock, return_value=None),
        patch.object(
            m365_service,
            "modules_repo",
            SimpleNamespace(
                get_module=AsyncMock(return_value={"settings": _autoprovision_creds()}),
                update_module=AsyncMock(),
            ),
        ),
        patch.object(
            m365_service,
            "get_admin_m365_credentials",
            new_callable=AsyncMock,
            return_value=_autoprovision_creds(),
        ),
        patch.object(
            m365_service,
            "get_company_admin_credentials",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch.object(
            m365_service,
            "_exchange_token",
            new_callable=AsyncMock,
            return_value=("access-token", None, None),
        ) as mock_exchange,
        patch.object(
            m365_service,
            "provision_pkce_public_client_app",
            new_callable=AsyncMock,
            return_value="new-pkce-id",
        ) as mock_provision_pkce,
        patch.object(
            m365_service,
            "update_admin_m365_credentials",
            new_callable=AsyncMock,
        ) as mock_update_admin_creds,
    ):
        mock_ctx.return_value = (
            {"id": 1, "is_super_admin": True},
            {},
            None,
            99,
            None,
        )

        response = await async_client.get(
            "/m365/provision?tenant_id=test-tenant-id",
            follow_redirects=False,
        )

    assert response.status_code == 303
    mock_exchange.assert_awaited_once()
    mock_provision_pkce.assert_awaited_once()
    mock_update_admin_creds.assert_awaited_once()


@pytest.mark.anyio("asyncio")
async def test_company_pkce_auto_provision_uses_per_company_admin_creds():
    """Per-company PKCE auto-provision uses stored admin credentials when missing."""
    company_creds = {
        "client_id": "company-admin-id",
        "client_secret": "company-admin-secret",
        "tenant_id": "partner-tenant-id",
    }
    with (
        patch.object(
            m365_service,
            "get_company_admin_credentials",
            new_callable=AsyncMock,
            return_value=company_creds,
        ),
        patch.object(
            m365_service,
            "auto_provision_company_pkce_client_id",
            new_callable=AsyncMock,
            return_value="company-pkce-id",
        ) as mock_auto_pkce,
        patch.object(
            m365_service,
            "get_effective_pkce_client_id",
            new_callable=AsyncMock,
            return_value="fallback",
        ) as mock_fallback,
    ):
        pkce_id = await m365_service.get_effective_pkce_client_id_for_company(
            99, redirect_uri="https://example.com/m365/callback"
        )

    assert pkce_id == "company-pkce-id"
    mock_auto_pkce.assert_awaited_once()
    mock_fallback.assert_not_awaited()


@pytest.mark.anyio("asyncio")
async def test_admin_company_m365_provision_uses_pkce(async_client: HttpxAsyncClient):
    """GET /admin/companies/{id}/m365-provision always uses the PKCE public client."""
    with (
        patch("app.main._require_super_admin_page", new_callable=AsyncMock) as mock_auth,
        patch(
            "app.main.m365_service.get_effective_pkce_client_id_for_company",
            new_callable=AsyncMock,
            return_value="custom-pkce-client-id",
        ),
    ):
        mock_auth.return_value = ({"id": 1, "is_super_admin": True}, None)

        response = await async_client.get(
            "/admin/companies/5/m365-provision?tenant_id=customer-tenant-id",
            follow_redirects=False,
        )

    assert response.status_code == 303
    location = response.headers["location"]
    parsed = urlparse(location)
    qs = parse_qs(parsed.query)

    # Must use /organizations endpoint (not /{tenant_id}) to avoid AADSTS700016
    assert "organizations" in parsed.path, "Should use /organizations endpoint"
    assert "customer-tenant-id" not in parsed.path, "Should NOT hardcode tenant in URL path"

    # Must pass domain_hint for the correct tenant
    assert qs.get("domain_hint", [None])[0] == "customer-tenant-id"

    assert qs.get("client_id", [None])[0] == "custom-pkce-client-id"
    assert "code_challenge" in qs
    assert qs.get("code_challenge_method", [None])[0] == "S256"


# ---------------------------------------------------------------------------
# Tests for m365_discover route (always uses PKCE)
# ---------------------------------------------------------------------------

@pytest.mark.anyio("asyncio")
async def test_m365_discover_always_uses_pkce(
    async_client: HttpxAsyncClient,
):
    """GET /m365/discover always uses PKCE regardless of admin credential config."""
    with (
        patch("app.main._load_license_context", new_callable=AsyncMock) as mock_ctx,
        patch(
            "app.main.m365_service.get_effective_pkce_client_id_for_company",
            new_callable=AsyncMock,
            return_value="custom-pkce-client-id",
        ),
    ):
        mock_ctx.return_value = (
            {"id": 1, "is_super_admin": True},
            {},
            None,
            42,
            None,
        )

        response = await async_client.get("/m365/discover", follow_redirects=False)

    assert response.status_code == 303
    location = response.headers["location"]
    parsed = urlparse(location)
    qs = parse_qs(parsed.query)

    assert qs.get("client_id", [None])[0] == "custom-pkce-client-id"
    assert "code_challenge" in qs
    assert qs.get("code_challenge_method", [None])[0] == "S256"


@pytest.mark.anyio("asyncio")
async def test_m365_discover_uses_pkce_even_when_admin_credentials_configured(
    async_client: HttpxAsyncClient,
):
    """GET /m365/discover uses PKCE even when admin credentials are configured.

    This prevents AADSTS700025 which occurs when the configured admin client_id
    belongs to a public PKCE app rather than a confidential CSP app.
    """
    with (
        patch("app.main._load_license_context", new_callable=AsyncMock) as mock_ctx,
        patch(
            "app.main._get_m365_admin_credentials",
            new_callable=AsyncMock,
            return_value=("configured-admin-client", "configured-admin-secret"),
        ),
        patch(
            "app.main.m365_service.get_effective_pkce_client_id_for_company",
            new_callable=AsyncMock,
            return_value="custom-pkce-client-id",
        ),
    ):
        mock_ctx.return_value = (
            {"id": 1, "is_super_admin": True},
            {},
            None,
            42,
            None,
        )

        response = await async_client.get("/m365/discover", follow_redirects=False)

    assert response.status_code == 303
    location = response.headers["location"]
    parsed = urlparse(location)
    qs = parse_qs(parsed.query)

    # Must NOT use the admin client ID - always use the PKCE public client
    assert qs.get("client_id", [None])[0] != "configured-admin-client"
    assert qs.get("client_id", [None])[0] == "custom-pkce-client-id"
    assert "code_challenge" in qs
    assert qs.get("code_challenge_method", [None])[0] == "S256"


@pytest.mark.anyio("asyncio")
async def test_m365_discover_rejects_azure_cli_fallback_client(async_client: HttpxAsyncClient):
    """GET /m365/discover returns an actionable error when only Azure CLI fallback is available."""
    with (
        patch("app.main._load_license_context", new_callable=AsyncMock) as mock_ctx,
        patch(
            "app.main.m365_service.get_effective_pkce_client_id_for_company",
            new_callable=AsyncMock,
            return_value="04b07795-8542-4ab8-9e00-81f6b0a2c83a",
        ),
    ):
        mock_ctx.return_value = (
            {"id": 1, "is_super_admin": True},
            {},
            None,
            42,
            None,
        )
        response = await async_client.get("/m365/discover", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"].startswith("/m365?error=")
    assert "M365_PKCE_CLIENT_ID" in response.headers["location"]


@pytest.mark.anyio("asyncio")
async def test_m365_provision_rejects_azure_cli_fallback_client(async_client: HttpxAsyncClient):
    """GET /m365/provision returns an actionable error when only Azure CLI fallback is available."""
    with (
        patch("app.main._load_license_context", new_callable=AsyncMock) as mock_ctx,
        patch(
            "app.main.m365_service.get_effective_pkce_client_id_for_company",
            new_callable=AsyncMock,
            return_value="04b07795-8542-4ab8-9e00-81f6b0a2c83a",
        ),
    ):
        mock_ctx.return_value = (
            {"id": 1, "is_super_admin": True},
            {},
            None,
            42,
            None,
        )
        response = await async_client.get(
            "/m365/provision?tenant_id=test-tenant-id",
            follow_redirects=False,
        )

    assert response.status_code == 303
    assert response.headers["location"].startswith("/m365?error=")
    assert "M365_PKCE_CLIENT_ID" in response.headers["location"]


# ---------------------------------------------------------------------------
# Tests for discover callback (flow=="discover")
# ---------------------------------------------------------------------------

@pytest.mark.anyio("asyncio")
async def test_discover_callback_uses_pkce_token_exchange(
    async_client: HttpxAsyncClient,
):
    """Discover callback uses PKCE (no client_secret) when code_verifier is in state."""
    code_verifier, _ = m365_service.generate_pkce_pair()
    state = _signed_state(
        {
            "company_id": 42,
            "user_id": 1,
            "flow": "discover",
            "code_verifier": code_verifier,
        }
    )

    token_calls: list[dict] = []

    async def fake_post(url, *, data=None, **kwargs):
        token_calls.append({"url": url, "data": data or {}})
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "id_token": (
                "eyJhbGciOiJub25lIn0."
                "eyJ0aWQiOiJkaXNjb3ZlcmVkLXRlbmFudC1pZCJ9."
                "sig"
            )
        }
        return mock_resp

    with (
        patch("app.main.httpx.AsyncClient") as mock_http,
        patch(
            "app.main.m365_service.get_effective_pkce_client_id_for_company",
            new_callable=AsyncMock,
            return_value="custom-pkce-client-id",
        ),
        patch(
            "app.main._get_m365_admin_credentials",
            new_callable=AsyncMock,
            return_value=(None, None),
        ),
    ):
        mock_http.return_value.__aenter__ = AsyncMock(
            return_value=MagicMock(post=AsyncMock(side_effect=fake_post))
        )
        mock_http.return_value.__aexit__ = AsyncMock(return_value=False)

        response = await async_client.get(
            f"/m365/callback?code=auth-code&state={state}",
            follow_redirects=False,
        )

    assert response.status_code == 303
    assert "provision" in response.headers.get("location", "")
    assert "discovered-tenant-id" in response.headers.get("location", "")
    assert len(token_calls) == 1
    token_req = token_calls[0]["data"]
    assert token_req.get("client_id") == "custom-pkce-client-id"
    assert token_req.get("code_verifier") == code_verifier
    assert "client_secret" not in token_req


@pytest.mark.anyio("asyncio")
async def test_discover_callback_missing_code_verifier_returns_error(
    async_client: HttpxAsyncClient,
):
    """Discover callback returns an error when code_verifier is absent and no admin creds."""
    state = _signed_state(
        {
            "company_id": 42,
            "user_id": 1,
            "flow": "discover",
        }
    )

    with (
        patch(
            "app.main._get_m365_admin_credentials",
            new_callable=AsyncMock,
            return_value=(None, None),
        ),
    ):
        response = await async_client.get(
            f"/m365/callback?code=auth-code&state={state}",
            follow_redirects=False,
        )

    assert response.status_code == 303
    assert "error" in response.headers.get("location", "")


@pytest.mark.anyio("asyncio")
async def test_discover_callback_aadsts700025_returns_clear_error(
    async_client: HttpxAsyncClient,
):
    """Discover callback surfaces a clear error when AADSTS700025 is returned.

    Guards the legacy admin-creds fallback: if the configured admin client is a
    public PKCE app, the token exchange fails with AADSTS700025 and the callback
    must return an actionable message.
    """
    state = _signed_state(
        {
            "company_id": 42,
            "user_id": 1,
            "flow": "discover",
            # No code_verifier - triggers the legacy admin-creds path
        }
    )

    async def fake_post(url, *, data=None, **kwargs):
        mock_resp = MagicMock()
        mock_resp.status_code = 401
        mock_resp.text = "AADSTS700025: Client is public so client_secret not allowed"
        return mock_resp

    with (
        patch("app.main.httpx.AsyncClient") as mock_http,
        patch(
            "app.main._get_m365_admin_credentials",
            new_callable=AsyncMock,
            return_value=("some-public-client-id", "some-secret"),
        ),
    ):
        mock_http.return_value.__aenter__ = AsyncMock(
            return_value=MagicMock(post=AsyncMock(side_effect=fake_post))
        )
        mock_http.return_value.__aexit__ = AsyncMock(return_value=False)

        response = await async_client.get(
            f"/m365/callback?code=auth-code&state={state}",
            follow_redirects=False,
        )

    assert response.status_code == 303
    location = response.headers.get("location", "")
    assert "error" in location
    assert "AADSTS700025" in location or "public+app+and+cannot" in location.lower()


# ---------------------------------------------------------------------------
# Tests for provision callback (flow=="provision") with PKCE
# ---------------------------------------------------------------------------

@pytest.mark.anyio("asyncio")
async def test_provision_callback_uses_pkce_token_exchange(
    async_client: HttpxAsyncClient,
):
    """The provision callback exchanges the auth code via PKCE when code_verifier is in state."""
    code_verifier, _ = m365_service.generate_pkce_pair()
    state = _signed_state(
        {
            "company_id": 42,
            "user_id": 1,
            "tenant_id": "customer-tenant-id",
            "flow": "provision",
            "code_verifier": code_verifier,
        }
    )

    token_calls: list[dict] = []

    async def fake_post(url, *, data=None, **kwargs):
        token_calls.append({"url": url, "data": data or {}})
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"access_token": "test-access-token"}
        return mock_resp

    async def fake_provision(**kwargs):
        return {
            "client_id": "new-client-id",
            "client_secret": "new-secret",
            "app_object_id": "obj-id",
            "client_secret_key_id": "key-id",
            "client_secret_expires_at": None,
        }

    with (
        patch("app.main.httpx.AsyncClient") as mock_http,
        patch(
            "app.main.m365_service.get_effective_pkce_client_id_for_company",
            new_callable=AsyncMock,
            return_value="custom-pkce-client-id",
        ),
        patch.object(m365_service, "provision_app_registration", side_effect=fake_provision),
        patch("app.main.m365_service.upsert_credentials", new_callable=AsyncMock),
        patch(
            "app.main.m365_service.auto_provision_company_pkce_client_id",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch("app.main.company_repo.get_company_by_id", new_callable=AsyncMock, return_value={"name": "Acme"}),
        patch("app.main.scheduled_tasks_repo.get_commands_for_company", new_callable=AsyncMock, return_value=set()),
        patch("app.main.scheduled_tasks_repo.create_task", new_callable=AsyncMock),
        patch("app.main.scheduler_service.refresh", new_callable=AsyncMock),
    ):
        mock_http.return_value.__aenter__ = AsyncMock(return_value=MagicMock(post=AsyncMock(side_effect=fake_post)))
        mock_http.return_value.__aexit__ = AsyncMock(return_value=False)

        response = await async_client.get(
            f"/m365/callback?code=auth-code&state={state}",
            follow_redirects=False,
        )

    # Should have succeeded and redirected
    assert response.status_code == 303
    assert "/m365" in response.headers.get("location", "")

    # Token exchange must use PKCE (code_verifier, no client_secret)
    assert token_calls, "Expected at least one token exchange"
    token_req = token_calls[0]["data"]
    assert token_req.get("client_id") == "custom-pkce-client-id"
    assert "code_verifier" in token_req
    assert token_req["code_verifier"] == code_verifier
    assert "client_secret" not in token_req


@pytest.mark.anyio("asyncio")
async def test_provision_callback_auto_provisions_company_pkce(async_client: HttpxAsyncClient):
    """Provision callback provisions a dedicated PKCE app for the company."""
    code_verifier, _ = m365_service.generate_pkce_pair()
    state = _signed_state(
        {
            "company_id": 42,
            "user_id": 1,
            "tenant_id": "customer-tenant-id",
            "flow": "provision",
            "code_verifier": code_verifier,
        }
    )

    async def fake_post(url, *, data=None, **kwargs):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"access_token": "test-access-token"}
        return mock_resp

    async def fake_provision(**kwargs):
        return {
            "client_id": "new-client-id",
            "client_secret": "new-secret",
            "app_object_id": "obj-id",
            "client_secret_key_id": "key-id",
            "client_secret_expires_at": None,
        }

    with (
        patch("app.main.httpx.AsyncClient") as mock_http,
        patch(
            "app.main.m365_service.get_effective_pkce_client_id_for_company",
            new_callable=AsyncMock,
            return_value="custom-pkce-client-id",
        ),
        patch.object(m365_service, "provision_app_registration", side_effect=fake_provision),
        patch("app.main.m365_service.upsert_credentials", new_callable=AsyncMock),
        patch(
            "app.main.m365_service.auto_provision_company_pkce_client_id",
            new_callable=AsyncMock,
        ) as mock_auto_pkce,
        patch("app.main.company_repo.get_company_by_id", new_callable=AsyncMock, return_value={"name": "Acme"}),
        patch("app.main.scheduled_tasks_repo.get_commands_for_company", new_callable=AsyncMock, return_value=set()),
        patch("app.main.scheduled_tasks_repo.create_task", new_callable=AsyncMock),
        patch("app.main.scheduler_service.refresh", new_callable=AsyncMock),
    ):
        mock_http.return_value.__aenter__ = AsyncMock(return_value=MagicMock(post=AsyncMock(side_effect=fake_post)))
        mock_http.return_value.__aexit__ = AsyncMock(return_value=False)

        response = await async_client.get(
            f"/m365/callback?code=auth-code&state={state}",
            follow_redirects=False,
        )

    assert response.status_code == 303
    mock_auto_pkce.assert_awaited_once()
    args, kwargs = mock_auto_pkce.call_args
    assert args and args[0] == 42
    assert kwargs.get("redirect_uri")




@pytest.mark.anyio("asyncio")
async def test_provision_callback_persists_token_tenant_when_it_differs_from_state(
    async_client: HttpxAsyncClient,
):
    """Provision callback stores credentials using tenant ID extracted from token."""
    code_verifier, _ = m365_service.generate_pkce_pair()
    state = _signed_state(
        {
            "company_id": 42,
            "user_id": 1,
            "tenant_id": "state-tenant-id",
            "flow": "provision",
            "code_verifier": code_verifier,
        }
    )

    async def fake_post(url, *, data=None, **kwargs):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        # tid claim resolves to token-tenant-id
        mock_resp.json.return_value = {
            "access_token": (
                "eyJhbGciOiJub25lIn0."
                "eyJ0aWQiOiJ0b2tlbi10ZW5hbnQtaWQifQ."
                "sig"
            )
        }
        return mock_resp

    async def fake_provision(**kwargs):
        return {
            "client_id": "new-client-id",
            "client_secret": "new-secret",
            "app_object_id": "obj-id",
            "client_secret_key_id": "key-id",
            "client_secret_expires_at": None,
        }

    with (
        patch("app.main.httpx.AsyncClient") as mock_http,
        patch(
            "app.main.m365_service.get_effective_pkce_client_id_for_company",
            new_callable=AsyncMock,
            return_value="custom-pkce-client-id",
        ),
        patch.object(m365_service, "provision_app_registration", side_effect=fake_provision),
        patch("app.main.m365_service.upsert_credentials", new_callable=AsyncMock) as mock_upsert,
        patch(
            "app.main.m365_service.auto_provision_company_pkce_client_id",
            new_callable=AsyncMock,
            return_value=None,
        ),
        patch("app.main.company_repo.get_company_by_id", new_callable=AsyncMock, return_value={"name": "Acme"}),
        patch("app.main.scheduled_tasks_repo.get_commands_for_company", new_callable=AsyncMock, return_value=set()),
        patch("app.main.scheduled_tasks_repo.create_task", new_callable=AsyncMock),
        patch("app.main.scheduler_service.refresh", new_callable=AsyncMock),
    ):
        mock_http.return_value.__aenter__ = AsyncMock(return_value=MagicMock(post=AsyncMock(side_effect=fake_post)))
        mock_http.return_value.__aexit__ = AsyncMock(return_value=False)

        response = await async_client.get(
            f"/m365/callback?code=auth-code&state={state}",
            follow_redirects=False,
        )

    assert response.status_code == 303
    assert mock_upsert.await_count == 1
    assert mock_upsert.await_args.kwargs["tenant_id"] == "token-tenant-id"


@pytest.mark.anyio("asyncio")
async def test_callback_error_aadsts700016_clears_company_pkce():
    """AADSTS700016 errors should clear per-company and global PKCE IDs."""
    state = _signed_state({"company_id": 123, "flow": "discover"})
    error_param = "invalid_client"
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/m365/callback",
            "query_string": urlencode(
                {
                    "error": error_param,
                    "error_description": "AADSTS700016: Application not found",
                    "state": state,
                }
            ).encode(),
            "headers": [],
        }
    )

    with (
        patch.object(
            m365_service, "clear_company_pkce_client_id", new_callable=AsyncMock
        ) as mock_clear_company,
        patch.object(
            m365_service, "clear_pkce_client_id", new_callable=AsyncMock
        ) as mock_clear_global,
    ):
        response = await m365_callback(
            request, state=state, error=error_param
        )

    assert response.status_code == 303
    mock_clear_company.assert_awaited_once_with(123)
    mock_clear_global.assert_awaited_once()


@pytest.mark.anyio("asyncio")
async def test_callback_error_aadsts700016_without_company_id_skips_company_clear():
    """Missing company_id should still clear the global PKCE client."""
    state = _signed_state({"flow": "discover"})
    error_param = "invalid_client"
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/m365/callback",
            "query_string": urlencode(
                {
                    "error": error_param,
                    "error_description": "AADSTS700016: Application not found",
                    "state": state,
                }
            ).encode(),
            "headers": [],
        }
    )

    with (
        patch.object(
            m365_service, "clear_company_pkce_client_id", new_callable=AsyncMock
        ) as mock_clear_company,
        patch.object(
            m365_service, "clear_pkce_client_id", new_callable=AsyncMock
        ) as mock_clear_global,
    ):
        response = await m365_callback(
            request, state=state, error=error_param
        )

    assert response.status_code == 303
    mock_clear_company.assert_not_awaited()
    mock_clear_global.assert_awaited_once()


@pytest.mark.anyio("asyncio")
async def test_callback_error_aadsts700016_invalid_company_id_skips_company_clear():
    """Non-integer company_id should not attempt per-company clear."""
    state = _signed_state({"company_id": "abc", "flow": "discover"})
    error_param = "invalid_client"
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/m365/callback",
            "query_string": urlencode(
                {
                    "error": error_param,
                    "error_description": "AADSTS700016: Application not found",
                    "state": state,
                }
            ).encode(),
            "headers": [],
        }
    )

    with (
        patch.object(
            m365_service, "clear_company_pkce_client_id", new_callable=AsyncMock
        ) as mock_clear_company,
        patch.object(
            m365_service, "clear_pkce_client_id", new_callable=AsyncMock
        ) as mock_clear_global,
    ):
        response = await m365_callback(
            request, state=state, error=error_param
        )

    assert response.status_code == 303
    mock_clear_company.assert_not_awaited()
    mock_clear_global.assert_awaited_once()


@pytest.mark.anyio("asyncio")
async def test_callback_error_aadsts700016_with_unparseable_state_still_clears_global_pkce():
    """Unparseable state should still clear the global PKCE client ID."""
    error_param = "invalid_client"
    bad_state = "not-a-valid-state"
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/m365/callback",
            "query_string": urlencode(
                {
                    "error": error_param,
                    "error_description": "AADSTS700016: Application not found",
                    "state": bad_state,
                }
            ).encode(),
            "headers": [],
        }
    )

    with (
        patch(
            "app.main.oauth_state_serializer.loads",
            side_effect=BadSignature("bad state"),
        ) as mock_state_loads,
        patch.object(
            m365_service, "clear_company_pkce_client_id", new_callable=AsyncMock
        ) as mock_clear_company,
        patch.object(
            m365_service, "clear_pkce_client_id", new_callable=AsyncMock
        ) as mock_clear_global,
    ):
        response = await m365_callback(
            request, state=bad_state, error=error_param
        )

    assert response.status_code == 303
    mock_state_loads.assert_called_once()
    mock_clear_company.assert_not_awaited()
    mock_clear_global.assert_awaited_once()

# ---------------------------------------------------------------------------
# Fixture
# ---------------------------------------------------------------------------

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
