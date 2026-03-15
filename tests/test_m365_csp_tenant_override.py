"""Tests for CSP tenant ID override in acquire_access_token.

When a company has ``csp_tenant_id`` set (the definitive client Azure AD
tenant ID sourced from Microsoft's /v1.0/contracts API via the CSP customer
mapping), ``acquire_access_token`` must use that tenant for the token
endpoint instead of the potentially incorrect ``tenant_id`` stored in
``company_m365_credentials``.

This prevents the sync_o365 scheduler from acquiring a token against the
CSP *partner* tenant and returning the partner's licenses instead of the
client's licenses.

When the CSP tenant override is applied and the stored client_id matches the
admin CSP app, the current admin credentials are used so that any secret
rotation is automatically picked up without requiring per-company credential
updates.
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

_ADMIN_CLIENT_ID = "admin-client-id"
_ADMIN_CLIENT_SECRET = "admin-client-secret"
_ADMIN_CLIENT_SECRET_ROTATED = "admin-client-secret-rotated"


def _make_creds(
    tenant_id: str = "csp-partner-tenant",
    client_id: str = _ADMIN_CLIENT_ID,
    client_secret: str = _ADMIN_CLIENT_SECRET,
) -> dict[str, Any]:
    return {
        "company_id": 1,
        "tenant_id": tenant_id,
        "client_id": client_id,
        "client_secret": client_secret,
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


def _make_admin_creds(
    client_id: str = _ADMIN_CLIENT_ID,
    client_secret: str = _ADMIN_CLIENT_SECRET,
) -> dict[str, Any]:
    return {
        "client_id": client_id,
        "client_secret": client_secret,
        "tenant_id": "partner-tenant",
        "app_object_id": None,
        "client_secret_key_id": None,
        "client_secret_expires_at": None,
    }


def _fake_exchange(expected_tenant_id: str, expected_client_secret: str | None = None):
    """Return a fake _exchange_token that asserts the correct tenant/secret is used."""
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
        if expected_client_secret is not None:
            assert client_secret == expected_client_secret, (
                f"Expected client_secret {expected_client_secret!r} but got {client_secret!r}"
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
    # Admin creds have a different client_id -> stored credentials used as-is
    monkeypatch.setattr(
        m365_service, "get_admin_m365_credentials",
        AsyncMock(return_value=_make_admin_creds(client_id="different-admin-id")),
    )

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
    # No override triggered, so get_admin_m365_credentials is never called.
    # We still provide a mock to guard against accidental calls.
    monkeypatch.setattr(
        m365_service, "get_admin_m365_credentials",
        AsyncMock(return_value=None),
    )

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
    monkeypatch.setattr(
        m365_service, "get_admin_m365_credentials",
        AsyncMock(return_value=None),
    )

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


@pytest.mark.anyio
async def test_acquire_uses_current_admin_secret_when_csp_override_and_client_id_matches(monkeypatch):
    """acquire_access_token uses current admin secret when CSP override is active and client_ids match.

    Scenario: A CSP company's stored credentials use the admin app's client_id
    but the stored client_secret is stale (e.g. after an admin secret rotation).
    The override is triggered because csp_tenant_id differs from stored tenant_id.
    The current admin secret from get_admin_m365_credentials() should be used
    instead of the stale stored secret.
    """
    client_tenant = "customer-tenant-id"
    partner_tenant = "partner-tenant-id"

    creds = _make_creds(tenant_id=partner_tenant, client_secret=_ADMIN_CLIENT_SECRET)
    company = _make_company(csp_tenant_id=client_tenant)
    # Simulate that the admin secret has been rotated since the company record was written
    admin_creds = _make_admin_creds(client_secret=_ADMIN_CLIENT_SECRET_ROTATED)

    monkeypatch.setattr(m365_service.m365_repo, "get_credentials", AsyncMock(return_value=creds))
    monkeypatch.setattr(m365_service.m365_repo, "update_tokens", AsyncMock())
    monkeypatch.setattr(m365_service.companies_repo, "get_company_by_id", AsyncMock(return_value=company))
    monkeypatch.setattr(
        m365_service, "get_admin_m365_credentials",
        AsyncMock(return_value=admin_creds),
    )

    with patch.object(
        m365_service,
        "_exchange_token",
        side_effect=_fake_exchange(client_tenant, expected_client_secret=_ADMIN_CLIENT_SECRET_ROTATED),
    ):
        token = await m365_service.acquire_access_token(1)

    assert token == "access-token"


@pytest.mark.anyio
async def test_acquire_keeps_stored_secret_when_csp_override_but_client_id_differs(monkeypatch):
    """acquire_access_token keeps stored credentials when client_ids differ on CSP override.

    Scenario: The company has a per-company app (different from the admin app)
    but tenant_id was incorrectly recorded as the partner tenant. The override
    is triggered. Because the stored client_id does NOT match the admin app,
    the stored credentials are used unchanged.
    """
    client_tenant = "customer-tenant-id"
    partner_tenant = "partner-tenant-id"
    per_company_client_id = "per-company-app-id"
    per_company_secret = "per-company-secret"

    creds = _make_creds(
        tenant_id=partner_tenant,
        client_id=per_company_client_id,
        client_secret=per_company_secret,
    )
    company = _make_company(csp_tenant_id=client_tenant)
    # Admin app has a different client_id
    admin_creds = _make_admin_creds(client_id=_ADMIN_CLIENT_ID)

    monkeypatch.setattr(m365_service.m365_repo, "get_credentials", AsyncMock(return_value=creds))
    monkeypatch.setattr(m365_service.m365_repo, "update_tokens", AsyncMock())
    monkeypatch.setattr(m365_service.companies_repo, "get_company_by_id", AsyncMock(return_value=company))
    monkeypatch.setattr(
        m365_service, "get_admin_m365_credentials",
        AsyncMock(return_value=admin_creds),
    )

    with patch.object(
        m365_service,
        "_exchange_token",
        side_effect=_fake_exchange(client_tenant, expected_client_secret=per_company_secret),
    ):
        token = await m365_service.acquire_access_token(1)

    assert token == "access-token"


@pytest.mark.anyio
async def test_acquire_keeps_stored_credentials_when_csp_override_and_no_admin_creds(monkeypatch):
    """acquire_access_token keeps stored credentials when no admin creds are configured.

    Scenario: CSP override is triggered but the m365-admin module is not
    configured. The stored credentials should be used unchanged.
    """
    client_tenant = "customer-tenant-id"
    partner_tenant = "partner-tenant-id"

    creds = _make_creds(tenant_id=partner_tenant)
    company = _make_company(csp_tenant_id=client_tenant)

    monkeypatch.setattr(m365_service.m365_repo, "get_credentials", AsyncMock(return_value=creds))
    monkeypatch.setattr(m365_service.m365_repo, "update_tokens", AsyncMock())
    monkeypatch.setattr(m365_service.companies_repo, "get_company_by_id", AsyncMock(return_value=company))
    monkeypatch.setattr(
        m365_service, "get_admin_m365_credentials",
        AsyncMock(return_value=None),
    )

    with patch.object(
        m365_service,
        "_exchange_token",
        side_effect=_fake_exchange(client_tenant, expected_client_secret=_ADMIN_CLIENT_SECRET),
    ):
        token = await m365_service.acquire_access_token(1)

    assert token == "access-token"
