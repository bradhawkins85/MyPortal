"""Regression tests for the Microsoft 365 diagnostics permission catalog."""

from app.services import m365 as m365_service


def test_graph_diagnostics_catalog_has_human_readable_permission_names():
    """Every required Graph role is rendered as a permission name, not its GUID."""
    graph_app = next(
        app
        for app in m365_service.ENTERPRISE_APP_CATALOG
        if app["app_id"] == m365_service._GRAPH_APP_ID
    )

    assert all(permission["name"] != permission["id"] for permission in graph_app["permissions"])
    assert {
        permission["id"]: permission["name"] for permission in graph_app["permissions"]
    }["9e3f62cf-ca93-4989-b6ce-bf83c28f9fe8"] == "RoleManagement.ReadWrite.Directory"
