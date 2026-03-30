"""Tests for detailed M365 sync logging added to aid 403 debugging.

Verifies that:
- acquire_access_token logs company_id, effective_tenant_id, client_id, and
  csp_mapping_applied when acquiring a fresh token.
- acquire_access_token logs the cache-hit path with tenant/client context.
- acquire_access_token logs tenant_id + client_id in the refresh-token fallback
  error message.
- _exchange_token logs tenant_id, client_id, and grant_type on token failure.
- sync_company_licenses logs a start message with company_id.
- _sync_staff_assignments logs a start message with company_id, license_id, sku_id.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services import m365 as m365_service
from app.services.m365 import M365Error


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _make_creds(
    tenant_id: str = "tenant-abc",
    client_id: str = "client-abc",
    access_token: str | None = None,
    token_expires_at: datetime | None = None,
    refresh_token: str | None = None,
) -> dict[str, Any]:
    return {
        "company_id": 1,
        "tenant_id": tenant_id,
        "client_id": client_id,
        "client_secret": "secret-abc",
        "refresh_token": refresh_token,
        "access_token": access_token,
        "token_expires_at": token_expires_at,
    }


# ---------------------------------------------------------------------------
# acquire_access_token – token cache hit logging
# ---------------------------------------------------------------------------


@pytest.mark.anyio("asyncio")
async def test_acquire_access_token_logs_cache_hit():
    """When a valid cached token is returned, company_id, tenant_id and
    client_id must appear in the info log."""
    future_expiry = _utcnow() + timedelta(hours=1)
    creds = _make_creds(access_token="cached-token", token_expires_at=future_expiry)

    logged_calls: list[tuple[str, dict[str, Any]]] = []

    def _capture_log_info(msg: str, **kwargs: Any) -> None:
        logged_calls.append((msg, kwargs))

    with (
        patch.object(m365_service, "get_credentials", AsyncMock(return_value=creds)),
        patch.object(m365_service.companies_repo, "get_company_csp_tenant_id", AsyncMock(return_value=None)),
        patch.object(m365_service, "log_info", side_effect=_capture_log_info),
    ):
        result = await m365_service.acquire_access_token(1)

    assert result == "cached-token"

    # There must be a cache-hit log entry
    cache_hit_logs = [(msg, kw) for msg, kw in logged_calls if msg == "M365 using cached access token"]
    assert cache_hit_logs, "Expected 'M365 using cached access token' log message"
    _, kw = cache_hit_logs[0]
    assert kw.get("company_id") == 1
    assert kw.get("tenant_id") == "tenant-abc"
    assert kw.get("client_id") == "client-abc"


# ---------------------------------------------------------------------------
# acquire_access_token – fresh token acquisition logging
# ---------------------------------------------------------------------------


@pytest.mark.anyio("asyncio")
async def test_acquire_access_token_logs_acquiring_and_success():
    """When a fresh token is acquired, logs must include company_id,
    effective_tenant_id, client_id, and csp_mapping_applied."""
    creds = _make_creds(access_token=None, token_expires_at=None)
    future_expiry = _utcnow() + timedelta(hours=1)

    logged_calls: list[tuple[str, dict[str, Any]]] = []

    def _capture_log_info(msg: str, **kwargs: Any) -> None:
        logged_calls.append((msg, kwargs))

    with (
        patch.object(m365_service, "get_credentials", AsyncMock(return_value=creds)),
        patch.object(m365_service.companies_repo, "get_company_csp_tenant_id", AsyncMock(return_value=None)),
        patch.object(
            m365_service,
            "_exchange_token",
            AsyncMock(return_value=("new-token", None, future_expiry)),
        ),
        patch.object(m365_service.m365_repo, "update_tokens", AsyncMock()),
        patch.object(m365_service, "_encrypt", lambda x: x),
        patch.object(m365_service, "log_info", side_effect=_capture_log_info),
    ):
        result = await m365_service.acquire_access_token(1)

    assert result == "new-token"

    acquiring_logs = [(msg, kw) for msg, kw in logged_calls if msg == "M365 acquiring access token"]
    assert acquiring_logs, "Expected 'M365 acquiring access token' log message"
    _, kw = acquiring_logs[0]
    assert kw.get("company_id") == 1
    assert kw.get("tenant_id") == "tenant-abc"
    assert kw.get("client_id") == "client-abc"
    assert kw.get("effective_tenant_id") == "tenant-abc"
    assert kw.get("csp_mapping_applied") is False

    success_logs = [(msg, kw) for msg, kw in logged_calls if msg == "M365 access token acquired successfully"]
    assert success_logs, "Expected 'M365 access token acquired successfully' log message"
    _, kw = success_logs[0]
    assert kw.get("company_id") == 1
    assert kw.get("effective_tenant_id") == "tenant-abc"
    assert kw.get("client_id") == "client-abc"
    assert kw.get("grant_type") == "client_credentials"


@pytest.mark.anyio("asyncio")
async def test_acquire_access_token_logs_csp_mapping():
    """When a CSP tenant mapping is applied, csp_mapping_applied=True and
    effective_tenant_id equals the mapped CSP tenant, not the creds tenant."""
    creds = _make_creds(tenant_id="partner-tenant", client_id="csp-client")
    future_expiry = _utcnow() + timedelta(hours=1)

    logged_calls: list[tuple[str, dict[str, Any]]] = []

    def _capture_log_info(msg: str, **kwargs: Any) -> None:
        logged_calls.append((msg, kwargs))

    with (
        patch.object(m365_service, "get_credentials", AsyncMock(return_value=creds)),
        patch.object(
            m365_service.companies_repo,
            "get_company_csp_tenant_id",
            AsyncMock(return_value="customer-tenant"),
        ),
        patch.object(
            m365_service,
            "_exchange_token",
            AsyncMock(return_value=("csp-token", None, future_expiry)),
        ),
        patch.object(m365_service.m365_repo, "update_tokens", AsyncMock()),
        patch.object(m365_service, "_encrypt", lambda x: x),
        patch.object(m365_service, "log_info", side_effect=_capture_log_info),
    ):
        result = await m365_service.acquire_access_token(42)

    assert result == "csp-token"

    acquiring_logs = [(msg, kw) for msg, kw in logged_calls if msg == "M365 acquiring access token"]
    assert acquiring_logs, "Expected 'M365 acquiring access token' log message"
    _, kw = acquiring_logs[0]
    assert kw.get("company_id") == 42
    assert kw.get("tenant_id") == "partner-tenant"
    assert kw.get("effective_tenant_id") == "customer-tenant"
    assert kw.get("csp_mapping_applied") is True


# ---------------------------------------------------------------------------
# acquire_access_token – refresh token fallback error logging
# ---------------------------------------------------------------------------


@pytest.mark.anyio("asyncio")
async def test_acquire_access_token_fallback_logs_tenant_and_client():
    """When the refresh token is invalid the fallback error log must include
    tenant_id and client_id so operators can identify the failing company."""
    creds = _make_creds(refresh_token="stale-refresh")
    future_expiry = _utcnow() + timedelta(hours=1)

    call_count = 0

    async def _mock_exchange(**kwargs: Any) -> Any:
        nonlocal call_count
        call_count += 1
        if kwargs.get("refresh_token"):
            raise M365Error("token rejected")
        return ("cc-token", None, future_expiry)

    logged_errors: list[tuple[str, dict[str, Any]]] = []

    def _capture_log_error(msg: str, **kwargs: Any) -> None:
        logged_errors.append((msg, kwargs))

    with (
        patch.object(m365_service, "get_credentials", AsyncMock(return_value=creds)),
        patch.object(m365_service.companies_repo, "get_company_csp_tenant_id", AsyncMock(return_value=None)),
        patch.object(m365_service, "_exchange_token", side_effect=_mock_exchange),
        patch.object(m365_service.m365_repo, "update_tokens", AsyncMock()),
        patch.object(m365_service, "_encrypt", lambda x: x),
        patch.object(m365_service, "log_error", side_effect=_capture_log_error),
        patch.object(m365_service, "log_info", lambda *a, **kw: None),
    ):
        result = await m365_service.acquire_access_token(7)

    assert result == "cc-token"

    fallback_errors = [
        (msg, kw)
        for msg, kw in logged_errors
        if msg == "M365 refresh token is invalid; falling back to client_credentials grant"
    ]
    assert fallback_errors, "Expected 'M365 refresh token is invalid; falling back to client_credentials grant' log"
    _, kw = fallback_errors[0]
    assert kw.get("company_id") == 7
    assert kw.get("tenant_id") == "tenant-abc"
    assert kw.get("client_id") == "client-abc"


# ---------------------------------------------------------------------------
# _exchange_token – failure logging includes tenant_id and client_id
# ---------------------------------------------------------------------------


@pytest.mark.anyio("asyncio")
async def test_exchange_token_failure_logs_tenant_and_client():
    """_exchange_token must include tenant_id, client_id, and grant_type in
    its error log so that multi-tenant 403 failures are traceable."""
    mock_response = MagicMock()
    mock_response.status_code = 400
    mock_response.text = '{"error":"invalid_client"}'

    mock_client = MagicMock()
    mock_client.post = AsyncMock(return_value=mock_response)
    mock_ctx = MagicMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_client)
    mock_ctx.__aexit__ = AsyncMock(return_value=None)

    logged_errors: list[tuple[str, dict[str, Any]]] = []

    def _capture(msg: str, **kwargs: Any) -> None:
        logged_errors.append((msg, kwargs))

    with (
        patch("app.services.m365.httpx.AsyncClient", return_value=mock_ctx),
        patch.object(m365_service, "log_error", side_effect=_capture),
    ):
        with pytest.raises(M365Error):
            await m365_service._exchange_token(
                tenant_id="test-tenant",
                client_id="test-client",
                client_secret="secret",
                refresh_token=None,
            )

    assert logged_errors, "Expected an error to be logged"
    msg, kw = logged_errors[0]
    assert kw.get("tenant_id") == "test-tenant"
    assert kw.get("client_id") == "test-client"
    assert kw.get("grant_type") == "client_credentials"
    assert kw.get("status") == 400


@pytest.mark.anyio("asyncio")
async def test_exchange_token_failure_logs_refresh_grant_type():
    """When the refresh_token grant fails, grant_type='refresh_token' is logged."""
    mock_response = MagicMock()
    mock_response.status_code = 401
    mock_response.text = '{"error":"invalid_grant"}'

    mock_client = MagicMock()
    mock_client.post = AsyncMock(return_value=mock_response)
    mock_ctx = MagicMock()
    mock_ctx.__aenter__ = AsyncMock(return_value=mock_client)
    mock_ctx.__aexit__ = AsyncMock(return_value=None)

    logged_errors: list[tuple[str, dict[str, Any]]] = []

    def _capture(msg: str, **kwargs: Any) -> None:
        logged_errors.append((msg, kwargs))

    with (
        patch("app.services.m365.httpx.AsyncClient", return_value=mock_ctx),
        patch.object(m365_service, "log_error", side_effect=_capture),
    ):
        with pytest.raises(M365Error):
            await m365_service._exchange_token(
                tenant_id="test-tenant",
                client_id="test-client",
                client_secret="secret",
                refresh_token="old-refresh",
            )

    assert logged_errors
    _, kw = logged_errors[0]
    assert kw.get("grant_type") == "refresh_token"


# ---------------------------------------------------------------------------
# sync_company_licenses – start log
# ---------------------------------------------------------------------------


@pytest.mark.anyio("asyncio")
async def test_sync_company_licenses_logs_start():
    """sync_company_licenses must emit a start log with company_id before
    calling the Graph API so that failures can be tied to a specific company."""
    logged_calls: list[tuple[str, dict[str, Any]]] = []

    def _capture_log_info(msg: str, **kwargs: Any) -> None:
        logged_calls.append((msg, kwargs))

    with (
        patch.object(m365_service, "acquire_access_token", AsyncMock(return_value="tok")),
        patch.object(
            m365_service,
            "_graph_get",
            AsyncMock(return_value={"value": []}),
        ),
        patch.object(m365_service, "log_info", side_effect=_capture_log_info),
    ):
        await m365_service.sync_company_licenses(99)

    start_logs = [
        (msg, kw)
        for msg, kw in logged_calls
        if msg == "M365 starting license synchronisation"
    ]
    assert start_logs, "Expected a 'M365 starting license synchronisation' log"
    _, kw = start_logs[0]
    assert kw.get("company_id") == 99


# ---------------------------------------------------------------------------
# _sync_staff_assignments – start log
# ---------------------------------------------------------------------------


@pytest.mark.anyio("asyncio")
async def test_sync_staff_assignments_logs_start():
    """_sync_staff_assignments must emit a start log with company_id,
    license_id, and sku_id to help trace per-SKU 403 failures."""
    logged_calls: list[tuple[str, dict[str, Any]]] = []

    def _capture_log_info(msg: str, **kwargs: Any) -> None:
        logged_calls.append((msg, kwargs))

    with (
        patch.object(m365_service, "_graph_get", AsyncMock(return_value={"value": []})),
        patch.object(m365_service.license_repo, "list_staff_for_license", AsyncMock(return_value=[])),
        patch.object(m365_service.license_repo, "bulk_unlink_staff", AsyncMock()),
        patch.object(m365_service, "log_info", side_effect=_capture_log_info),
    ):
        await m365_service._sync_staff_assignments(
            company_id=5,
            license_id=20,
            access_token="fake-token",
            sku_id="sku-1234",
        )

    start_logs = [
        (msg, kw)
        for msg, kw in logged_calls
        if msg == "M365 syncing staff assignments for license"
    ]
    assert start_logs, "Expected a 'M365 syncing staff assignments for license' log"
    _, kw = start_logs[0]
    assert kw.get("company_id") == 5
    assert kw.get("license_id") == 20
    assert kw.get("sku_id") == "sku-1234"
