"""Tests that sync_o365 uses the customer's CSP tenant ID for token acquisition.

The bug: when a company's M365 credentials were set up using a CSP/shared admin
app (registered in the partner/parent tenant), the tenant_id stored in
company_m365_credentials could be the partner tenant rather than the customer
tenant.  acquire_access_token must prefer companies.csp_tenant_id when it is
set so that the Graph API token is scoped to the correct customer tenant.
"""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch
from typing import Any

from app.services import m365 as m365_service
from app.repositories import companies as companies_repo


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


_PARTNER_TENANT = "partner-tenant-id"
_CUSTOMER_TENANT = "customer-tenant-id"
_CLIENT_ID = "csp-admin-client-id"
_CLIENT_SECRET = "csp-admin-secret"


def _make_creds(tenant_id: str = _PARTNER_TENANT) -> dict[str, Any]:
    return {
        "tenant_id": tenant_id,
        "client_id": _CLIENT_ID,
        "client_secret": _CLIENT_SECRET,
        "refresh_token": None,
        "access_token": None,
        "token_expires_at": None,
    }


# ---------------------------------------------------------------------------
# Tests for acquire_access_token – CSP tenant override
# ---------------------------------------------------------------------------


@pytest.mark.anyio("asyncio")
async def test_acquire_access_token_uses_csp_tenant_id_when_set():
    """acquire_access_token uses csp_tenant_id from companies when it is set.

    This simulates the bug scenario: credentials have the partner tenant ID
    stored but the company has a mapped csp_tenant_id (customer tenant).
    The token endpoint should use the customer's tenant, not the partner's.
    """
    captured_tenant_ids: list[str] = []

    async def mock_exchange_token(
        *,
        tenant_id: str,
        client_id: str,
        client_secret: str,
        refresh_token: Any,
    ) -> tuple[str, None, None]:
        captured_tenant_ids.append(tenant_id)
        return "access-token-for-customer", None, None

    with (
        patch.object(
            m365_service,
            "get_credentials",
            AsyncMock(return_value=_make_creds(tenant_id=_PARTNER_TENANT)),
        ),
        patch.object(
            companies_repo,
            "get_company_csp_tenant_id",
            AsyncMock(return_value=_CUSTOMER_TENANT),
        ),
        patch.object(
            m365_service,
            "_exchange_token",
            side_effect=mock_exchange_token,
        ),
        patch.object(
            m365_service.m365_repo,
            "update_tokens",
            AsyncMock(),
        ),
    ):
        token = await m365_service.acquire_access_token(42)

    assert token == "access-token-for-customer"
    assert len(captured_tenant_ids) == 1
    # Must use the customer's tenant, not the partner's
    assert captured_tenant_ids[0] == _CUSTOMER_TENANT


@pytest.mark.anyio("asyncio")
async def test_acquire_access_token_uses_credential_tenant_when_no_csp_tenant():
    """acquire_access_token falls back to creds tenant_id when csp_tenant_id is None.

    For companies not managed via CSP, the csp_tenant_id is NULL and the
    tenant_id from company_m365_credentials is the correct tenant to use.
    """
    captured_tenant_ids: list[str] = []

    async def mock_exchange_token(
        *,
        tenant_id: str,
        client_id: str,
        client_secret: str,
        refresh_token: Any,
    ) -> tuple[str, None, None]:
        captured_tenant_ids.append(tenant_id)
        return "access-token-standalone", None, None

    with (
        patch.object(
            m365_service,
            "get_credentials",
            AsyncMock(return_value=_make_creds(tenant_id=_CUSTOMER_TENANT)),
        ),
        patch.object(
            companies_repo,
            "get_company_csp_tenant_id",
            AsyncMock(return_value=None),
        ),
        patch.object(
            m365_service,
            "_exchange_token",
            side_effect=mock_exchange_token,
        ),
        patch.object(
            m365_service.m365_repo,
            "update_tokens",
            AsyncMock(),
        ),
    ):
        token = await m365_service.acquire_access_token(99)

    assert token == "access-token-standalone"
    assert len(captured_tenant_ids) == 1
    # Uses the tenant from credentials when no CSP mapping exists
    assert captured_tenant_ids[0] == _CUSTOMER_TENANT


@pytest.mark.anyio("asyncio")
async def test_sync_company_licenses_scopes_token_to_csp_customer_tenant():
    """sync_company_licenses calls acquire_access_token which uses csp_tenant_id.

    End-to-end check: when a company has csp_tenant_id set, the Graph token
    used by sync_company_licenses is obtained for the customer tenant.
    """
    captured_tenant_ids: list[str] = []

    async def mock_exchange_token(
        *,
        tenant_id: str,
        client_id: str,
        client_secret: str,
        refresh_token: Any,
    ) -> tuple[str, None, None]:
        captured_tenant_ids.append(tenant_id)
        return "customer-scoped-token", None, None

    graph_response = {"value": []}

    with (
        patch.object(
            m365_service,
            "get_credentials",
            AsyncMock(return_value=_make_creds(tenant_id=_PARTNER_TENANT)),
        ),
        patch.object(
            companies_repo,
            "get_company_csp_tenant_id",
            AsyncMock(return_value=_CUSTOMER_TENANT),
        ),
        patch.object(
            m365_service,
            "_exchange_token",
            side_effect=mock_exchange_token,
        ),
        patch.object(
            m365_service.m365_repo,
            "update_tokens",
            AsyncMock(),
        ),
        patch.object(
            m365_service,
            "_graph_get",
            AsyncMock(return_value=graph_response),
        ),
    ):
        await m365_service.sync_company_licenses(42)

    assert len(captured_tenant_ids) == 1
    assert captured_tenant_ids[0] == _CUSTOMER_TENANT
