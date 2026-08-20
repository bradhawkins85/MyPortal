"""Regression coverage for the Windows Defender management surface."""
import asyncio
from pathlib import Path
from types import SimpleNamespace

from starlette.requests import Request
from starlette.responses import RedirectResponse

from app.api.routes import defender
from app.api.routes.defender import router
from app.schemas.defender import DefenderExclusionCreate
from app.security.menu_permissions import MENU_PERMISSION_MAP


def test_defender_permission_supports_role_access_levels():
    permission = MENU_PERMISSION_MAP["menu.defender"]
    assert permission.label == "Windows Defender"
    assert permission.admin_only is False


def test_defender_portal_context_uses_application_session_auth(monkeypatch):
    request = Request({"type": "http", "method": "GET", "path": "/defender", "headers": []})
    expected_user = {"company_id": 42, "is_company_admin": True}

    async def require_authenticated_user(received_request):
        assert received_request is request
        return expected_user, None

    monkeypatch.setattr(
        defender,
        "_main",
        lambda: SimpleNamespace(_require_authenticated_user=require_authenticated_user),
    )

    user, membership, company_id, redirect = asyncio.run(defender._portal_context(request))

    assert user is expected_user
    assert membership is None
    assert company_id == 42
    assert redirect is None


def test_defender_portal_context_preserves_auth_redirect(monkeypatch):
    request = Request({"type": "http", "method": "GET", "path": "/defender", "headers": []})
    auth_redirect = RedirectResponse("/login", status_code=303)

    async def require_authenticated_user(_request):
        return None, auth_redirect

    monkeypatch.setattr(
        defender,
        "_main",
        lambda: SimpleNamespace(_require_authenticated_user=require_authenticated_user),
    )

    user, membership, company_id, redirect = asyncio.run(defender._portal_context(request))

    assert (user, membership, company_id) == (None, None, None)
    assert redirect is auth_redirect


def test_defender_page_renders_html_template(monkeypatch):
    request = Request({"type": "http", "method": "GET", "path": "/defender", "headers": []})
    expected_user = {"company_id": 42, "is_company_admin": True}
    rendered_response = object()

    async def portal_context(_request):
        return expected_user, None, 42, None

    async def render_template(template_name, received_request, user, *, extra):
        assert template_name == "defender/index.html"
        assert received_request is request
        assert user is expected_user
        assert extra == {
            "defender_enabled": False,
            "defender_devices": [],
            "defender_exclusions": [],
            "defender_detections": [],
            "defender_can_write": True,
            "defender_settings": {},
        }
        return rendered_response

    monkeypatch.setattr(defender, "_portal_context", portal_context)
    monkeypatch.setattr(
        defender.repo,
        "company_enabled",
        lambda _company_id: asyncio.sleep(0, result=False),
    )
    monkeypatch.setattr(defender, "_main", lambda: SimpleNamespace(_render_template=render_template))

    response = asyncio.run(defender.defender_page(request))

    assert response is rendered_response


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
