"""Tests verifying that M365 provision routes always use PKCE.

The provision routes must use PKCE (public-client authorization code flow)
so that the customer's Global Admin can grant consent without requiring the
CSP/partner admin app to have a service principal in the customer tenant
(avoids AADSTS700016 errors during re-provisioning).
"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import parse_qs, urlparse

from httpx import AsyncClient as HttpxAsyncClient
from starlette.testclient import TestClient

from app.main import app
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


# ---------------------------------------------------------------------------
# Tests for m365_provision route
# ---------------------------------------------------------------------------

@pytest.mark.anyio("asyncio")
async def test_m365_provision_uses_pkce(async_client: HttpxAsyncClient):
    """GET /m365/provision always uses the PKCE public client."""
    with (
        patch("app.main._load_license_context", new_callable=AsyncMock) as mock_ctx,
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
    assert qs.get("client_id", [None])[0] == _pkce_client_id()

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
    assert qs.get("client_id", [None])[0] == _pkce_client_id()
    # Must include PKCE parameters
    assert "code_challenge" in qs
    # Must use /organizations endpoint
    assert "organizations" in parsed.path


@pytest.mark.anyio("asyncio")
async def test_admin_company_m365_provision_uses_pkce(async_client: HttpxAsyncClient):
    """GET /admin/companies/{id}/m365-provision always uses the PKCE public client."""
    with (
        patch("app.main._require_super_admin_page", new_callable=AsyncMock) as mock_auth,
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

    assert qs.get("client_id", [None])[0] == _pkce_client_id()
    assert "code_challenge" in qs
    assert qs.get("code_challenge_method", [None])[0] == "S256"


# ---------------------------------------------------------------------------
# Tests for m365_discover route (PKCE fallback)
# ---------------------------------------------------------------------------

@pytest.mark.anyio("asyncio")
async def test_m365_discover_falls_back_to_pkce_without_admin_credentials(
    async_client: HttpxAsyncClient,
):
    """GET /m365/discover falls back to PKCE when no admin credentials configured."""
    with (
        patch("app.main._load_license_context", new_callable=AsyncMock) as mock_ctx,
        patch(
            "app.main._get_m365_admin_credentials",
            new_callable=AsyncMock,
            return_value=(None, None),
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

    # Must use PKCE public client
    assert qs.get("client_id", [None])[0] == _pkce_client_id()
    assert "code_challenge" in qs
    assert qs.get("code_challenge_method", [None])[0] == "S256"


@pytest.mark.anyio("asyncio")
async def test_m365_discover_uses_admin_credentials_when_configured(
    async_client: HttpxAsyncClient,
):
    """GET /m365/discover uses admin credentials when they are configured."""
    with (
        patch("app.main._load_license_context", new_callable=AsyncMock) as mock_ctx,
        patch(
            "app.main._get_m365_admin_credentials",
            new_callable=AsyncMock,
            return_value=("configured-admin-client", "configured-admin-secret"),
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

    assert qs.get("client_id", [None])[0] == "configured-admin-client"
    # Should NOT include PKCE params when using admin credentials
    assert "code_challenge" not in qs


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
        patch.object(m365_service, "provision_app_registration", side_effect=fake_provision),
        patch("app.main.m365_service.upsert_credentials", new_callable=AsyncMock),
        patch("app.main.company_repo.get_company_by_id", new_callable=AsyncMock, return_value={"name": "Acme"}),
        patch("app.main.scheduled_tasks_repo.get_commands_for_company", new_callable=AsyncMock, return_value=[]),
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
    assert len(token_calls) == 1
    token_req = token_calls[0]["data"]
    assert token_req.get("client_id") == _pkce_client_id()
    assert "code_verifier" in token_req
    assert token_req["code_verifier"] == code_verifier
    assert "client_secret" not in token_req




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
        patch.object(m365_service, "provision_app_registration", side_effect=fake_provision),
        patch("app.main.m365_service.upsert_credentials", new_callable=AsyncMock) as mock_upsert,
        patch("app.main.company_repo.get_company_by_id", new_callable=AsyncMock, return_value={"name": "Acme"}),
        patch("app.main.scheduled_tasks_repo.get_commands_for_company", new_callable=AsyncMock, return_value=[]),
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
