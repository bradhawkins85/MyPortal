"""Tests for the CSP/Lighthouse customer listing service function."""
from __future__ import annotations

import pytest
from unittest.mock import patch
from typing import Any

from app.services import m365 as m365_service


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_contracts_page(
    items: list[dict[str, Any]],
    next_link: str | None = None,
) -> dict[str, Any]:
    response: dict[str, Any] = {"value": items}
    if next_link:
        response["@odata.nextLink"] = next_link
    return response


def _make_contract(
    *,
    customer_id: str = "tenant-123",
    display_name: str = "Contoso Ltd",
    default_domain: str = "contoso.onmicrosoft.com",
    contract_type: str = "Contract",
) -> dict[str, Any]:
    return {
        "customerId": customer_id,
        "displayName": display_name,
        "defaultDomainName": default_domain,
        "contractType": contract_type,
    }


# ---------------------------------------------------------------------------
# Tests for list_csp_customers
# ---------------------------------------------------------------------------


@pytest.mark.anyio("asyncio")
async def test_list_csp_customers_returns_sorted_list():
    """list_csp_customers returns contracts sorted by display name."""
    page = _make_contracts_page(
        [
            _make_contract(customer_id="tid-z", display_name="Zeta Corp"),
            _make_contract(customer_id="tid-a", display_name="Alpha Ltd"),
            _make_contract(customer_id="tid-m", display_name="Mid Inc"),
        ]
    )

    async def mock_graph_get(token: str, url: str) -> dict:
        return page

    with patch.object(m365_service, "_graph_get", side_effect=mock_graph_get):
        result = await m365_service.list_csp_customers("access-token")

    assert len(result) == 3
    assert result[0]["name"] == "Alpha Ltd"
    assert result[1]["name"] == "Mid Inc"
    assert result[2]["name"] == "Zeta Corp"


@pytest.mark.anyio("asyncio")
async def test_list_csp_customers_maps_fields():
    """list_csp_customers maps Graph fields to the expected output dict keys."""
    contract = _make_contract(
        customer_id="abc-123",
        display_name="Fabrikam",
        default_domain="fabrikam.onmicrosoft.com",
        contract_type="Contract",
    )

    async def mock_graph_get(token: str, url: str) -> dict:
        return _make_contracts_page([contract])

    with patch.object(m365_service, "_graph_get", side_effect=mock_graph_get):
        result = await m365_service.list_csp_customers("token")

    assert len(result) == 1
    customer = result[0]
    assert customer["tenant_id"] == "abc-123"
    assert customer["name"] == "Fabrikam"
    assert customer["default_domain"] == "fabrikam.onmicrosoft.com"
    assert customer["contract_type"] == "Contract"


@pytest.mark.anyio("asyncio")
async def test_list_csp_customers_skips_missing_customer_id():
    """Contracts without a customerId are silently skipped."""
    contracts = [
        {"customerId": None, "displayName": "No ID"},
        _make_contract(customer_id="valid-id", display_name="Valid Corp"),
    ]

    async def mock_graph_get(token: str, url: str) -> dict:
        return _make_contracts_page(contracts)

    with patch.object(m365_service, "_graph_get", side_effect=mock_graph_get):
        result = await m365_service.list_csp_customers("token")

    assert len(result) == 1
    assert result[0]["tenant_id"] == "valid-id"


@pytest.mark.anyio("asyncio")
async def test_list_csp_customers_paginates():
    """list_csp_customers follows @odata.nextLink to retrieve all pages."""
    page1 = _make_contracts_page(
        [_make_contract(customer_id="tid-1", display_name="Acme")],
        next_link="https://graph.microsoft.com/v1.0/contracts?$skiptoken=abc",
    )
    page2 = _make_contracts_page(
        [_make_contract(customer_id="tid-2", display_name="Beta Co")]
    )

    call_urls: list[str] = []

    async def mock_graph_get(token: str, url: str) -> dict:
        call_urls.append(url)
        if "skiptoken" in url:
            return page2
        return page1

    with patch.object(m365_service, "_graph_get", side_effect=mock_graph_get):
        result = await m365_service.list_csp_customers("token")

    assert len(result) == 2
    assert len(call_urls) == 2, "Should have made two Graph requests (one per page)"


