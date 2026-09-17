"""Regression tests for the delegated Purview role remediation."""

from unittest.mock import AsyncMock, patch

import pytest

from app.services import m365


TENANT_ID = "11111111-1111-1111-1111-111111111111"
CLIENT_ID = "22222222-2222-2222-2222-222222222222"
PRINCIPAL_ID = "33333333-3333-3333-3333-333333333333"
ROLE_ID = "44444444-4444-4444-4444-444444444444"


def _credentials() -> dict[str, str]:
    return {"tenant_id": TENANT_ID, "client_id": CLIENT_ID}


def _get_responses(*, assignments: list[dict] | None = None):
    async def fake_get(_token: str, url: str) -> dict:
        if "/organization?" in url:
            return {"value": [{"id": TENANT_ID}]}
        if "/servicePrincipals?" in url:
            return {"value": [{"id": PRINCIPAL_ID, "appId": CLIENT_ID}]}
        if "/roleDefinitions?" in url:
            return {"value": [{
                "id": ROLE_ID,
                "displayName": "Compliance Administrator",
                "isBuiltIn": True,
            }]}
        if "/roleAssignments?" in url:
            return {"value": assignments or []}
        raise AssertionError(f"Unexpected URL: {url}")

    return fake_get


@pytest.mark.anyio("asyncio")
async def test_existing_compliance_assignment_is_success_without_duplicate():
    with (
        patch.object(m365, "get_credentials", AsyncMock(return_value=_credentials())),
        patch.object(m365, "_graph_get", side_effect=_get_responses(assignments=[{"id": "existing"}])),
        patch.object(m365, "_graph_post", new_callable=AsyncMock) as post,
    ):
        result = await m365.ensure_compliance_administrator_role(7, "delegated-token")

    assert result == {"status": "existing", "verified": True}
    post.assert_not_awaited()


@pytest.mark.anyio("asyncio")
async def test_missing_compliance_assignment_is_created_and_verified():
    verification_count = 0

    async def fake_get(token: str, url: str) -> dict:
        nonlocal verification_count
        response = await _get_responses()(token, url)
        if "/roleAssignments?" in url:
            verification_count += 1
            return {"value": [{"id": "new"}]} if verification_count > 1 else {"value": []}
        return response

    with (
        patch.object(m365, "get_credentials", AsyncMock(return_value=_credentials())),
        patch.object(m365, "_graph_get", side_effect=fake_get),
        patch.object(m365, "_graph_post", AsyncMock(return_value={"id": "new"})) as post,
    ):
        result = await m365.ensure_compliance_administrator_role(
            7, "delegated-token", verify_delay_seconds=0
        )

    assert result["status"] == "created"
    assert post.await_args.args[2] == {
        "principalId": PRINCIPAL_ID,
        "roleDefinitionId": ROLE_ID,
        "directoryScopeId": "/",
    }


@pytest.mark.anyio("asyncio")
async def test_incorrect_authenticated_tenant_is_rejected_before_changes():
    async def fake_get(_token: str, _url: str) -> dict:
        return {"value": [{"id": "99999999-9999-9999-9999-999999999999"}]}

    with (
        patch.object(m365, "get_credentials", AsyncMock(return_value=_credentials())),
        patch.object(m365, "_graph_get", side_effect=fake_get),
        patch.object(m365, "_graph_post", new_callable=AsyncMock) as post,
    ):
        with pytest.raises(m365.M365Error, match="configured for tenant"):
            await m365.ensure_compliance_administrator_role(7, "delegated-token")
    post.assert_not_awaited()


@pytest.mark.anyio("asyncio")
async def test_missing_service_principal_has_client_id_guidance():
    async def fake_get(token: str, url: str) -> dict:
        if "/servicePrincipals?" in url:
            return {"value": []}
        return await _get_responses()(token, url)

    with (
        patch.object(m365, "get_credentials", AsyncMock(return_value=_credentials())),
        patch.object(m365, "_graph_get", side_effect=fake_get),
    ):
        with pytest.raises(m365.M365Error, match="configured client ID"):
            await m365.ensure_compliance_administrator_role(7, "delegated-token")


@pytest.mark.anyio("asyncio")
async def test_insufficient_privileges_has_actionable_consent_guidance():
    with (
        patch.object(m365, "get_credentials", AsyncMock(return_value=_credentials())),
        patch.object(m365, "_graph_get", side_effect=_get_responses()),
        patch.object(
            m365,
            "_graph_post",
            AsyncMock(side_effect=m365.M365Error("forbidden", http_status=403)),
        ),
    ):
        with pytest.raises(m365.M365Error, match="RoleManagement.ReadWrite.Directory"):
            await m365.ensure_compliance_administrator_role(7, "delegated-token")


@pytest.mark.anyio("asyncio")
async def test_role_assignment_verification_retry_exhaustion_is_bounded():
    with (
        patch.object(m365, "get_credentials", AsyncMock(return_value=_credentials())),
        patch.object(m365, "_graph_get", side_effect=_get_responses()),
        patch.object(m365, "_graph_post", AsyncMock(return_value={"id": "new"})),
        patch.object(m365.asyncio, "sleep", new_callable=AsyncMock) as sleep,
    ):
        with pytest.raises(m365.M365Error, match="after 3 checks"):
            await m365.ensure_compliance_administrator_role(
                7, "delegated-token", verify_attempts=3, verify_delay_seconds=0
            )
    assert sleep.await_count == 2
