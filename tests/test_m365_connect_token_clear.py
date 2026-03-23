"""Tests that the connect callback always clears the delegated access token and
uses it only transiently for try_grant_missing_permissions.

The connect flow now requests narrow delegated scopes (CONNECT_SCOPE) that are
only useful for granting missing application permissions.  The delegated token
is never cached – it is used in-memory for try_grant_missing_permissions() and
the stored access_token is always set to None so that subsequent syncs acquire
a fresh client_credentials token carrying the full set of application
permissions.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.main import app
from app.services import m365 as m365_service


def _signed_state(payload: dict) -> str:
    from app.main import oauth_state_serializer  # type: ignore[attr-defined]
    return oauth_state_serializer.dumps(payload)


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


# ---------------------------------------------------------------------------
# Connect callback – access token always cleared; permissions still granted
# ---------------------------------------------------------------------------


@pytest.mark.anyio("asyncio")
async def test_connect_callback_clears_access_token_when_permissions_granted(
    async_client,
):
    """When try_grant_missing_permissions grants new permissions, the cached
    access_token is still None (never stored) and permissions are granted."""
    state = _signed_state(
        {
            "company_id": 1,
            "user_id": 1,
            "flow": "connect",
        }
    )

    update_tokens_calls: list[dict] = []

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

    fake_creds = {
        "tenant_id": "tenant-123",
        "client_id": "app-client-id",
        "client_secret": "secret",
    }

    async def fake_token_post(url, *, data=None, **kwargs):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "access_token": "delegated-token-abc",
            "refresh_token": "refresh-xyz",
            "expires_in": 3600,
        }
        return mock_resp

    grant_calls: list[dict] = []

    async def fake_grant(company_id, access_token):
        grant_calls.append({"company_id": company_id, "access_token": access_token})
        return True  # permissions were granted

    with (
        patch("app.main.m365_repo.update_tokens", side_effect=fake_update_tokens),
        patch("app.main.m365_service.get_credentials", AsyncMock(return_value=fake_creds)),
        patch(
            "app.main.m365_service.try_grant_missing_permissions",
            side_effect=fake_grant,
        ),
        patch("app.main.httpx.AsyncClient") as mock_http,
    ):
        mock_http.return_value.__aenter__ = AsyncMock(
            return_value=MagicMock(post=AsyncMock(side_effect=fake_token_post))
        )
        mock_http.return_value.__aexit__ = AsyncMock(return_value=False)

        response = await async_client.get(
            f"/m365/callback?code=auth-code&state={state}",
            follow_redirects=False,
        )

    assert response.status_code == 303

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
        "refresh_token must be stored for future client_credentials acquisition"
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
):
    """When try_grant_missing_permissions returns False (no new grants),
    the access_token is still not cached – always None."""
    state = _signed_state(
        {
            "company_id": 1,
            "user_id": 1,
            "flow": "connect",
        }
    )

    update_tokens_calls: list[dict] = []

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
                "access_token": access_token,
            }
        )
        return {}

    fake_creds = {
        "tenant_id": "tenant-123",
        "client_id": "app-client-id",
        "client_secret": "secret",
    }

    async def fake_token_post(url, *, data=None, **kwargs):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "access_token": "delegated-token-abc",
            "refresh_token": "refresh-xyz",
            "expires_in": 3600,
        }
        return mock_resp

    with (
        patch("app.main.m365_repo.update_tokens", side_effect=fake_update_tokens),
        patch("app.main.m365_service.get_credentials", AsyncMock(return_value=fake_creds)),
        patch(
            "app.main.m365_service.try_grant_missing_permissions",
            new_callable=AsyncMock,
            return_value=False,  # no new permissions
        ),
        patch("app.main.httpx.AsyncClient") as mock_http,
    ):
        mock_http.return_value.__aenter__ = AsyncMock(
            return_value=MagicMock(post=AsyncMock(side_effect=fake_token_post))
        )
        mock_http.return_value.__aexit__ = AsyncMock(return_value=False)

        response = await async_client.get(
            f"/m365/callback?code=auth-code&state={state}",
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
# CONNECT_SCOPE constant – must include permissions for try_grant_missing_permissions
# ---------------------------------------------------------------------------


def test_connect_scope_includes_approle_assignment_write():
    """CONNECT_SCOPE must include AppRoleAssignment.ReadWrite.All for granting app roles."""
    assert "AppRoleAssignment.ReadWrite.All" in m365_service.CONNECT_SCOPE


def test_connect_scope_includes_directory_read():
    """CONNECT_SCOPE must include Directory.Read.All for service principal lookups."""
    assert "Directory.Read.All" in m365_service.CONNECT_SCOPE


def test_connect_scope_includes_offline_access():
    """CONNECT_SCOPE must include offline_access for refresh token."""
    assert "offline_access" in m365_service.CONNECT_SCOPE


def test_connect_scope_does_not_use_default():
    """CONNECT_SCOPE must NOT use /.default (which only grants pre-configured permissions)."""
    assert ".default" not in m365_service.CONNECT_SCOPE
