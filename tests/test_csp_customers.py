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
