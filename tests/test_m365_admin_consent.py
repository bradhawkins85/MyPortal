from unittest.mock import AsyncMock, patch

import pytest

from app.services import m365


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_grant_required_admin_consent_assigns_roles_and_updates_existing_scope_grant():
    principal_id = "11111111-1111-1111-1111-111111111111"
    resource_id = "22222222-2222-2222-2222-222222222222"
    role_id = "33333333-3333-3333-3333-333333333333"
    grant_id = "44444444-4444-4444-4444-444444444444"

    graph_get = AsyncMock(side_effect=[
        {"value": []},
        {"value": [{"id": grant_id, "resourceId": resource_id, "scope": "Existing.Scope"}]},
    ])
    graph_post = AsyncMock(return_value={})
    graph_patch = AsyncMock(return_value={})
    with (
        patch.object(m365, "_graph_get", graph_get),
        patch.object(m365, "_post_app_role_assignment_with_retry", graph_post),
        patch.object(m365, "_graph_patch", graph_patch),
    ):
        changed = await m365._grant_required_admin_consent(
            "token",
            principal_id,
            [(resource_id, role_id)],
            [(resource_id, ["New.Scope", "Existing.Scope"])],
        )

    assert changed is True
    graph_post.assert_awaited_once()
    graph_patch.assert_awaited_once_with(
        "token",
        f"https://graph.microsoft.com/v1.0/oauth2PermissionGrants/{grant_id}",
        {"scope": "Existing.Scope New.Scope"},
    )


@pytest.mark.anyio
async def test_grant_required_admin_consent_is_idempotent_when_everything_is_granted():
    principal_id = "11111111-1111-1111-1111-111111111111"
    resource_id = "22222222-2222-2222-2222-222222222222"
    role_id = "33333333-3333-3333-3333-333333333333"
    grant_id = "44444444-4444-4444-4444-444444444444"

    with (
        patch.object(m365, "_graph_get", AsyncMock(side_effect=[
            {"value": [{"resourceId": resource_id, "appRoleId": role_id}]},
            {"value": [{"id": grant_id, "resourceId": resource_id, "scope": "Required.Scope"}]},
        ])),
        patch.object(m365, "_post_app_role_assignment_with_retry", AsyncMock()) as role_post,
        patch.object(m365, "_graph_post", AsyncMock()) as graph_post,
        patch.object(m365, "_graph_patch", AsyncMock()) as graph_patch,
    ):
        changed = await m365._grant_required_admin_consent(
            "token", principal_id, [(resource_id, role_id)], [(resource_id, ["Required.Scope"])]
        )

    assert changed is False
    role_post.assert_not_awaited()
    graph_post.assert_not_awaited()
    graph_patch.assert_not_awaited()


def test_admin_flows_request_permission_grant_write_scope():
    scope = "https://graph.microsoft.com/DelegatedPermissionGrant.ReadWrite.All"
    assert scope in m365.PROVISION_SCOPE
    assert scope in m365.CONNECT_SCOPE


@pytest.mark.anyio
async def test_grant_required_admin_consent_surfaces_failed_scope_update():
    principal_id = "11111111-1111-1111-1111-111111111111"
    resource_id = "22222222-2222-2222-2222-222222222222"
    grant_id = "44444444-4444-4444-4444-444444444444"
    with (
        patch.object(m365, "_graph_get", AsyncMock(side_effect=[
            {"value": []},
            {"value": [{"id": grant_id, "resourceId": resource_id, "scope": "Old.Scope"}]},
        ])),
        patch.object(m365, "_graph_patch", AsyncMock(side_effect=m365.M365Error("forbidden", http_status=403))),
    ):
        with pytest.raises(m365.M365Error, match="forbidden"):
            await m365._grant_required_admin_consent(
                "token", principal_id, [], [(resource_id, ["Required.Scope"])]
            )


@pytest.mark.anyio
async def test_grant_updates_opaque_oauth_id_and_follows_all_pages():
    principal_id = "11111111-1111-1111-1111-111111111111"
    resource_id = "22222222-2222-2222-2222-222222222222"
    opaque_id = "l5eW7x0ga0-WDOntXzHateQDNpSH5-lPk9HjD3Sarjk"
    graph_get = AsyncMock(side_effect=[
        {"value": [], "@odata.nextLink": "https://graph.microsoft.com/v1.0/servicePrincipals/next"},
        {"value": [{"resourceId": resource_id, "appRoleId": "existing-role"}]},
        {"value": [], "@odata.nextLink": "https://graph.microsoft.com/v1.0/oauth2PermissionGrants/next"},
        {"value": [{"id": opaque_id, "resourceId": resource_id, "scope": "Existing.Scope"}]},
    ])
    graph_patch = AsyncMock(return_value={})
    with (
        patch.object(m365, "_graph_get", graph_get),
        patch.object(m365, "_graph_patch", graph_patch),
    ):
        changed = await m365._grant_required_admin_consent(
            "token", principal_id, [], [(resource_id, ["New.Scope"])],
        )

    assert changed is True
    assert graph_get.await_count == 4
    graph_patch.assert_awaited_once_with(
        "token",
        f"https://graph.microsoft.com/v1.0/oauth2PermissionGrants/{opaque_id}",
        {"scope": "Existing.Scope New.Scope"},
    )
