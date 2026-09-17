"""Purview/eDiscovery prerequisite preflight regression tests."""

from unittest.mock import AsyncMock, patch

import pytest

from app.services import m365


@pytest.mark.anyio("asyncio")
async def test_preflight_distinguishes_eop_permission_and_reports_all_checks():
    client_id = "22222222-2222-2222-2222-222222222222"
    object_id = "33333333-3333-3333-3333-333333333333"
    eop_object_id = "44444444-4444-4444-4444-444444444444"

    async def graph_get(_token: str, url: str):
        if "/domains?" in url:
            return {"value": [{"id": "contoso.onmicrosoft.com", "isInitial": True}]}
        if "/applications/" in url:
            return {"requiredResourceAccess": [{
                "resourceAppId": m365._SCC_APP_ID,
                "resourceAccess": [{"id": m365._SCC_MANAGE_AS_APP_ROLE, "type": "Role"}],
            }]}
        if "/appRoleAssignments" in url:
            return {"value": [{
                "appRoleId": m365._SCC_MANAGE_AS_APP_ROLE, "resourceId": eop_object_id,
            }]}
        if f"servicePrincipals/{eop_object_id}" in url:
            return {"appId": m365._SCC_APP_ID}
        if "/servicePrincipals?" in url:
            return {"value": [{"id": object_id, "appId": client_id}]}
        raise AssertionError(url)

    async def invoke(_token, _tenant, command, _parameters=None, **_kwargs):
        if command == "Get-OrganizationConfig":
            return {"value": [{"Name": "contoso"}]}
        if command == "Get-ServicePrincipal":
            return {"value": [{"ObjectId": object_id, "AppId": client_id}]}
        return {"value": [{"ExternalDirectoryObjectId": object_id}]}

    with (
        patch.object(m365, "get_credentials", AsyncMock(return_value={
            "tenant_id": "11111111-1111-1111-1111-111111111111",
            "client_id": client_id,
            "app_object_id": "55555555-5555-5555-5555-555555555555",
        })),
        patch.object(m365, "acquire_access_token", AsyncMock(return_value="graph-token")),
        patch.object(m365, "_graph_get", side_effect=graph_get),
        patch.object(m365, "_acquire_scc_access_token", AsyncMock(return_value=("scc-token", "tenant"))),
        patch.object(m365, "_scc_invoke_command", side_effect=invoke),
    ):
        result = await m365.run_purview_preflight(7)

    assert result["ready"] is True
    assert [item["key"] for item in result["checks"]] == [
        "eop_permission", "admin_consent", "organization",
        "service_principal", "ediscovery_manager",
    ]
    assert {item["status"] for item in result["checks"]} == {"Passed"}


@pytest.mark.anyio("asyncio")
async def test_preflight_missing_eop_consent_does_not_probe_or_retry_purview():
    client_id = "22222222-2222-2222-2222-222222222222"

    async def graph_get(_token: str, url: str):
        if "/domains?" in url:
            return {"value": [{"id": "contoso.onmicrosoft.com", "isInitial": True}]}
        if "/applications/" in url:
            # The EXO app ID must not count as the EOP permission.
            return {"requiredResourceAccess": [{
                "resourceAppId": m365._EXO_APP_ID,
                "resourceAccess": [{"id": m365._EXO_MANAGE_AS_APP_ROLE, "type": "Role"}],
            }]}
        if "/servicePrincipals?" in url:
            return {"value": [{"id": "33333333-3333-3333-3333-333333333333"}]}
        if "/appRoleAssignments" in url:
            return {"value": []}
        raise AssertionError(url)

    with (
        patch.object(m365, "get_credentials", AsyncMock(return_value={
            "tenant_id": "11111111-1111-1111-1111-111111111111",
            "client_id": client_id,
            "app_object_id": "55555555-5555-5555-5555-555555555555",
        })),
        patch.object(m365, "acquire_access_token", AsyncMock(return_value="graph-token")),
        patch.object(m365, "_graph_get", side_effect=graph_get),
        patch.object(m365, "_scc_invoke_command", new_callable=AsyncMock) as invoke,
    ):
        result = await m365.run_purview_preflight(7)

    invoke.assert_not_awaited()
    by_key = {item["key"]: item for item in result["checks"]}
    assert by_key["eop_permission"]["status"] == "Requires Admin Action"
    assert "Office 365 Exchange Online" in by_key["eop_permission"]["detail"]
    assert by_key["organization"]["status"] == "Failed"
