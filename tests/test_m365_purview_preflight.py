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
        if "/transitiveMemberOf/" in url:
            return {"value": [{
                "displayName": "Compliance Administrator",
                "roleTemplateId": m365._COMPLIANCE_ADMIN_ROLE_TEMPLATE_ID,
            }]}
        if "/servicePrincipals?" in url:
            return {"value": [{"id": object_id, "appId": client_id}]}
        raise AssertionError(url)

    async def invoke(_token, _tenant, command, _parameters=None, **_kwargs):
        if command == "Get-ServicePrincipal":
            return {"value": [{"ObjectId": object_id, "AppId": client_id}]}
        if command == "Get-RoleGroup":
            return {"value": [{"Members": [object_id]}]}
        raise AssertionError(command)

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

    assert result["ready"] is False
    assert [item["key"] for item in result["checks"]] == [
        "provider_support", "eop_permission", "admin_consent", "administrator_role",
        "organization", "service_principal", "ediscovery_manager",
        "tenant_license", "search_rbac", "purge_rbac",
    ]
    assert result["provider_supported"] is False


@pytest.mark.anyio("asyncio")
async def test_preflight_missing_eop_consent_reports_failed_live_purview_probe():
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
        if "/transitiveMemberOf/" in url:
            return {"value": [{
                "displayName": "Compliance Administrator",
                "roleTemplateId": m365._COMPLIANCE_ADMIN_ROLE_TEMPLATE_ID,
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
        patch.object(
            m365, "_acquire_scc_access_token",
            AsyncMock(side_effect=m365.M365Error("invalid_client", http_status=401)),
        ),
        patch.object(m365, "_scc_invoke_command", new_callable=AsyncMock) as invoke,
    ):
        result = await m365.run_purview_preflight(7)

    invoke.assert_not_awaited()
    by_key = {item["key"]: item for item in result["checks"]}
    assert by_key["eop_permission"]["status"] == "Requires Admin Action"
    assert "Office 365 Exchange Online" in by_key["eop_permission"]["detail"]
    assert by_key["organization"]["status"] == "Failed"


@pytest.mark.anyio("asyncio")
async def test_preflight_uses_live_purview_probe_when_graph_assignment_is_stale():
    client_id = "22222222-2222-2222-2222-222222222222"
    object_id = "33333333-3333-3333-3333-333333333333"

    async def graph_get(_token: str, url: str):
        if "/domains?" in url:
            return {"value": [{"id": "contoso.onmicrosoft.com", "isInitial": True}]}
        if "/applications/" in url:
            raise m365.M365Error("Insufficient privileges", http_status=403)
        if "/appRoleAssignments" in url:
            return {"value": []}
        if "/transitiveMemberOf/" in url:
            return {"value": [{
                "displayName": "Compliance Administrator",
                "roleTemplateId": m365._COMPLIANCE_ADMIN_ROLE_TEMPLATE_ID,
            }]}
        if "/servicePrincipals?" in url:
            return {"value": [{"id": object_id, "appId": client_id}]}
        raise AssertionError(url)

    async def invoke(_token, _tenant, command, _parameters=None, **_kwargs):
        if command == "Get-ServicePrincipal":
            return {"value": [{"ObjectId": object_id, "AppId": client_id}]}
        if command == "Get-RoleGroup":
            return {"value": [{"Members": [object_id]}]}
        raise AssertionError(command)

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

    assert result["ready"] is False
    assert {item["status"] for item in result["checks"]} == {"Passed", "Unsupported", "Not Verified"}


@pytest.mark.anyio("asyncio")
async def test_preflight_accepts_eop_assignment_when_manifest_cannot_be_read():
    client_id = "22222222-2222-2222-2222-222222222222"
    object_id = "33333333-3333-3333-3333-333333333333"
    eop_object_id = "44444444-4444-4444-4444-444444444444"

    async def graph_get(_token: str, url: str):
        if "/domains?" in url:
            return {"value": [{"id": "contoso.onmicrosoft.com", "isInitial": True}]}
        if "/applications/" in url:
            raise m365.M365Error("Insufficient privileges", http_status=403)
        if "/appRoleAssignments" in url:
            return {"value": [{
                "appRoleId": m365._SCC_MANAGE_AS_APP_ROLE,
                "resourceId": eop_object_id,
            }]}
        if f"servicePrincipals/{eop_object_id}" in url:
            return {"appId": m365._SCC_APP_ID}
        if "/transitiveMemberOf/" in url:
            return {"value": [{
                "displayName": "Compliance Administrator",
                "roleTemplateId": m365._COMPLIANCE_ADMIN_ROLE_TEMPLATE_ID,
            }]}
        if "/servicePrincipals?" in url:
            return {"value": [{"id": object_id, "appId": client_id}]}
        raise AssertionError(url)

    async def invoke(_token, _tenant, command, _parameters=None, **_kwargs):
        if command == "Get-ServicePrincipal":
            return {"value": [{"ObjectId": object_id}]}
        if command == "Get-RoleGroup":
            return {"value": [{"Members": [object_id]}]}
        return {"value": [{"Name": "contoso"}]}

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

    by_key = {item["key"]: item for item in result["checks"]}
    assert by_key["eop_permission"]["status"] == "Passed"
    assert by_key["admin_consent"]["status"] == "Passed"


@pytest.mark.anyio("asyncio")
async def test_preflight_avoids_broken_role_group_member_cmdlet():
    client_id = "22222222-2222-2222-2222-222222222222"
    object_id = "33333333-3333-3333-3333-333333333333"

    async def graph_get(_token: str, url: str):
        if "/domains?" in url:
            return {"value": [{"id": "contoso.onmicrosoft.com", "isInitial": True}]}
        if "/applications/" in url:
            return {"requiredResourceAccess": []}
        if "/appRoleAssignments" in url:
            return {"value": []}
        if "/transitiveMemberOf/" in url:
            return {"value": [{
                "displayName": "Compliance Administrator",
                "roleTemplateId": m365._COMPLIANCE_ADMIN_ROLE_TEMPLATE_ID,
            }]}
        if "/servicePrincipals?" in url:
            return {"value": [{"id": object_id, "appId": client_id}]}
        raise AssertionError(url)

    commands: list[str] = []

    async def invoke(_token, _tenant, command, parameters=None, **_kwargs):
        commands.append(command)
        if command == "Get-RoleGroup":
            assert parameters == {"Identity": "eDiscoveryManager"}
            return {"value": [{"Members": [{"ExternalDirectoryObjectId": object_id}]}]}
        if command == "Get-ServicePrincipal":
            assert parameters == {"Identity": object_id}
            return {"value": [{"ObjectId": object_id, "AppId": client_id}]}
        raise AssertionError(command)

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

    assert result["ready"] is False
    assert commands == ["Get-RoleGroup", "Get-ServicePrincipal"]
    assert "Get-RoleGroupMember" not in commands


@pytest.mark.anyio("asyncio")
async def test_preflight_scopes_service_principal_internal_error_to_registration():
    client_id = "22222222-2222-2222-2222-222222222222"
    object_id = "33333333-3333-3333-3333-333333333333"

    async def graph_get(_token: str, url: str):
        if "/domains?" in url:
            return {"value": [{"id": "contoso.onmicrosoft.com", "isInitial": True}]}
        if "/applications/" in url:
            return {"requiredResourceAccess": []}
        if "/appRoleAssignments" in url:
            return {"value": []}
        if "/transitiveMemberOf/" in url:
            return {"value": [{
                "displayName": "Compliance Administrator",
                "roleTemplateId": m365._COMPLIANCE_ADMIN_ROLE_TEMPLATE_ID,
            }]}
        if "/servicePrincipals?" in url:
            return {"value": [{"id": object_id, "appId": client_id}]}
        raise AssertionError(url)

    async def invoke(_token, _tenant, command, parameters=None, **_kwargs):
        if command == "Get-RoleGroup":
            return {"value": [{"Members": []}]}
        if command == "Get-ServicePrincipal":
            assert parameters == {"Identity": object_id}
            raise m365.M365Error(
                "Security & Compliance Get-ServicePrincipal failed (500): "
                "System.ArgumentNullException",
                http_status=500,
            )
        raise AssertionError(command)

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

    checks = {check["key"]: check for check in result["checks"]}
    assert checks["eop_permission"]["status"] == "Passed"
    assert checks["admin_consent"]["status"] == "Passed"
    assert checks["organization"]["status"] == "Passed"
    assert checks["service_principal"]["status"] == "Requires Admin Action"
    assert checks["ediscovery_manager"]["status"] == "Requires Admin Action"


@pytest.mark.anyio("asyncio")
async def test_preflight_repair_registers_principal_and_adds_role_member():
    client_id = "22222222-2222-2222-2222-222222222222"
    object_id = "33333333-3333-3333-3333-333333333333"
    eop_object_id = "44444444-4444-4444-4444-444444444444"

    async def graph_get(_token: str, url: str):
        if "/domains?" in url:
            return {"value": [{"id": "contoso.onmicrosoft.com", "isInitial": True}]}
        if "/applications" in url:
            return {"requiredResourceAccess": []}
        if "/appRoleAssignments" in url:
            return {"value": [{"appRoleId": m365._SCC_MANAGE_AS_APP_ROLE, "resourceId": eop_object_id}]}
        if f"servicePrincipals/{eop_object_id}" in url:
            return {"appId": m365._SCC_APP_ID}
        if "/transitiveMemberOf/" in url:
            return {"value": [{
                "displayName": "Compliance Administrator",
                "roleTemplateId": m365._COMPLIANCE_ADMIN_ROLE_TEMPLATE_ID,
            }]}
        if "/servicePrincipals?" in url:
            return {"value": [{"id": object_id, "appId": client_id}]}
        raise AssertionError(url)

    commands: list[tuple[str, dict | None]] = []

    async def invoke(_token, _tenant, command, parameters=None, **_kwargs):
        commands.append((command, parameters))
        return {"value": []}

    with (
        patch.object(m365, "get_credentials", AsyncMock(return_value={
            "tenant_id": "11111111-1111-1111-1111-111111111111", "client_id": client_id,
        })),
        patch.object(m365, "acquire_access_token", AsyncMock(return_value="graph-token")),
        patch.object(m365, "_graph_get", side_effect=graph_get),
        patch.object(m365, "_acquire_scc_access_token", AsyncMock(return_value=("scc-token", "tenant"))),
        patch.object(m365, "_scc_invoke_command", side_effect=invoke),
    ):
        result = await m365.run_purview_preflight(7, repair=True)

    assert result["repaired"] == []
    assert not any(
        command in {"New-ServicePrincipal", "Add-RoleGroupMember"}
        for command, _parameters in commands
    )


@pytest.mark.anyio("asyncio")
async def test_preflight_reports_missing_enterprise_application_admin_role():
    client_id = "22222222-2222-2222-2222-222222222222"
    object_id = "33333333-3333-3333-3333-333333333333"

    async def graph_get(_token: str, url: str):
        if "/domains?" in url:
            return {"value": [{"id": "contoso.onmicrosoft.com", "isInitial": True}]}
        if "/applications/" in url:
            return {"requiredResourceAccess": []}
        if "/appRoleAssignments" in url or "/transitiveMemberOf/" in url:
            return {"value": []}
        if "/servicePrincipals?" in url:
            return {"value": [{"id": object_id, "appId": client_id}]}
        raise AssertionError(url)

    with (
        patch.object(m365, "get_credentials", AsyncMock(return_value={
            "tenant_id": "11111111-1111-1111-1111-111111111111",
            "client_id": client_id,
            "app_object_id": "55555555-5555-5555-5555-555555555555",
        })),
        patch.object(m365, "acquire_access_token", AsyncMock(return_value="graph-token")),
        patch.object(m365, "_graph_get", side_effect=graph_get),
        patch.object(m365, "_acquire_scc_access_token", AsyncMock(side_effect=m365.M365Error("unauthorized", http_status=403))),
    ):
        result = await m365.run_purview_preflight(7)

    check = next(item for item in result["checks"] if item["key"] == "administrator_role")
    assert check["status"] == "Requires Admin Action"
    assert "enterprise application" in check["detail"]
    assert "Compliance Administrator" in check["remediation"]
    assert result["ready"] is False
