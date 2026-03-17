"""Tests that the connect callback clears the cached delegated access token when
new permissions are successfully granted via try_grant_missing_permissions.

After an administrator re-authorises via 'Authorize portal access', the connect
callback stores a delegated access token.  Delegated tokens do NOT include
application permissions such as Reports.Read.All.  If try_grant_missing_permissions
just added Reports.Read.All to the app's appRoleAssignments, the cached delegated
token would cause mailbox sync to keep failing (403) for up to ~1 hour until it
expires.

The fix: when new permissions are granted, immediately clear the cached access_token
so the next sync acquires a fresh client_credentials token that includes the new
application permissions.
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
# Connect callback – access token cleared when new permissions are granted
# ---------------------------------------------------------------------------


@pytest.mark.anyio("asyncio")
async def test_connect_callback_clears_access_token_when_permissions_granted(
    async_client,
):
    """When try_grant_missing_permissions grants new permissions, the cached
    access_token is cleared so the next sync uses client_credentials."""
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

    with (
        patch("app.main.m365_repo.update_tokens", side_effect=fake_update_tokens),
        patch("app.main.m365_service.get_credentials", AsyncMock(return_value=fake_creds)),
        patch(
            "app.main.m365_service.try_grant_missing_permissions",
            new_callable=AsyncMock,
            return_value=True,  # permissions were granted
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

    # There should be two update_tokens calls:
    # 1st: stores the connect-flow tokens (access_token + refresh_token)
    # 2nd: clears the access_token after granting new permissions
    assert len(update_tokens_calls) == 2, (
        f"Expected 2 update_tokens calls (store + clear), got {len(update_tokens_calls)}"
    )

    first_call = update_tokens_calls[0]
    assert first_call["access_token"] is not None, (
        "First call should store the delegated access_token"
    )
    assert first_call["refresh_token"] is not None, (
        "First call should store the refresh_token"
    )

    second_call = update_tokens_calls[1]
    assert second_call["access_token"] is None, (
        "Second call must clear access_token (set to None) after granting new permissions"
    )
    assert second_call["refresh_token"] is not None, (
        "Second call must preserve the refresh_token"
    )
    assert second_call["token_expires_at"] is None, (
        "Second call must clear token_expires_at"
    )


@pytest.mark.anyio("asyncio")
async def test_connect_callback_does_not_clear_access_token_when_no_new_permissions(
    async_client,
):
    """When try_grant_missing_permissions returns False (no new grants),
    the access_token is NOT cleared – no extra update_tokens call needed."""
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

    # Only one update_tokens call expected (the initial token storage)
    assert len(update_tokens_calls) == 1, (
        f"Expected exactly 1 update_tokens call when no new permissions; got {len(update_tokens_calls)}"
    )
    assert update_tokens_calls[0]["access_token"] is not None, (
        "The access_token must be stored (not cleared) when no permissions were newly granted"
    )
