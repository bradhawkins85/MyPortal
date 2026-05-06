"""Tests for try_grant_missing_permissions – the best-effort permission grant
that runs during the 'Authorize portal access' (connect) callback to ensure
newly-required permissions (e.g. Reports.Read.All, MailboxSettings.Read for
mailbox sync) are added to existing app registrations automatically.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from app.services import m365 as m365_service
from app.services.m365 import (
    _PROVISION_APP_ROLES,
    M365Error,
    _EXO_MANAGE_AS_APP_ROLE,
    _EXO_APP_ID,
    _TEAMS_MANAGE_AS_APP_ROLE,
    _TEAMS_APP_ID,
    _TEAMS_ADMIN_ROLE_TEMPLATE_ID,
    _SHAREPOINT_TENANT_SETTINGS_ROLE,
)
from tests.conftest import drain_provision_background_tasks


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_MAILBOX_REPORTS_ROLE = "230c1aed-a721-4c5d-9cb4-a90514e508ef"  # Reports.Read.All
_MAILBOX_SETTINGS_ROLE = "40f97065-369a-49f4-947c-6a255697ae91"  # MailboxSettings.Read
_MAIL_READWRITE_ROLE = "e2a3a72e-5f79-4c64-b1b1-878b674786c9"  # Mail.ReadWrite


def _fake_creds(client_id: str = "app-client-id") -> dict[str, Any]:
    return {"client_id": client_id, "tenant_id": "tenant-123"}


def _sp_response(sp_id: str = "sp-object-id") -> dict[str, Any]:
    return {"value": [{"id": sp_id}]}


def _teams_sp_response(sp_id: str = "teams-sp-id") -> dict[str, Any]:
    """Teams SP response including the ManageAsApp appRole so role-existence check passes."""
    return {"value": [{"id": sp_id, "appRoles": [{"id": _TEAMS_MANAGE_AS_APP_ROLE}]}]}


def _assignments_response(role_ids: list[str]) -> dict[str, Any]:
    return {"value": [{"appRoleId": rid} for rid in role_ids]}


# ---------------------------------------------------------------------------
# Tests for try_grant_missing_permissions
# ---------------------------------------------------------------------------


@pytest.mark.anyio("asyncio")
async def test_try_grant_missing_permissions_grants_missing_roles():
    """Grants roles that are in _PROVISION_APP_ROLES but not yet assigned."""
    all_roles = list(_PROVISION_APP_ROLES)
    # Simulate an app that is missing the two mailbox permissions
    present_roles = [r for r in all_roles if r not in {_MAILBOX_REPORTS_ROLE, _MAILBOX_SETTINGS_ROLE}]

    granted: list[dict] = []

    async def mock_graph_get(token: str, url: str) -> dict:
        if "appRoleAssignments" in url:
            return _assignments_response(present_roles)
        if _GRAPH_APP_ID in url:
            return {"value": [{"id": "graph-sp-id"}]}
        # service principal lookup for the company app
        return _sp_response("sp-123")

    async def mock_graph_post(token: str, url: str, payload: dict) -> dict:
        granted.append(payload)
        return {"id": "new-assignment"}

    with (
        patch.object(m365_service, "get_credentials", AsyncMock(return_value=_fake_creds())),
        patch.object(m365_service, "_graph_get", side_effect=mock_graph_get),
        patch.object(m365_service, "_graph_post", side_effect=mock_graph_post),
    ):
        result = await m365_service.try_grant_missing_permissions(
            company_id=1,
            access_token="admin-token",
        )

    assert result is True, "Should return True when permissions were granted"
    granted_role_ids = {g["appRoleId"] for g in granted}
    assert _MAILBOX_REPORTS_ROLE in granted_role_ids, (
        "Reports.Read.All must be granted when missing"
    )
    assert _MAILBOX_SETTINGS_ROLE in granted_role_ids, (
        "MailboxSettings.Read must be granted when missing"
    )


@pytest.mark.anyio("asyncio")
async def test_try_grant_missing_permissions_noop_when_all_present():
    """No Graph POST calls when all required permissions are already assigned."""
    # Include resourceId so EXO and Teams ManageAsApp are recognized as distinct grants.
    graph_assignments = [{"appRoleId": r, "resourceId": "graph-sp-id"} for r in _PROVISION_APP_ROLES]
    exo_assignment = {"appRoleId": _EXO_MANAGE_AS_APP_ROLE, "resourceId": "exo-sp-id"}
    teams_assignment = {"appRoleId": _TEAMS_MANAGE_AS_APP_ROLE, "resourceId": "teams-sp-id"}
    all_assignments = graph_assignments + [exo_assignment, teams_assignment]

    post_calls: list[str] = []

    async def mock_graph_get(token: str, url: str) -> dict:
        if "appRoleAssignments" in url:
            return {"value": all_assignments}
        if _EXO_APP_ID in url:
            return {"value": [{"id": "exo-sp-id"}]}
        if _TEAMS_APP_ID in url:
            return _teams_sp_response()
        if "roleManagement/directory/roleAssignments" in url:
            return {"value": [{"id": "existing-assignment"}]}  # both admin roles already assigned
        return _sp_response()

    async def mock_graph_post(token: str, url: str, payload: dict) -> dict:
        post_calls.append(url)
        return {}

    with (
        patch.object(m365_service, "get_credentials", AsyncMock(return_value=_fake_creds())),
        patch.object(m365_service, "_graph_get", side_effect=mock_graph_get),
        patch.object(m365_service, "_graph_post", side_effect=mock_graph_post),
    ):
        result = await m365_service.try_grant_missing_permissions(
            company_id=1,
            access_token="admin-token",
        )

    assert result is False, "Should return False when no permissions were missing"
    assert post_calls == [], "No appRoleAssignment POSTs expected when all roles are present"


@pytest.mark.anyio("asyncio")
async def test_try_grant_missing_permissions_grants_exo_and_teams_when_graph_complete():
    """EXO Exchange.ManageAsApp and Teams.ManageAsApp are both granted when all Graph
    roles are present but neither ManageAsApp role has been assigned yet.

    Regression test: previously the function returned early when all
    _PROVISION_APP_ROLES were assigned, skipping the Exchange.ManageAsApp
    grant.  This caused Get-MailboxPermission to fail with 403.
    """
    # All Graph roles present (no resourceId needed for these); neither EXO nor Teams granted.
    all_graph_role_assignments = [{"appRoleId": r} for r in _PROVISION_APP_ROLES]

    granted: list[dict] = []

    async def mock_graph_get(token: str, url: str) -> dict:
        if "appRoleAssignments" in url:
            return {"value": all_graph_role_assignments}
        if _EXO_APP_ID in url:
            return {"value": [{"id": "exo-sp-id"}]}
        if _TEAMS_APP_ID in url:
            return _teams_sp_response()
        if "roleManagement/directory/roleAssignments" in url:
            return {"value": [{"id": "existing-role"}]}  # admin roles already assigned
        # service principal lookup for the company app
        return _sp_response("sp-123")

    async def mock_graph_post(token: str, url: str, payload: dict) -> dict:
        granted.append(payload)
        return {"id": "new-assignment"}

    with (
        patch.object(m365_service, "get_credentials", AsyncMock(return_value=_fake_creds())),
        patch.object(m365_service, "_graph_get", side_effect=mock_graph_get),
        patch.object(m365_service, "_graph_post", side_effect=mock_graph_post),
    ):
        result = await m365_service.try_grant_missing_permissions(
            company_id=1,
            access_token="admin-token",
        )

    assert result is True, "Should return True when EXO/Teams permissions were granted"
    granted_role_ids = {g["appRoleId"] for g in granted}
    granted_resource_ids = {g["resourceId"] for g in granted}
    assert _EXO_MANAGE_AS_APP_ROLE in granted_role_ids, "Exchange.ManageAsApp must be granted"
    assert _TEAMS_MANAGE_AS_APP_ROLE in granted_role_ids, "Teams.ManageAsApp must be granted"
    assert "exo-sp-id" in granted_resource_ids, "Exchange.ManageAsApp must target EXO SP"
    assert "teams-sp-id" in granted_resource_ids, "Teams.ManageAsApp must target Teams SP"


@pytest.mark.anyio("asyncio")
async def test_try_grant_missing_permissions_noop_when_no_credentials():
    """Returns False without error when no credentials are configured for the company."""
    with patch.object(m365_service, "get_credentials", AsyncMock(return_value=None)):
        result = await m365_service.try_grant_missing_permissions(
            company_id=99,
            access_token="token",
        )
    assert result is False


@pytest.mark.anyio("asyncio")
async def test_try_grant_missing_permissions_noop_when_sp_not_found():
    """Returns False without error when the service principal is not found."""

    async def mock_graph_get(token: str, url: str) -> dict:
        return {"value": []}  # empty – SP not found

    with (
        patch.object(m365_service, "get_credentials", AsyncMock(return_value=_fake_creds())),
        patch.object(m365_service, "_graph_get", side_effect=mock_graph_get),
    ):
        result = await m365_service.try_grant_missing_permissions(
            company_id=1,
            access_token="token",
        )
    assert result is False


@pytest.mark.anyio("asyncio")
async def test_try_grant_missing_permissions_continues_on_partial_failure():
    """If one role grant fails, the others are still attempted."""
    all_roles = list(_PROVISION_APP_ROLES)
    present_roles = [r for r in all_roles if r not in {_MAILBOX_REPORTS_ROLE, _MAILBOX_SETTINGS_ROLE}]

    granted: list[str] = []

    async def mock_graph_get(token: str, url: str) -> dict:
        if "appRoleAssignments" in url:
            return _assignments_response(present_roles)
        if _GRAPH_APP_ID in url:
            return {"value": [{"id": "graph-sp-id"}]}
        if _EXO_APP_ID in url:
            return {"value": [{"id": "exo-sp-id"}]}
        if _TEAMS_APP_ID in url:
            return _teams_sp_response()
        if "roleManagement/directory/roleAssignments" in url:
            return {"value": [{"id": "existing-role"}]}  # admin roles already assigned
        return _sp_response()

    call_count = 0

    async def mock_graph_post(token: str, url: str, payload: dict) -> dict:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise M365Error("403 Forbidden")
        granted.append(payload["appRoleId"])
        return {"id": "assignment"}

    with (
        patch.object(m365_service, "get_credentials", AsyncMock(return_value=_fake_creds())),
        patch.object(m365_service, "_graph_get", side_effect=mock_graph_get),
        patch.object(m365_service, "_graph_post", side_effect=mock_graph_post),
    ):
        # Should not raise even when one role grant fails
        result = await m365_service.try_grant_missing_permissions(
            company_id=1,
            access_token="admin-token",
        )

    # Both missing Graph roles + EXO + Teams ManageAsApp should all be attempted
    assert call_count == 4, (
        "Both missing Graph roles, EXO, and Teams role should be attempted even when first fails"
    )
    # The second/third/fourth calls succeeded, so result should be True
    assert result is True, "Should return True when at least one permission was granted"


@pytest.mark.anyio("asyncio")
async def test_try_grant_missing_permissions_swallows_unexpected_errors():
    """Any unexpected exception is caught and logged rather than raised."""

    async def mock_graph_get(*args: Any, **kwargs: Any) -> dict:
        raise RuntimeError("network failure")

    with (
        patch.object(m365_service, "get_credentials", AsyncMock(return_value=_fake_creds())),
        patch.object(m365_service, "_graph_get", side_effect=mock_graph_get),
    ):
        # Must not raise
        result = await m365_service.try_grant_missing_permissions(
            company_id=1,
            access_token="token",
        )
    assert result is False, "Should return False when an unexpected error occurs"


# ---------------------------------------------------------------------------
# Tests for provision_app_registration – 409 Conflict handling
# ---------------------------------------------------------------------------


@pytest.mark.anyio("asyncio")
async def test_provision_app_registration_skips_409_role_assignments():
    """provision_app_registration must not fail when an appRoleAssignment returns 409."""
    from app.services.m365 import provision_app_registration, M365Error

    post_calls: list[dict] = []
    call_count = 0

    async def mock_graph_post(token: str, url: str, payload: dict) -> dict:
        nonlocal call_count
        call_count += 1
        post_calls.append({"url": url, "payload": payload})
        # Simulate 409 Conflict for the first appRoleAssignment
        if "appRoleAssignments" in url and call_count == 1:
            raise M365Error("Microsoft Graph POST failed (409)")
        # All other calls succeed
        if url.endswith("/addPassword"):
            return {"secretText": "test-secret", "keyId": "key-id-1"}
        return {"id": "created-id", "appId": "new-client-id"}

    async def mock_graph_get(token: str, url: str, **kwargs: Any) -> dict:
        if "filter=displayName" in url:
            return {"value": []}
        if "servicePrincipals" in url and _GRAPH_APP_ID in url:
            return {"value": [{"id": "graph-sp-id"}]}
        if "servicePrincipals" in url and _TEAMS_APP_ID in url:
            # Include appRoles so the ManageAsApp role-existence check passes
            return {"value": [{"id": "new-sp-id", "appRoles": [{"id": _TEAMS_MANAGE_AS_APP_ROLE}]}]}
        # EXO SP lookup returns a valid SP so grant is attempted
        return {"value": [{"id": "new-sp-id"}]}

    with (
        patch.object(m365_service, "_graph_post", side_effect=mock_graph_post),
        patch.object(m365_service, "_graph_get", side_effect=mock_graph_get),
    ):
        result = await provision_app_registration(
            access_token="admin-token",
            display_name="Test App",
        )
        await drain_provision_background_tasks()

    assert result["client_id"] == "new-client-id"
    assert result["client_secret"] == "test-secret"
    # All roles should have been attempted (Graph roles + Exchange.ManageAsApp + Teams.ManageAsApp)
    role_assignment_calls = [c for c in post_calls if "appRoleAssignments" in c["url"]]
    assert len(role_assignment_calls) == len(_PROVISION_APP_ROLES) + 2, (
        "All role assignments must be attempted even when the first returns 409"
    )


# ---------------------------------------------------------------------------
# Tests for connect callback – access token cleared when permissions granted
# ---------------------------------------------------------------------------


@pytest.mark.anyio("asyncio")
async def test_try_grant_missing_permissions_returns_false_when_all_grants_fail():
    """Returns False when all permission grants fail (no new permissions added)."""
    all_roles = list(_PROVISION_APP_ROLES)
    present_roles = [r for r in all_roles if r not in {_MAILBOX_REPORTS_ROLE, _MAILBOX_SETTINGS_ROLE}]

    async def mock_graph_get(token: str, url: str) -> dict:
        if "appRoleAssignments" in url:
            return _assignments_response(present_roles)
        if _GRAPH_APP_ID in url:
            return {"value": [{"id": "graph-sp-id"}]}
        if _EXO_APP_ID in url:
            return {"value": [{"id": "exo-sp-id"}]}
        if _TEAMS_APP_ID in url:
            return _teams_sp_response()
        if "roleManagement/directory/roleAssignments" in url:
            return {"value": []}  # admin roles not assigned
        return _sp_response()

    async def mock_graph_post(token: str, url: str, payload: dict) -> dict:
        raise M365Error("403 Forbidden – insufficient privileges")

    with (
        patch.object(m365_service, "get_credentials", AsyncMock(return_value=_fake_creds())),
        patch.object(m365_service, "_graph_get", side_effect=mock_graph_get),
        patch.object(m365_service, "_graph_post", side_effect=mock_graph_post),
    ):
        result = await m365_service.try_grant_missing_permissions(
            company_id=1,
            access_token="admin-token",
        )

    assert result is False, "Should return False when all grants fail"


# ---------------------------------------------------------------------------
# Constant checks – ensure mailbox permissions are present in _PROVISION_APP_ROLES
# ---------------------------------------------------------------------------

_USER_READWRITE_ALL_ROLE = "741f803b-c850-494e-b5df-cde7c675a1ca"  # User.ReadWrite.All
_GROUP_MEMBER_READWRITE_ALL_ROLE = "dbaae8cf-10b5-4b86-a4a1-f871c94c6695"  # GroupMember.ReadWrite.All


def test_provision_app_roles_includes_reports_read_all():
    """_PROVISION_APP_ROLES must include Reports.Read.All for mailbox sync."""
    assert _MAILBOX_REPORTS_ROLE in _PROVISION_APP_ROLES, (
        "Reports.Read.All (230c1aed-a721-4c5d-9cb4-a90514e508ef) must be in _PROVISION_APP_ROLES"
    )


def test_provision_app_roles_includes_mailbox_settings_read():
    """_PROVISION_APP_ROLES must include MailboxSettings.Read for mailbox sync."""
    assert _MAILBOX_SETTINGS_ROLE in _PROVISION_APP_ROLES, (
        "MailboxSettings.Read (40f97065-369a-49f4-947c-6a255697ae91) must be in _PROVISION_APP_ROLES"
    )


def test_provision_app_roles_includes_mail_readwrite():
    """_PROVISION_APP_ROLES must include Mail.ReadWrite for Office 365 mailbox import."""
    assert _MAIL_READWRITE_ROLE in _PROVISION_APP_ROLES, (
        "Mail.ReadWrite (e2a3a72e-5f79-4c64-b1b1-878b674786c9) must be in _PROVISION_APP_ROLES"
    )


def test_provision_app_roles_includes_user_readwrite_all():
    """_PROVISION_APP_ROLES must include User.ReadWrite.All for staff onboarding (create/update users, assign licenses)."""
    assert _USER_READWRITE_ALL_ROLE in _PROVISION_APP_ROLES, (
        "User.ReadWrite.All (741f803b-c850-494e-b5df-cde7c675a1ca) must be in _PROVISION_APP_ROLES "
        "to enable staff onboarding Graph API calls (create user, assign license, update user)"
    )


def test_provision_app_roles_includes_group_member_readwrite_all():
    """_PROVISION_APP_ROLES must include GroupMember.ReadWrite.All for staff onboarding (add/remove group members)."""
    assert _GROUP_MEMBER_READWRITE_ALL_ROLE in _PROVISION_APP_ROLES, (
        "GroupMember.ReadWrite.All (dbaae8cf-10b5-4b86-a4a1-f871c94c6695) must be in _PROVISION_APP_ROLES "
        "to enable staff onboarding Graph API calls (add/remove group members)"
    )


def test_provision_app_roles_includes_sharepoint_tenant_settings_read_all():
    """_PROVISION_APP_ROLES must include SharePointTenantSettings.Read.All for SPO best-practice checks."""
    assert _SHAREPOINT_TENANT_SETTINGS_ROLE in _PROVISION_APP_ROLES, (
        "SharePointTenantSettings.Read.All (a8ead177-1889-4546-9387-f25e658e2a79) must be in "
        "_PROVISION_APP_ROLES to enable SharePoint Online best-practice checks "
        "(GET /admin/sharepoint/settings)"
    )


@pytest.mark.anyio("asyncio")
async def test_try_grant_missing_permissions_grants_sharepoint_when_missing():
    """SharePointTenantSettings.Read.All is granted via the connect flow when missing.

    Regression test: verifies that the 'Authorize portal access' callback correctly
    grants SharePointTenantSettings.Read.All (a Graph application permission) when
    it is absent from the existing app role assignments.
    """
    # All Graph roles present except SharePointTenantSettings.Read.All
    present_roles = [r for r in _PROVISION_APP_ROLES if r != _SHAREPOINT_TENANT_SETTINGS_ROLE]
    # EXO and Teams ManageAsApp already granted so the test focuses purely on the Graph role.
    exo_assignment = {"appRoleId": _EXO_MANAGE_AS_APP_ROLE, "resourceId": "exo-sp-id"}
    teams_assignment = {"appRoleId": _TEAMS_MANAGE_AS_APP_ROLE, "resourceId": "teams-sp-id"}
    all_assignments = (
        [{"appRoleId": r, "resourceId": "graph-sp-id"} for r in present_roles]
        + [exo_assignment, teams_assignment]
    )

    granted: list[dict] = []

    async def mock_graph_get(token: str, url: str) -> dict:
        if "appRoleAssignments" in url:
            return {"value": all_assignments}
        if _GRAPH_APP_ID in url:
            return {"value": [{"id": "graph-sp-id"}]}
        if _EXO_APP_ID in url:
            return {"value": [{"id": "exo-sp-id"}]}
        if _TEAMS_APP_ID in url:
            return _teams_sp_response()
        if "roleManagement/directory/roleAssignments" in url:
            return {"value": [{"id": "existing-role"}]}  # admin roles already assigned
        return _sp_response("sp-123")

    async def mock_graph_post(token: str, url: str, payload: dict) -> dict:
        granted.append(payload)
        return {"id": "new-assignment"}

    with (
        patch.object(m365_service, "get_credentials", AsyncMock(return_value=_fake_creds())),
        patch.object(m365_service, "_graph_get", side_effect=mock_graph_get),
        patch.object(m365_service, "_graph_post", side_effect=mock_graph_post),
    ):
        result = await m365_service.try_grant_missing_permissions(
            company_id=1,
            access_token="admin-token",
        )

    assert result is True, "Should return True when SharePointTenantSettings.Read.All was granted"
    granted_role_ids = {g["appRoleId"] for g in granted}
    granted_resource_ids = {g.get("resourceId") for g in granted}
    assert _SHAREPOINT_TENANT_SETTINGS_ROLE in granted_role_ids, (
        "SharePointTenantSettings.Read.All must be granted when missing"
    )
    assert "graph-sp-id" in granted_resource_ids, (
        "SharePointTenantSettings.Read.All must be assigned to the Microsoft Graph SP"
    )
    # Only SharePointTenantSettings.Read.All should have been granted (one missing Graph role)
    assert len(granted) == 1, (
        f"Exactly one grant expected (SharePointTenantSettings.Read.All); got {len(granted)}"
    )



# ---------------------------------------------------------------------------
# Tests for _ensure_exchange_admin_role
# ---------------------------------------------------------------------------


@pytest.mark.anyio("asyncio")
async def test_ensure_exchange_admin_role_assigns_when_missing():
    """Exchange Administrator directory role is assigned when not present."""
    posted: list[dict] = []

    async def mock_graph_get(token: str, url: str) -> dict:
        if "roleManagement/directory/roleAssignments" in url:
            return {"value": []}  # not assigned
        return {"value": []}

    async def mock_graph_post(token: str, url: str, payload: dict) -> dict:
        posted.append(payload)
        return {"id": "new-role-assignment"}

    with (
        patch.object(m365_service, "_graph_get", side_effect=mock_graph_get),
        patch.object(m365_service, "_graph_post", side_effect=mock_graph_post),
    ):
        result = await m365_service._ensure_exchange_admin_role(
            access_token="admin-token",
            sp_object_id="sp-123",
        )

    assert result is True, "Should return True when role was newly assigned"
    assert len(posted) == 1
    assert posted[0]["principalId"] == "sp-123"
    assert posted[0]["roleDefinitionId"] == _EXO_ADMIN_ROLE_TEMPLATE_ID
    assert posted[0]["directoryScopeId"] == "/"


@pytest.mark.anyio("asyncio")
async def test_ensure_exchange_admin_role_noop_when_already_assigned():
    """Returns False without POST when Exchange Administrator role is already assigned."""
    posted: list[dict] = []

    async def mock_graph_get(token: str, url: str) -> dict:
        if "roleManagement/directory/roleAssignments" in url:
            return {"value": [{"id": "existing-assignment"}]}  # already assigned
        return {"value": []}

    async def mock_graph_post(token: str, url: str, payload: dict) -> dict:
        posted.append(payload)
        return {}

    with (
        patch.object(m365_service, "_graph_get", side_effect=mock_graph_get),
        patch.object(m365_service, "_graph_post", side_effect=mock_graph_post),
    ):
        result = await m365_service._ensure_exchange_admin_role(
            access_token="admin-token",
            sp_object_id="sp-123",
        )

    assert result is False, "Should return False when role is already assigned"
    assert posted == [], "No POST expected when role is already assigned"


@pytest.mark.anyio("asyncio")
async def test_ensure_exchange_admin_role_logs_on_failure():
    """Returns False and logs error when role assignment fails."""

    async def mock_graph_get(token: str, url: str) -> dict:
        if "roleManagement/directory/roleAssignments" in url:
            return {"value": []}  # not assigned
        return {"value": []}

    async def mock_graph_post(token: str, url: str, payload: dict) -> dict:
        raise M365Error("403 Forbidden – insufficient privileges", http_status=403)

    with (
        patch.object(m365_service, "_graph_get", side_effect=mock_graph_get),
        patch.object(m365_service, "_graph_post", side_effect=mock_graph_post),
    ):
        result = await m365_service._ensure_exchange_admin_role(
            access_token="admin-token",
            sp_object_id="sp-123",
        )

    assert result is False, "Should return False when assignment fails"


@pytest.mark.anyio("asyncio")
async def test_try_grant_missing_permissions_assigns_exchange_admin_role():
    """Exchange Administrator directory role is assigned during connect flow."""
    # All Graph + EXO + Teams roles already granted (with resourceIds for precise matching)
    graph_assignments = [{"appRoleId": r, "resourceId": "graph-sp-id"} for r in _PROVISION_APP_ROLES]
    exo_assignment = {"appRoleId": _EXO_MANAGE_AS_APP_ROLE, "resourceId": "exo-sp-id"}
    teams_assignment = {"appRoleId": _TEAMS_MANAGE_AS_APP_ROLE, "resourceId": "teams-sp-id"}
    posted: list[dict] = []

    async def mock_graph_get(token: str, url: str) -> dict:
        if "appRoleAssignments" in url:
            return {"value": graph_assignments + [exo_assignment, teams_assignment]}
        if _EXO_APP_ID in url:
            return {"value": [{"id": "exo-sp-id"}]}
        if _TEAMS_APP_ID in url:
            return _teams_sp_response()
        if "roleManagement/directory/roleAssignments" in url:
            return {"value": []}  # both admin roles not yet assigned
        return _sp_response()

    async def mock_graph_post(token: str, url: str, payload: dict) -> dict:
        posted.append({"url": url, "payload": payload})
        return {"id": "new-assignment"}

    with (
        patch.object(m365_service, "get_credentials", AsyncMock(return_value=_fake_creds())),
        patch.object(m365_service, "_graph_get", side_effect=mock_graph_get),
        patch.object(m365_service, "_graph_post", side_effect=mock_graph_post),
    ):
        result = await m365_service.try_grant_missing_permissions(
            company_id=1,
            access_token="admin-token",
        )

    assert result is True, "Should return True when admin roles were assigned"
    role_assignment_urls = [p["url"] for p in posted if "roleManagement/directory/roleAssignments" in p["url"]]
    assert len(role_assignment_urls) == 2, "Both Exchange Admin and Teams Admin roles should be assigned"
    assigned_role_def_ids = {p["payload"]["roleDefinitionId"] for p in posted if "roleManagement" in p["url"]}
    assert _EXO_ADMIN_ROLE_TEMPLATE_ID in assigned_role_def_ids, "Exchange Admin role must be assigned"
    assert _TEAMS_ADMIN_ROLE_TEMPLATE_ID in assigned_role_def_ids, "Teams Admin role must be assigned"


# ---------------------------------------------------------------------------
# Constant checks – scopes include RoleManagement.ReadWrite.Directory
# ---------------------------------------------------------------------------


def test_provision_scope_includes_role_management():
    """PROVISION_SCOPE must include RoleManagement.ReadWrite.Directory."""
    from app.services.m365 import PROVISION_SCOPE

    assert "RoleManagement.ReadWrite.Directory" in PROVISION_SCOPE, (
        "PROVISION_SCOPE must include RoleManagement.ReadWrite.Directory for Exchange Admin role assignment"
    )


def test_connect_scope_includes_role_management():
    """CONNECT_SCOPE must include RoleManagement.ReadWrite.Directory."""
    from app.services.m365 import CONNECT_SCOPE

    assert "RoleManagement.ReadWrite.Directory" in CONNECT_SCOPE, (
        "CONNECT_SCOPE must include RoleManagement.ReadWrite.Directory for Exchange Admin role assignment"
    )


# ---------------------------------------------------------------------------
# Tests for _ensure_teams_service_admin_role
# ---------------------------------------------------------------------------


@pytest.mark.anyio("asyncio")
async def test_ensure_teams_service_admin_role_assigns_when_missing():
    """Teams Service Administrator directory role is assigned when not present."""
    posted: list[dict] = []

    async def mock_graph_get(token: str, url: str) -> dict:
        if "roleManagement/directory/roleAssignments" in url:
            return {"value": []}  # not assigned
        return {"value": []}

    async def mock_graph_post(token: str, url: str, payload: dict) -> dict:
        posted.append(payload)
        return {"id": "new-role-assignment"}

    with (
        patch.object(m365_service, "_graph_get", side_effect=mock_graph_get),
        patch.object(m365_service, "_graph_post", side_effect=mock_graph_post),
    ):
        result = await m365_service._ensure_teams_service_admin_role(
            access_token="admin-token",
            sp_object_id="sp-123",
        )

    assert result is True, "Should return True when role was newly assigned"
    assert len(posted) == 1
    assert posted[0]["principalId"] == "sp-123"
    assert posted[0]["roleDefinitionId"] == _TEAMS_ADMIN_ROLE_TEMPLATE_ID
    assert posted[0]["directoryScopeId"] == "/"


@pytest.mark.anyio("asyncio")
async def test_ensure_teams_service_admin_role_noop_when_already_assigned():
    """Returns False without POST when Teams Service Administrator role is already assigned."""
    posted: list[dict] = []

    async def mock_graph_get(token: str, url: str) -> dict:
        if "roleManagement/directory/roleAssignments" in url:
            return {"value": [{"id": "existing-assignment"}]}  # already assigned
        return {"value": []}

    async def mock_graph_post(token: str, url: str, payload: dict) -> dict:
        posted.append(payload)
        return {}

    with (
        patch.object(m365_service, "_graph_get", side_effect=mock_graph_get),
        patch.object(m365_service, "_graph_post", side_effect=mock_graph_post),
    ):
        result = await m365_service._ensure_teams_service_admin_role(
            access_token="admin-token",
            sp_object_id="sp-123",
        )

    assert result is False, "Should return False when role is already assigned"
    assert posted == [], "No POST expected when role is already assigned"


@pytest.mark.anyio("asyncio")
async def test_ensure_teams_service_admin_role_logs_on_failure():
    """Returns False and logs error when Teams admin role assignment fails."""

    async def mock_graph_get(token: str, url: str) -> dict:
        if "roleManagement/directory/roleAssignments" in url:
            return {"value": []}

    async def mock_graph_post(token: str, url: str, payload: dict) -> dict:
        raise M365Error("403 Forbidden – insufficient privileges", http_status=403)

    with (
        patch.object(m365_service, "_graph_get", side_effect=mock_graph_get),
        patch.object(m365_service, "_graph_post", side_effect=mock_graph_post),
    ):
        result = await m365_service._ensure_teams_service_admin_role(
            access_token="admin-token",
            sp_object_id="sp-123",
        )

    assert result is False, "Should return False when assignment fails"


@pytest.mark.anyio("asyncio")
async def test_try_grant_missing_permissions_grants_teams_manage_as_app():
    """Teams.ManageAsApp is granted during connect flow when missing."""
    # All Graph roles present, EXO already granted, but Teams is missing.
    graph_assignments = [{"appRoleId": r, "resourceId": "graph-sp-id"} for r in _PROVISION_APP_ROLES]
    exo_assignment = {"appRoleId": _EXO_MANAGE_AS_APP_ROLE, "resourceId": "exo-sp-id"}

    granted: list[dict] = []

    async def mock_graph_get(token: str, url: str) -> dict:
        if "appRoleAssignments" in url:
            return {"value": graph_assignments + [exo_assignment]}
        if _EXO_APP_ID in url:
            return {"value": [{"id": "exo-sp-id"}]}
        if _TEAMS_APP_ID in url:
            return _teams_sp_response()
        if "roleManagement/directory/roleAssignments" in url:
            return {"value": [{"id": "existing-role"}]}  # admin roles already assigned
        return _sp_response("sp-123")

    async def mock_graph_post(token: str, url: str, payload: dict) -> dict:
        granted.append(payload)
        return {"id": "new-assignment"}

    with (
        patch.object(m365_service, "get_credentials", AsyncMock(return_value=_fake_creds())),
        patch.object(m365_service, "_graph_get", side_effect=mock_graph_get),
        patch.object(m365_service, "_graph_post", side_effect=mock_graph_post),
    ):
        result = await m365_service.try_grant_missing_permissions(
            company_id=1,
            access_token="admin-token",
        )

    assert result is True, "Should return True when Teams.ManageAsApp was granted"
    assert len(granted) == 1, "Only Teams.ManageAsApp grant expected"
    assert granted[0]["appRoleId"] == _TEAMS_MANAGE_AS_APP_ROLE
    assert granted[0]["resourceId"] == "teams-sp-id"


# ---------------------------------------------------------------------------
# Constant checks – Teams constants
# ---------------------------------------------------------------------------


def test_teams_app_id_constant_defined():
    """_TEAMS_APP_ID must be defined and be the Skype and Teams Tenant Admin API app ID."""
    from app.services.m365 import _TEAMS_APP_ID
    assert _TEAMS_APP_ID == "48ac35b8-9aa8-4d74-927d-1f4a14a0b239"


def test_teams_manage_as_app_role_constant_defined():
    """_TEAMS_MANAGE_AS_APP_ROLE must be defined."""
    from app.services.m365 import _TEAMS_MANAGE_AS_APP_ROLE
    assert _TEAMS_MANAGE_AS_APP_ROLE == "dc50a0fb-09a3-484d-be87-e023b12c6440"


def test_teams_admin_role_template_id_constant_defined():
    """_TEAMS_ADMIN_ROLE_TEMPLATE_ID must be defined as the Teams Service Administrator role."""
    from app.services.m365 import _TEAMS_ADMIN_ROLE_TEMPLATE_ID
    assert _TEAMS_ADMIN_ROLE_TEMPLATE_ID == "69091246-20e8-4a56-aa4d-066075b2a7a8"


@pytest.mark.anyio("asyncio")
async def test_try_grant_missing_permissions_skips_teams_when_role_absent_from_sp():
    """Teams.ManageAsApp is silently skipped when the Teams SP doesn't expose the role.

    Regression test: previously a 400 'Permission being assigned was not found' error
    was retried up to 10 times and then logged as an error.  Now the code checks
    appRoles before attempting the grant.
    """
    graph_assignments = [{"appRoleId": r, "resourceId": "graph-sp-id"} for r in _PROVISION_APP_ROLES]
    exo_assignment = {"appRoleId": _EXO_MANAGE_AS_APP_ROLE, "resourceId": "exo-sp-id"}
    posted: list[dict] = []

    async def mock_graph_get(token: str, url: str) -> dict:
        if "appRoleAssignments" in url:
            return {"value": graph_assignments + [exo_assignment]}
        if _EXO_APP_ID in url:
            return {"value": [{"id": "exo-sp-id"}]}
        if _TEAMS_APP_ID in url:
            # Teams SP exists but does NOT expose the ManageAsApp role
            return {"value": [{"id": "teams-sp-id", "appRoles": []}]}
        if "roleManagement/directory/roleAssignments" in url:
            return {"value": [{"id": "existing-role"}]}  # admin roles already assigned
        return _sp_response("sp-123")

    async def mock_graph_post(token: str, url: str, payload: dict) -> dict:
        posted.append(payload)
        return {"id": "new-assignment"}

    with (
        patch.object(m365_service, "get_credentials", AsyncMock(return_value=_fake_creds())),
        patch.object(m365_service, "_graph_get", side_effect=mock_graph_get),
        patch.object(m365_service, "_graph_post", side_effect=mock_graph_post),
    ):
        result = await m365_service.try_grant_missing_permissions(
            company_id=1,
            access_token="admin-token",
        )

    assert result is False, "Should return False when no new grants were made"
    teams_grants = [p for p in posted if p.get("resourceId") == "teams-sp-id"]
    assert teams_grants == [], "No Teams.ManageAsApp POST expected when role is absent from SP"