@pytest.mark.anyio("asyncio")
async def test_list_csp_customers_empty():
    """list_csp_customers returns an empty list when there are no contracts."""
    async def mock_graph_get(token: str, url: str) -> dict:
        return {"value": []}

    with patch.object(m365_service, "_graph_get", side_effect=mock_graph_get):
        result = await m365_service.list_csp_customers("token")

    assert result == []


@pytest.mark.anyio("asyncio")
async def test_list_csp_customers_propagates_graph_error():
    """list_csp_customers propagates M365Error from _graph_get."""
    async def mock_graph_get(token: str, url: str) -> dict:
        raise m365_service.M365Error("Forbidden")

    with patch.object(m365_service, "_graph_get", side_effect=mock_graph_get):
        with pytest.raises(m365_service.M365Error, match="Forbidden"):
            await m365_service.list_csp_customers("token")


def test_csp_scope_constant():
    """CSP_SCOPE includes the required scopes for GDAP access."""
    assert "Directory.Read.All" in m365_service.CSP_SCOPE
    assert "openid" in m365_service.CSP_SCOPE
    assert "profile" in m365_service.CSP_SCOPE
    assert "offline_access" in m365_service.CSP_SCOPE


# ---------------------------------------------------------------------------
# Tests for verify_tenant_permissions
# ---------------------------------------------------------------------------


def _make_sp_list(sp_id: str = "sp-obj-id") -> dict[str, Any]:
    return {"value": [{"id": sp_id}]}


def _make_assignments(role_ids: list[str]) -> dict[str, Any]:
    return {"value": [{"appRoleId": rid} for rid in role_ids]}


@pytest.mark.anyio("asyncio")
async def test_verify_tenant_permissions_all_ok():
    """verify_tenant_permissions returns all_ok=True when all roles are assigned."""
    all_roles = list(m365_service._PROVISION_APP_ROLES)

    async def mock_graph_get(token: str, url: str) -> dict:
        if "appRoleAssignments" in url:
            return _make_assignments(all_roles)
        return _make_sp_list()

    async def mock_exchange(*, tenant_id, client_id, client_secret, refresh_token):
        return "access-token", None, None

    mock_creds = {
        "tenant_id": "tenant-abc",
        "client_id": "client-id",
        "client_secret": "secret",
    }

    with (
        patch.object(m365_service, "get_credentials", return_value=mock_creds),
        patch.object(m365_service, "_exchange_token", side_effect=mock_exchange),
        patch.object(m365_service, "_graph_get", side_effect=mock_graph_get),
    ):
        result = await m365_service.verify_tenant_permissions(company_id=1)

    assert result["all_ok"] is True
    assert result["missing"] == []
    assert result["updated"] is False


@pytest.mark.anyio("asyncio")
async def test_verify_tenant_permissions_missing_no_csp_session():
    """verify_tenant_permissions reports missing roles when no CSP session available."""
    present_roles = [m365_service._PROVISION_APP_ROLES[0]]

    async def mock_graph_get(token: str, url: str) -> dict:
        if "appRoleAssignments" in url:
            return _make_assignments(present_roles)
        return _make_sp_list()

    async def mock_exchange(*, tenant_id, client_id, client_secret, refresh_token):
        return "access-token", None, None

    mock_creds = {
        "tenant_id": "tenant-abc",
        "client_id": "client-id",
        "client_secret": "secret",
    }

    with (
        patch.object(m365_service, "get_credentials", return_value=mock_creds),
        patch.object(m365_service, "_exchange_token", side_effect=mock_exchange),
        patch.object(m365_service, "_graph_get", side_effect=mock_graph_get),
    ):
        result = await m365_service.verify_tenant_permissions(
            company_id=1, csp_access_token=None
        )

    assert result["all_ok"] is False
    assert len(result["missing"]) > 0
    assert result["updated"] is False
    assert "error" not in result


@pytest.mark.anyio("asyncio")
async def test_verify_tenant_permissions_raises_when_no_credentials():
    """verify_tenant_permissions raises M365Error when no credentials are stored."""
    with patch.object(m365_service, "get_credentials", return_value=None):
        with pytest.raises(m365_service.M365Error, match="No M365 credentials"):
            await m365_service.verify_tenant_permissions(company_id=99)


