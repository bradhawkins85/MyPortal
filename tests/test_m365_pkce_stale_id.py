"""Tests verifying that a stale PKCE client ID is properly cleared.

When the PKCE app registration has been deleted from Azure AD, Microsoft
returns AADSTS700016.  The system must:

1. Clear the stale ``pkce_client_id`` from the m365-admin module settings.
2. Show the admin a helpful message directing them to sign in again or
   re-provision the M365 integration.

Also verifies that re-provisioning (``csp_admin_provision`` callback) clears
the stale ID when ``provision_pkce_public_client_app`` fails (returns None).
"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import parse_qs, urlparse

from httpx import AsyncClient as HttpxAsyncClient

from app.main import app
from app.services import m365 as m365_service


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _signed_state(payload: dict) -> str:
    from app.main import oauth_state_serializer  # type: ignore[attr-defined]
    return oauth_state_serializer.dumps(payload)


# ---------------------------------------------------------------------------
# Tests: AADSTS700016 in /m365/callback error handler
# ---------------------------------------------------------------------------

@pytest.mark.anyio("asyncio")
async def test_callback_clears_pkce_id_on_aadsts700016(async_client: HttpxAsyncClient):
    """AADSTS700016 in callback clears stale pkce_client_id and shows helpful message."""
    state = _signed_state({
        "company_id": 0,
        "user_id": 1,
        "flow": "csp_admin_provision",
    })
    error_desc = "AADSTS700016: Application with identifier 'stale-pkce-id' was not found in the directory"

    with patch.object(m365_service, "clear_pkce_client_id", new_callable=AsyncMock) as mock_clear:
        response = await async_client.get(
            f"/m365/callback?error=unauthorized_client"
            f"&error_description={error_desc}"
            f"&state={state}",
            follow_redirects=False,
        )

    assert response.status_code == 303
    mock_clear.assert_awaited_once()

    location = response.headers["location"]
    assert "error" in location
    # The message should mention re-provisioning or signing in again
    assert "AADSTS700016" in location or "cleared" in location or "sign" in location.lower()


@pytest.mark.anyio("asyncio")
async def test_callback_does_not_clear_pkce_id_on_other_errors(async_client: HttpxAsyncClient):
    """Non-AADSTS700016 errors do not trigger pkce_client_id clearing."""
    state = _signed_state({
        "company_id": 0,
        "user_id": 1,
        "flow": "csp_admin_provision",
    })

    with patch.object(m365_service, "clear_pkce_client_id", new_callable=AsyncMock) as mock_clear:
        response = await async_client.get(
            "/m365/callback?error=access_denied&state=" + state,
            follow_redirects=False,
        )

    assert response.status_code == 303
    mock_clear.assert_not_called()


@pytest.mark.anyio("asyncio")
async def test_callback_aadsts700016_redirects_to_m365_by_default(async_client: HttpxAsyncClient):
    """AADSTS700016 with a non-mail flow redirects to /m365."""
    state = _signed_state({
        "company_id": 0,
        "user_id": 1,
        "flow": "discover",
    })

    with patch.object(m365_service, "clear_pkce_client_id", new_callable=AsyncMock):
        response = await async_client.get(
            "/m365/callback?error=unauthorized_client"
            "&error_description=AADSTS700016: app not found"
            "&state=" + state,
            follow_redirects=False,
        )

    assert response.status_code == 303
    location = response.headers["location"]
    assert location.startswith("/m365?")


@pytest.mark.anyio("asyncio")
async def test_callback_aadsts700016_redirects_to_mail_module_for_mail_flow(
    async_client: HttpxAsyncClient,
):
    """AADSTS700016 during m365_mail_auth redirects to mail module page."""
    state = _signed_state({
        "company_id": 0,
        "user_id": 1,
        "flow": "m365_mail_auth",
        "account_id": 7,
    })

    with patch.object(m365_service, "clear_pkce_client_id", new_callable=AsyncMock):
        response = await async_client.get(
            "/m365/callback?error=unauthorized_client"
            "&error_description=AADSTS700016: app not found"
            "&state=" + state,
            follow_redirects=False,
        )

    assert response.status_code == 303
    location = response.headers["location"]
    assert location.startswith("/admin/modules/m365-mail?")


# ---------------------------------------------------------------------------
# Tests: clear_pkce_client_id when provision returns no PKCE app
# ---------------------------------------------------------------------------

@pytest.mark.anyio("asyncio")
async def test_csp_provision_clears_stale_pkce_when_creation_fails(
    async_client: HttpxAsyncClient,
):
    """csp_admin_provision callback clears stale pkce_client_id when provision_pkce returns None."""
    import base64
    import json
    # Build a minimal fake JWT so extract_tenant_id_from_token works.
    payload = base64.urlsafe_b64encode(json.dumps({"tid": "partner-tenant"}).encode()).decode().rstrip("=")
    fake_token = f"eyJhbGciOiJub25lIn0.{payload}.sig"

    code_verifier, _ = m365_service.generate_pkce_pair()
    state = _signed_state({
        "company_id": 0,
        "user_id": 1,
        "flow": "csp_admin_provision",
        "code_verifier": code_verifier,
    })

    async def fake_post(url, *, data=None, **kwargs):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"access_token": fake_token}
        return mock_resp

    provision_result = {
        "client_id": "new-admin-client-id",
        "client_secret": "new-admin-secret",
        "tenant_id": "partner-tenant",
        "app_object_id": "obj-id",
        "client_secret_key_id": "key-id",
        "client_secret_expires_at": None,
        "pkce_client_id": None,  # PKCE app creation failed
    }

    with (
        patch("app.main.httpx.AsyncClient") as mock_http,
        patch.object(
            m365_service,
            "provision_csp_admin_app_registration",
            new_callable=AsyncMock,
            return_value=provision_result,
        ),
        patch.object(m365_service, "update_admin_m365_credentials", new_callable=AsyncMock),
        patch.object(m365_service, "clear_pkce_client_id", new_callable=AsyncMock) as mock_clear,
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
    mock_clear.assert_awaited_once()


@pytest.mark.anyio("asyncio")
async def test_csp_provision_does_not_clear_pkce_when_creation_succeeds(
    async_client: HttpxAsyncClient,
):
    """csp_admin_provision callback does NOT call clear_pkce_client_id when a new PKCE app was created."""
    import base64
    import json
    payload = base64.urlsafe_b64encode(json.dumps({"tid": "partner-tenant"}).encode()).decode().rstrip("=")
    fake_token = f"eyJhbGciOiJub25lIn0.{payload}.sig"

    code_verifier, _ = m365_service.generate_pkce_pair()
    state = _signed_state({
        "company_id": 0,
        "user_id": 1,
        "flow": "csp_admin_provision",
        "code_verifier": code_verifier,
    })

    async def fake_post(url, *, data=None, **kwargs):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"access_token": fake_token}
        return mock_resp

    provision_result = {
        "client_id": "new-admin-client-id",
        "client_secret": "new-admin-secret",
        "tenant_id": "partner-tenant",
        "app_object_id": "obj-id",
        "client_secret_key_id": "key-id",
        "client_secret_expires_at": None,
        "pkce_client_id": "fresh-pkce-id",  # PKCE app created successfully
    }

    with (
        patch("app.main.httpx.AsyncClient") as mock_http,
        patch.object(
            m365_service,
            "provision_csp_admin_app_registration",
            new_callable=AsyncMock,
            return_value=provision_result,
        ),
        patch.object(m365_service, "update_admin_m365_credentials", new_callable=AsyncMock),
        patch.object(m365_service, "clear_pkce_client_id", new_callable=AsyncMock) as mock_clear,
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
    mock_clear.assert_not_called()


# ---------------------------------------------------------------------------
# Tests: clear_pkce_client_id service function
# ---------------------------------------------------------------------------

@pytest.mark.anyio("asyncio")
async def test_clear_pkce_client_id_removes_field():
    """clear_pkce_client_id removes pkce_client_id from stored settings."""
    from app.repositories import integration_modules as modules_repo

    existing_module = {
        "settings": {
            "client_id": "admin-client",
            "client_secret": "enc:secret",
            "tenant_id": "tenant",
            "pkce_client_id": "stale-pkce-id",
        },
        "enabled": True,
    }

    updated_settings: dict = {}

    async def fake_get_module(slug):
        return existing_module

    async def fake_update_module(slug, *, settings=None, **kwargs):
        if settings is not None:
            updated_settings.update(settings)

    with (
        patch.object(modules_repo, "get_module", side_effect=fake_get_module),
        patch.object(modules_repo, "update_module", side_effect=fake_update_module),
    ):
        await m365_service.clear_pkce_client_id()

    assert "pkce_client_id" not in updated_settings
    assert updated_settings.get("client_id") == "admin-client"


@pytest.mark.anyio("asyncio")
async def test_clear_pkce_client_id_noop_when_no_module():
    """clear_pkce_client_id is a no-op when the m365-admin module does not exist."""
    from app.repositories import integration_modules as modules_repo

    with (
        patch.object(modules_repo, "get_module", new_callable=AsyncMock, return_value=None),
        patch.object(modules_repo, "update_module", new_callable=AsyncMock) as mock_update,
    ):
        await m365_service.clear_pkce_client_id()

    mock_update.assert_not_called()


@pytest.mark.anyio("asyncio")
async def test_clear_pkce_client_id_noop_when_field_absent():
    """clear_pkce_client_id is a no-op when pkce_client_id is not in settings."""
    from app.repositories import integration_modules as modules_repo

    existing_module = {
        "settings": {"client_id": "admin-client", "client_secret": "enc:secret"},
        "enabled": True,
    }

    with (
        patch.object(modules_repo, "get_module", new_callable=AsyncMock, return_value=existing_module),
        patch.object(modules_repo, "update_module", new_callable=AsyncMock) as mock_update,
    ):
        await m365_service.clear_pkce_client_id()

    mock_update.assert_not_called()


# ---------------------------------------------------------------------------
# Fixtures
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
