"""Tests for CSP tenant ID override in acquire_access_token.

When a company has ``csp_tenant_id`` set (the definitive client Azure AD
tenant ID sourced from Microsoft's /v1.0/contracts API via the CSP customer
mapping), ``acquire_access_token`` must use that tenant for the token
endpoint instead of the potentially incorrect ``tenant_id`` stored in
``company_m365_credentials``.

This prevents the sync_o365 scheduler from acquiring a token against the
CSP *partner* tenant and returning the partner's licenses instead of the
client's licenses.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from app.services import m365 as m365_service


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_creds(tenant_id: str = "csp-partner-tenant") -> dict[str, Any]:
    return {
        "company_id": 1,
        "tenant_id": tenant_id,
        "client_id": "admin-client-id",
        "client_secret": "admin-client-secret",
        "refresh_token": None,
        "access_token": None,
        "token_expires_at": None,
    }


def _make_company(csp_tenant_id: str | None = None) -> dict[str, Any]:
    return {
        "id": 1,
        "name": "Test Client",
        "csp_tenant_id": csp_tenant_id,
    }


def _fake_exchange(expected_tenant_id: str):
    """Return a fake _exchange_token that asserts the correct tenant is used."""
    async def _exchange(
        *,
        tenant_id: str,
        client_id: str,
        client_secret: str,
        refresh_token: str | None,
    ):
        assert tenant_id == expected_tenant_id, (
            f"Expected tenant {expected_tenant_id!r} but got {tenant_id!r}"
        )
        return "access-token", None, None

    return _exchange


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_acquire_uses_csp_tenant_id_when_stored_tenant_is_csp_partner(monkeypatch):
    """acquire_access_token uses csp_tenant_id when stored tenant_id is the CSP partner tenant.

    Scenario: provisioning went wrong and the CSP partner's tenant_id was
    stored in company_m365_credentials instead of the client's tenant.
    ``csp_tenant_id`` on the company record holds the correct client tenant.
    """
    client_tenant = "client-tenant-id"
    csp_partner_tenant = "csp-partner-tenant-id"

    creds = _make_creds(tenant_id=csp_partner_tenant)
    company = _make_company(csp_tenant_id=client_tenant)

    monkeypatch.setattr(m365_service.m365_repo, "get_credentials", AsyncMock(return_value=creds))
    monkeypatch.setattr(m365_service.m365_repo, "update_tokens", AsyncMock())
    monkeypatch.setattr(m365_service.companies_repo, "get_company_by_id", AsyncMock(return_value=company))

    with patch.object(m365_service, "_exchange_token", side_effect=_fake_exchange(client_tenant)):
        token = await m365_service.acquire_access_token(1)

    assert token == "access-token"


@pytest.mark.anyio
async def test_acquire_uses_stored_tenant_when_csp_tenant_id_matches(monkeypatch):
    """acquire_access_token uses stored tenant_id when it already equals csp_tenant_id.

    This is the happy-path: credentials were correctly provisioned and the
    stored tenant_id already points to the client tenant.
    """
    client_tenant = "client-tenant-id"

    creds = _make_creds(tenant_id=client_tenant)
    company = _make_company(csp_tenant_id=client_tenant)

    monkeypatch.setattr(m365_service.m365_repo, "get_credentials", AsyncMock(return_value=creds))
    monkeypatch.setattr(m365_service.m365_repo, "update_tokens", AsyncMock())
    monkeypatch.setattr(m365_service.companies_repo, "get_company_by_id", AsyncMock(return_value=company))

    with patch.object(m365_service, "_exchange_token", side_effect=_fake_exchange(client_tenant)):
        token = await m365_service.acquire_access_token(1)

    assert token == "access-token"


@pytest.mark.anyio
async def test_acquire_uses_stored_tenant_when_no_csp_tenant_id(monkeypatch):
    """acquire_access_token uses stored tenant_id when csp_tenant_id is not set.

    Companies without a CSP relationship should behave exactly as before.
    """
    stored_tenant = "direct-tenant-id"

    creds = _make_creds(tenant_id=stored_tenant)
    company = _make_company(csp_tenant_id=None)

    monkeypatch.setattr(m365_service.m365_repo, "get_credentials", AsyncMock(return_value=creds))
    monkeypatch.setattr(m365_service.m365_repo, "update_tokens", AsyncMock())
    monkeypatch.setattr(m365_service.companies_repo, "get_company_by_id", AsyncMock(return_value=company))

    with patch.object(m365_service, "_exchange_token", side_effect=_fake_exchange(stored_tenant)):
        token = await m365_service.acquire_access_token(1)

    assert token == "access-token"


@pytest.mark.anyio
async def test_acquire_uses_stored_tenant_when_company_not_found(monkeypatch):
    """acquire_access_token uses stored tenant_id when the company record is missing."""
    stored_tenant = "direct-tenant-id"

    creds = _make_creds(tenant_id=stored_tenant)

    monkeypatch.setattr(m365_service.m365_repo, "get_credentials", AsyncMock(return_value=creds))
    monkeypatch.setattr(m365_service.m365_repo, "update_tokens", AsyncMock())
    monkeypatch.setattr(m365_service.companies_repo, "get_company_by_id", AsyncMock(return_value=None))

    with patch.object(m365_service, "_exchange_token", side_effect=_fake_exchange(stored_tenant)):
        token = await m365_service.acquire_access_token(1)

    assert token == "access-token"


@pytest.mark.anyio
async def test_acquire_raises_when_no_credentials(monkeypatch):
    """acquire_access_token raises M365Error when no credentials are configured."""
    monkeypatch.setattr(m365_service.m365_repo, "get_credentials", AsyncMock(return_value=None))

    with pytest.raises(m365_service.M365Error, match="credentials have not been configured"):
        await m365_service.acquire_access_token(1)
