from app.services.m365_access_baseline import (
    GRAPH_APPLICATION_PERMISSION_IDS,
    REQUIRED_DIRECTORY_ROLES,
    REQUIRED_PERMISSIONS,
)
from app.services.m365 import CONNECT_SCOPE, PROVISION_SCOPE


def test_required_access_baseline_contains_only_permissions_used_by_myportal():
    entries = {(item.permission_type, item.resource, item.name) for item in REQUIRED_PERMISSIONS}
    expected = {
        ("Application", "Microsoft Graph", name)
        for name in GRAPH_APPLICATION_PERMISSION_IDS
    }

    assert entries == expected
    assert len(entries) == len(REQUIRED_PERMISSIONS)
    assert not any(item.permission_type == "Delegated" for item in REQUIRED_PERMISSIONS)
    assert all(item.feature and item.operation and item.disposition for item in REQUIRED_PERMISSIONS)


def test_password_profile_application_permission_uses_graph_role_id():
    """Password resets request the application role, not an unrelated role ID."""
    assert (
        GRAPH_APPLICATION_PERMISSION_IDS["User-PasswordProfile.ReadWrite.All"]
        == "cc117bb9-00cf-4eb8-b580-ea2a878fe8f7"
    )


def test_permission_contract_does_not_assign_directory_roles():
    assert REQUIRED_DIRECTORY_ROLES == ()


def test_bootstrap_scopes_do_not_request_delegated_permission_management():
    assert "DelegatedPermissionGrant.ReadWrite.All" not in PROVISION_SCOPE
    assert "DelegatedPermissionGrant.ReadWrite.All" not in CONNECT_SCOPE
