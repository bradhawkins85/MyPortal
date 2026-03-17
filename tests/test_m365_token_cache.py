"""Tests for M365 access token caching in acquire_access_token."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from app.services import m365 as m365_service


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _utcnow() -> datetime:
    """Return current UTC time as a naive datetime (matches DB storage format)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _make_creds(
    access_token: str | None = "stored-access-token",
    token_expires_at: datetime | None = None,
) -> dict:
    return {
        "company_id": 1,
        "tenant_id": "tenant-abc",
        "client_id": "client-abc",
        "client_secret": "secret-abc",
        "refresh_token": None,
        "access_token": access_token,
        "token_expires_at": token_expires_at,
    }


# ---------------------------------------------------------------------------
# Tests: stored valid token is reused (no token-endpoint call)
# ---------------------------------------------------------------------------


@pytest.mark.anyio("asyncio")
async def test_acquire_access_token_reuses_valid_stored_token():
    """When a non-expired access token is cached in the DB, it is returned
    directly without calling Microsoft's token endpoint."""
    future_expiry = _utcnow() + timedelta(hours=1)
    creds = _make_creds(access_token="stored-access-token", token_expires_at=future_expiry)

    with (
        patch.object(m365_service, "get_credentials", AsyncMock(return_value=creds)),
        patch.object(m365_service.companies_repo, "get_company_csp_tenant_id", AsyncMock(return_value=None)),
        patch.object(m365_service, "_exchange_token", AsyncMock(side_effect=AssertionError("should not call token endpoint"))) as mock_exchange,
    ):
        result = await m365_service.acquire_access_token(1)

    assert result == "stored-access-token"
    mock_exchange.assert_not_called()


@pytest.mark.anyio("asyncio")
async def test_acquire_access_token_exchanges_when_token_expired():
    """When the stored token is expired a new token is requested from the endpoint."""
    past_expiry = _utcnow() - timedelta(minutes=1)
    creds = _make_creds(access_token="old-token", token_expires_at=past_expiry)

    with (
        patch.object(m365_service, "get_credentials", AsyncMock(return_value=creds)),
        patch.object(m365_service.companies_repo, "get_company_csp_tenant_id", AsyncMock(return_value=None)),
        patch.object(
            m365_service,
            "_exchange_token",
            AsyncMock(return_value=("new-access-token", None, _utcnow() + timedelta(hours=1))),
        ) as mock_exchange,
        patch.object(m365_service.m365_repo, "update_tokens", AsyncMock()),
        patch.object(m365_service, "_encrypt", lambda x: x),
    ):
        result = await m365_service.acquire_access_token(1)

    assert result == "new-access-token"
    mock_exchange.assert_called_once()


@pytest.mark.anyio("asyncio")
async def test_acquire_access_token_exchanges_within_margin():
    """When the stored token expires within 5 minutes (safety margin), a new
    token is fetched to prevent mid-request expiry."""
    soon_expiry = _utcnow() + timedelta(minutes=3)
    creds = _make_creds(access_token="almost-expired-token", token_expires_at=soon_expiry)

    with (
        patch.object(m365_service, "get_credentials", AsyncMock(return_value=creds)),
        patch.object(m365_service.companies_repo, "get_company_csp_tenant_id", AsyncMock(return_value=None)),
        patch.object(
            m365_service,
            "_exchange_token",
            AsyncMock(return_value=("fresh-token", None, _utcnow() + timedelta(hours=1))),
        ) as mock_exchange,
        patch.object(m365_service.m365_repo, "update_tokens", AsyncMock()),
        patch.object(m365_service, "_encrypt", lambda x: x),
    ):
        result = await m365_service.acquire_access_token(1)

    assert result == "fresh-token"
    mock_exchange.assert_called_once()


@pytest.mark.anyio("asyncio")
async def test_acquire_access_token_exchanges_when_no_stored_token():
    """When no access token is stored, a new token is always fetched."""
    creds = _make_creds(access_token=None, token_expires_at=None)

    with (
        patch.object(m365_service, "get_credentials", AsyncMock(return_value=creds)),
        patch.object(m365_service.companies_repo, "get_company_csp_tenant_id", AsyncMock(return_value=None)),
        patch.object(
            m365_service,
            "_exchange_token",
            AsyncMock(return_value=("brand-new-token", None, _utcnow() + timedelta(hours=1))),
        ) as mock_exchange,
        patch.object(m365_service.m365_repo, "update_tokens", AsyncMock()),
        patch.object(m365_service, "_encrypt", lambda x: x),
    ):
        result = await m365_service.acquire_access_token(1)

    assert result == "brand-new-token"
    mock_exchange.assert_called_once()


# ---------------------------------------------------------------------------
# Tests: stale refresh_token falls back to client_credentials
# ---------------------------------------------------------------------------


