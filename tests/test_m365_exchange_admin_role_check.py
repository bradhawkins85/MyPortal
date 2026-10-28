"""Permission check must verify the Exchange RBAC role, not only Exchange.ManageAsApp."""

from unittest.mock import AsyncMock, patch

import pytest

from app.services import m365 as m365_service

SP_OBJECT_ID = "11111111-1111-1111-1111-111111111111"
EXO_SP_ID = "22222222-2222-2222-2222-222222222222"


def _mock_graph_get(role_assignments: list[dict] | Exception):
    async def mock_get(token: str, url: str) -> dict:
        if "roleManagement/directory/roleAssignments" in url:
            if isinstance(role_assignments, Exception):
                raise role_assignments
            return {"value": role_assignments}
        if "/appRoleAssignments" in url:
            return {"value": [{
                "appRoleId": m365_service._EXO_MANAGE_AS_APP_ROLE,
                "resourceId": EXO_SP_ID,
            }]}
        if f"servicePrincipals/{EXO_SP_ID}" in url:
            return {"appId": m365_service._EXO_APP_ID}
        if "?$filter=appId eq" in url:
            return {"value": [{"id": SP_OBJECT_ID}]}
        return {}

    return mock_get


async def _run_check(role_assignments: list[dict] | Exception) -> dict:
    with (
        patch.object(m365_service, "get_credentials", AsyncMock(return_value={
            "tenant_id": "tenant-1", "client_id": "app-client-id", "client_secret": "secret",
        })),
        patch.object(m365_service, "_exchange_token", AsyncMock(return_value=("token", None, None))),
        patch.object(m365_service, "_graph_get", side_effect=_mock_graph_get(role_assignments)),
        patch.object(
            m365_service, "_get_sp_app_role_ids",
            AsyncMock(return_value=(EXO_SP_ID, {m365_service._EXO_MANAGE_AS_APP_ROLE})),
        ),
        patch.object(m365_service.m365_repo, "upsert_permission_check_result", AsyncMock()),
    ):
        results = await m365_service.check_enterprise_app_permissions(1)
    return next(app for app in results if app["app_id"] == m365_service._EXO_APP_ID)


def _status(exo_result: dict, permission_id: str) -> str:
    return next(p["status"] for p in exo_result["permissions"] if p["id"] == permission_id)


@pytest.mark.anyio("asyncio")
async def test_check_fails_when_manage_as_app_granted_but_exchange_role_missing():
    exo = await _run_check([])

    assert _status(exo, m365_service._EXO_MANAGE_AS_APP_ROLE) == "pass"
    assert _status(exo, m365_service._EXO_ADMIN_ROLE_CHECK_ID) == "fail"
    assert exo["all_ok"] is False


@pytest.mark.anyio("asyncio")
@pytest.mark.parametrize(
    "role_id",
    [m365_service._EXO_ADMIN_ROLE_TEMPLATE_ID, m365_service._GLOBAL_ADMIN_ROLE_TEMPLATE_ID],
)
async def test_check_passes_with_exchange_or_global_admin_role(role_id: str):
    exo = await _run_check([{"roleDefinitionId": role_id}])

    assert _status(exo, m365_service._EXO_ADMIN_ROLE_CHECK_ID) == "pass"
    assert exo["all_ok"] is True


@pytest.mark.anyio("asyncio")
async def test_check_reports_fail_when_role_lookup_errors():
    exo = await _run_check(m365_service.M365Error("Forbidden", http_status=403))

    assert _status(exo, m365_service._EXO_ADMIN_ROLE_CHECK_ID) == "fail"
