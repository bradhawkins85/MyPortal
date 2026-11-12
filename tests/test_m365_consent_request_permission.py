"""Policy.ReadWrite.ConsentRequest must be on the MyPortal app registration
and granted, otherwise diagnostics reports it as not configured."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.services import m365 as m365_service
from app.services.m365_access_baseline import GRAPH_APPLICATION_PERMISSION_IDS

# Microsoft Graph permissions reference, Application identifier.
CONSENT_REQUEST_ROLE = "999f8c63-0a38-4f1b-91fd-ed1947bdd1a9"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def test_consent_request_permission_has_its_published_role_id():
    assert GRAPH_APPLICATION_PERMISSION_IDS["Policy.ReadWrite.ConsentRequest"] == CONSENT_REQUEST_ROLE
    assert CONSENT_REQUEST_ROLE in m365_service.get_required_app_role_ids()
    # Granted even when the tenant's Graph service principal projection omits it.
    assert CONSENT_REQUEST_ROLE in m365_service._FORCE_GRANT_GRAPH_APP_ROLES


@pytest.mark.anyio("asyncio")
async def test_provisioning_lists_and_grants_the_consent_request_permission():
    posted: list[dict] = []
    grant = AsyncMock()

    async def fake_post(token, url, payload, **kwargs):
        if url.endswith("/v1.0/applications"):
            posted.append(payload)
            return {"id": "app-object-id", "appId": "new-client-id"}
        if url.endswith("/v1.0/servicePrincipals"):
            return {"id": "sp-id"}
        if "addPassword" in url:
            return {"secretText": "s", "keyId": "k"}
        return {}

    with (
        patch.object(
            m365_service, "_graph_get",
            AsyncMock(return_value={"value": [{"id": "graph-sp-id", "appRoles": []}]}),
        ),
        patch.object(m365_service, "_graph_post", side_effect=fake_post),
        patch.object(m365_service, "_grant_provisioned_roles", grant),
    ):
        await m365_service.provision_app_registration(access_token="tok")

    graph_entry = next(
        entry for entry in posted[0]["requiredResourceAccess"]
        if entry["resourceAppId"] == m365_service._GRAPH_APP_ID
    )
    assert {"id": CONSENT_REQUEST_ROLE, "type": "Role"} in graph_entry["resourceAccess"]
    assert CONSENT_REQUEST_ROLE in grant.call_args.kwargs["valid_graph_roles"]


@pytest.mark.anyio("asyncio")
async def test_repair_lists_baseline_permissions_on_the_app_registration_before_granting():
    calls: list[str] = []

    def recorder(name, value):
        async def _inner(*args, **kwargs):
            calls.append(name)
            return value
        return _inner

    with (
        patch.object(m365_service, "get_credentials", AsyncMock(return_value={"client_id": "app-id", "tenant_id": "t"})),
        patch.object(m365_service, "acquire_delegated_token", AsyncMock(return_value="delegated")),
        patch.object(m365_service, "ensure_graph_app_roles_on_manifest", side_effect=recorder("graph_manifest", 1)) as graph_manifest,
        patch.object(m365_service, "ensure_exchange_manage_as_app_on_manifest", side_effect=recorder("exo_manifest", False)),
        patch.object(m365_service, "try_grant_missing_permissions", side_effect=recorder("grant", True)),
        patch.object(m365_service, "ensure_compliance_administrator_role", AsyncMock(return_value={"status": "existing"})),
        patch.object(m365_service, "check_enterprise_app_permissions", AsyncMock(return_value=[])),
        patch.object(m365_service, "run_purview_preflight", AsyncMock(return_value={"ready": True, "repaired": []})),
    ):
        await m365_service.repair_enterprise_app_permissions(1)

    graph_manifest.assert_awaited_once_with(1, "delegated")
    assert calls.index("graph_manifest") < calls.index("grant")
    assert calls.index("exo_manifest") < calls.index("grant")