@pytest.mark.anyio("asyncio")
async def test_acquire_access_token_falls_back_when_refresh_token_invalid():
    """When the stored refresh_token is stale/revoked, acquire_access_token falls
    back to client_credentials and clears the stale refresh_token from the DB."""
    from app.services.m365 import M365Error

    creds = {
        "company_id": 1,
        "tenant_id": "tenant-abc",
        "client_id": "client-abc",
        "client_secret": "secret-abc",
        "refresh_token": "stale-refresh-token",
        "access_token": None,
        "token_expires_at": None,
    }

    call_count = 0

    async def _mock_exchange(**kwargs):
        nonlocal call_count
        call_count += 1
        if kwargs.get("refresh_token"):
            # Simulate Microsoft rejecting the stale refresh token
            raise M365Error("Unable to acquire Microsoft 365 access token")
        # client_credentials succeeds
        return ("cc-access-token", None, _utcnow() + timedelta(hours=1))

    mock_update = AsyncMock()
    with (
        patch.object(m365_service, "get_credentials", AsyncMock(return_value=creds)),
        patch.object(m365_service.companies_repo, "get_company_csp_tenant_id", AsyncMock(return_value=None)),
        patch.object(m365_service, "_exchange_token", side_effect=_mock_exchange),
        patch.object(m365_service.m365_repo, "update_tokens", mock_update),
        patch.object(m365_service, "_encrypt", lambda x: x),
    ):
        result = await m365_service.acquire_access_token(1)

    assert result == "cc-access-token"
    assert call_count == 2  # first attempt with refresh_token, second with client_credentials
    # The stale refresh_token must be cleared (None) so future calls skip it
    mock_update.assert_called_once()
    _, call_kwargs = mock_update.call_args
    assert call_kwargs.get("refresh_token") is None


@pytest.mark.anyio("asyncio")
async def test_acquire_access_token_raises_when_no_refresh_token_and_credentials_fail():
    """When there is no refresh_token and the client_credentials grant fails,
    the M365Error is propagated to the caller."""
    from app.services.m365 import M365Error

    creds = {
        "company_id": 1,
        "tenant_id": "tenant-abc",
        "client_id": "client-abc",
        "client_secret": "bad-secret",
        "refresh_token": None,
        "access_token": None,
        "token_expires_at": None,
    }

    with (
        patch.object(m365_service, "get_credentials", AsyncMock(return_value=creds)),
        patch.object(m365_service.companies_repo, "get_company_csp_tenant_id", AsyncMock(return_value=None)),
        patch.object(
            m365_service,
            "_exchange_token",
            AsyncMock(side_effect=M365Error("Unable to acquire Microsoft 365 access token")),
        ),
    ):
        with pytest.raises(M365Error):
            await m365_service.acquire_access_token(1)


@pytest.mark.anyio("asyncio")
async def test_acquire_access_token_raises_when_fallback_also_fails():
    """When the refresh_token fails AND the client_credentials fallback also fails,
    the M365Error is propagated to the caller."""
    from app.services.m365 import M365Error

    creds = {
        "company_id": 1,
        "tenant_id": "tenant-abc",
        "client_id": "client-abc",
        "client_secret": "bad-secret",
        "refresh_token": "stale-refresh-token",
        "access_token": None,
        "token_expires_at": None,
    }

    with (
        patch.object(m365_service, "get_credentials", AsyncMock(return_value=creds)),
        patch.object(m365_service.companies_repo, "get_company_csp_tenant_id", AsyncMock(return_value=None)),
        patch.object(
            m365_service,
            "_exchange_token",
            AsyncMock(side_effect=M365Error("Unable to acquire Microsoft 365 access token")),
        ),
    ):
        with pytest.raises(M365Error):
            await m365_service.acquire_access_token(1)


@pytest.mark.anyio("asyncio")
async def test_acquire_access_token_force_client_credentials_bypasses_cache_and_refresh():
    """force_client_credentials=True must ignore cached/delegated tokens and
    call the client_credentials grant directly."""
    creds = {
        "company_id": 1,
        "tenant_id": "tenant-abc",
        "client_id": "client-abc",
        "client_secret": "secret-abc",
        "refresh_token": "delegated-refresh-token",
        "access_token": "cached-delegated-token",
        "token_expires_at": _utcnow() + timedelta(hours=1),
    }

    mock_update = AsyncMock()
    mock_exchange = AsyncMock(return_value=("app-only-token", None, _utcnow() + timedelta(hours=1)))
    with (
        patch.object(m365_service, "get_credentials", AsyncMock(return_value=creds)),
        patch.object(m365_service.companies_repo, "get_company_csp_tenant_id", AsyncMock(return_value=None)),
        patch.object(m365_service, "_exchange_token", mock_exchange),
        patch.object(m365_service.m365_repo, "update_tokens", mock_update),
        patch.object(m365_service, "_encrypt", lambda x: x),
    ):
        result = await m365_service.acquire_access_token(1, force_client_credentials=True)

    assert result == "app-only-token"
    mock_exchange.assert_called_once()
    _, call_kwargs = mock_exchange.call_args
    assert call_kwargs.get("refresh_token") is None
