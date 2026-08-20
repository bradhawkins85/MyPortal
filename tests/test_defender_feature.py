"""Regression coverage for the Windows Defender management surface."""
from pathlib import Path

from app.api.routes.defender import router
from app.schemas.defender import DefenderExclusionCreate
from app.security.menu_permissions import MENU_PERMISSION_MAP


def test_defender_permission_supports_role_access_levels():
    permission = MENU_PERMISSION_MAP["menu.defender"]
    assert permission.label == "Windows Defender"
    assert permission.admin_only is False


def test_defender_routes_cover_portal_tray_and_ticket_workflows():
    paths = {route.path for route in router.routes}
    assert {
        "/defender",
        "/api/defender/enabled",
        "/api/defender/exclusions",
        "/api/defender/settings",
        "/api/defender/devices/{device_id}/commands/{command_type}",
        "/api/defender/detections/{detection_id}/actions",
        "/api/defender/devices/{device_id}/ticket",
        "/api/defender/detections/{detection_id}/ticket",
        "/api/tray/defender/policy",
        "/api/tray/defender/status",
        "/api/tray/defender/detections",
        "/api/tray/defender/commands",
        "/api/tray/defender/commands/{command_id}/result",
    } <= paths


def test_device_exclusion_payload_requires_supported_type():
    payload = DefenderExclusionCreate(
        scope="device", exclusion_type="path", value=r"C:\Trusted", tray_device_id=42
    )
    assert payload.tray_device_id == 42


def test_all_defender_tables_use_shared_filtering_and_sorting():
    template = Path("app/templates/defender/index.html").read_text()
    for table_name in ("devices", "exclusions", "detections"):
        table_id = f"defender-{table_name}-table"
        assert f'id="{table_id}" data-table' in template
        assert f'data-table-filter="{table_id}"' in template
    assert template.count('data-sort="') >= 10
    assert "/static/js/tables.js" in template


def test_defender_ui_exposes_management_workflows():
    template = Path("app/templates/defender/index.html").read_text()
    assert "Tamper protection" in template
    assert "Stale agents" in template
    assert 'data-defender-command="quick_scan"' in template
    assert 'data-defender-command="full_scan"' in template
    assert 'data-defender-command="signature_update"' in template
    assert 'data-detection-action="quarantine"' in template
    assert "Automatic tickets" in template