@pytest.mark.anyio("asyncio")
async def test_verify_tenant_permissions_raises_when_sp_not_found():
    """verify_tenant_permissions raises M365Error when service principal is absent."""
    async def mock_graph_get(token: str, url: str) -> dict:
        return {"value": []}

    async def mock_exchange(*, tenant_id, client_id, client_secret, refresh_token):
        return "access-token", None, None

    mock_creds = {
        "tenant_id": "tenant-abc",
        "client_id": "client-id",
        "client_secret": "secret",
    }

    with (
        patch.object(m365_service, "get_credentials", return_value=mock_creds),
        patch.object(m365_service, "_exchange_token", side_effect=mock_exchange),
        patch.object(m365_service, "_graph_get", side_effect=mock_graph_get),
    ):
        with pytest.raises(m365_service.M365Error, match="Service principal not found"):
            await m365_service.verify_tenant_permissions(company_id=1)


@pytest.mark.anyio("asyncio")
async def test_verify_tenant_permissions_grants_missing_and_returns_updated():
    """verify_tenant_permissions grants missing roles and returns updated=True."""
    present_roles = [m365_service._PROVISION_APP_ROLES[0]]
    graph_post_calls: list[str] = []

    async def mock_graph_get(token: str, url: str) -> dict:
        if "appRoleAssignments" in url:
            return _make_assignments(present_roles)
        # SP lookup for both the provisioned app and the Graph SP
        return {"value": [{"id": "sp-or-graph-sp-id"}]}

    async def mock_exchange(*, tenant_id, client_id, client_secret, refresh_token):
        return "access-token", None, None

    async def mock_graph_post(token: str, url: str, payload: dict) -> dict:
        graph_post_calls.append(url)
        return {"id": "new-assignment"}

    async def mock_obo(*, customer_tenant_id, client_id, client_secret, user_assertion):
        return "customer-token", None, None

    mock_creds = {
        "tenant_id": "tenant-abc",
        "client_id": "client-id",
        "client_secret": "secret",
    }
    mock_admin_creds = {
        "client_id": "admin-client-id",
        "client_secret": "admin-secret",
        "tenant_id": "partner-tenant",
    }

    with (
        patch.object(m365_service, "get_credentials", return_value=mock_creds),
        patch.object(m365_service, "_exchange_token", side_effect=mock_exchange),
        patch.object(m365_service, "_graph_get", side_effect=mock_graph_get),
        patch.object(m365_service, "_graph_post", side_effect=mock_graph_post),
        patch.object(m365_service, "_exchange_obo_token", side_effect=mock_obo),
        patch.object(
            m365_service, "get_admin_m365_credentials", return_value=mock_admin_creds
        ),
    ):
        result = await m365_service.verify_tenant_permissions(
            company_id=1, csp_access_token="csp-token"
        )

    assert result["all_ok"] is True
    assert result["updated"] is True
    assert result["missing"] == []
    assert len(graph_post_calls) > 0


@pytest.mark.anyio("asyncio")
async def test_verify_tenant_permissions_obo_failure_returns_error():
    """verify_tenant_permissions returns error when OBO token exchange fails."""
    present_roles = [m365_service._PROVISION_APP_ROLES[0]]

    async def mock_graph_get(token: str, url: str) -> dict:
        if "appRoleAssignments" in url:
            return _make_assignments(present_roles)
        return _make_sp_list()

    async def mock_exchange(*, tenant_id, client_id, client_secret, refresh_token):
        return "access-token", None, None

    async def mock_obo(*, customer_tenant_id, client_id, client_secret, user_assertion):
        raise m365_service.M365Error("OBO denied")

    mock_creds = {
        "tenant_id": "tenant-abc",
        "client_id": "client-id",
        "client_secret": "secret",
    }
    mock_admin_creds = {
        "client_id": "admin-client-id",
        "client_secret": "admin-secret",
        "tenant_id": "partner-tenant",
    }

    with (
        patch.object(m365_service, "get_credentials", return_value=mock_creds),
        patch.object(m365_service, "_exchange_token", side_effect=mock_exchange),
        patch.object(m365_service, "_graph_get", side_effect=mock_graph_get),
        patch.object(m365_service, "_exchange_obo_token", side_effect=mock_obo),
        patch.object(
            m365_service, "get_admin_m365_credentials", return_value=mock_admin_creds
        ),
    ):
        result = await m365_service.verify_tenant_permissions(
            company_id=1, csp_access_token="csp-token"
        )

    assert result["all_ok"] is False
    assert "error" in result
    assert "OBO denied" in result["error"]
    assert result["updated"] is False
