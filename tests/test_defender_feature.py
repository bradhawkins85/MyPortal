"""Regression coverage for the Windows Defender management surface."""
import asyncio
from pathlib import Path
from types import SimpleNamespace

from starlette.requests import Request
from starlette.responses import RedirectResponse

from app.api.routes import defender
from app.api.routes.defender import router
from app.repositories import defender as defender_repo
from app.schemas.defender import (DefenderExclusionCreate, DefenderExclusionListCreate,
    DefenderSettingsUpdate, DefenderStatusReport)
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


def test_defender_exclusion_payload_supports_registry_paths():
    payload = DefenderExclusionCreate(
        scope="company", exclusion_type="registry", value=r"HKLM\Software\Contoso"
    )

    assert payload.exclusion_type == "registry"
    assert payload.value == r"HKLM\Software\Contoso"


def test_all_defender_tables_use_shared_filtering_and_sorting():
    template = Path("app/templates/defender/index.html").read_text()
    for table_name in ("devices", "exclusions", "detections"):
        table_id = f"defender-{table_name}-table"
        assert f'id="{table_id}" data-table' in template
        assert f'data-table-filter="{table_id}"' in template
    assert template.count('data-sort="') >= 10
    assert "/static/js/tables.js" in template


def test_defender_endpoint_table_displays_last_scan_details():
    template = Path("app/templates/defender/index.html").read_text()
    assert 'data-sort="date">Last scan</th>' in template
    assert "d.last_scan.scan_type|title" in template
    assert "d.last_scan.status|title" in template
    assert "d.last_scan.duration_seconds" in template


def test_status_report_accepts_recent_scan_history():
    report = DefenderStatusReport(
        scan_history=[{
            "scan_type": "quick",
            "started_at": "2026-08-20T01:00:00Z",
            "completed_at": "2026-08-20T01:02:30Z",
            "duration_seconds": 150,
            "status": "completed",
        }]
    )
    assert report.scan_history[0].scan_type == "quick"
    assert report.scan_history[0].duration_seconds == 150


def test_status_report_accepts_defender_protection_history():
    report = DefenderStatusReport(detections=[{
        "detection_uid": "det-123",
        "threat_name": "Trojan:Win32/Example",
        "severity": "high",
        "status": "remediated",
        "detected_at": "2026-08-20T01:02:03Z",
        "infected_files": [r"C:\Users\Example\payload.exe"],
        "details": {"action_success": True},
    }])
    assert report.detections[0].threat_name == "Trojan:Win32/Example"
    assert report.detections[0].status == "remediated"
    assert report.detections[0].infected_files == [r"C:\Users\Example\payload.exe"]


def test_status_report_persists_embedded_protection_history(monkeypatch):
    request = Request({"type": "http", "method": "POST", "path": "/api/tray/defender/status", "headers": []})
    reported = []

    async def tray(_request):
        return {"id": 7, "company_id": 42, "hostname": "PC-07"}

    async def report_detection(device_id, company_id, detection):
        reported.append((device_id, company_id, detection.detection_uid))

    monkeypatch.setattr(defender, "_tray", tray)
    monkeypatch.setattr(defender.repo, "report_status", lambda *_args: asyncio.sleep(0))
    monkeypatch.setattr(defender.repo, "report_detection", report_detection)
    monkeypatch.setattr(defender.repo, "settings", lambda _company_id: asyncio.sleep(0, result={}))
    monkeypatch.setattr(defender.repo, "clear_alert_ticket", lambda *_args: asyncio.sleep(0))

    response = asyncio.run(defender.tray_status(DefenderStatusReport(detections=[{
        "detection_uid": "det-123",
        "threat_name": "Trojan:Win32/Example",
        "severity": "high",
        "status": "remediated",
        "detected_at": "2026-08-20T01:02:03Z",
    }]), request))

    assert response == {"status": "accepted"}
    assert reported == [(7, 42, "det-123")]


def test_defender_ui_exposes_management_workflows():
    template = Path("app/templates/defender/index.html").read_text()
    assert "Tamper protection" in template
    assert "Stale agents" in template
    assert 'data-defender-command="quick_scan"' in template
    assert 'data-defender-command="full_scan"' in template
    assert 'data-defender-command="signature_update"' in template
    assert 'data-defender-modal-open": "protection-policy-modal"' in template
    assert 'data-defender-modal-open": "ticket-actions-modal"' in template
    assert 'data-defender-modal-open": "exclusions-modal"' in template
    assert 'data-defender-modal-open": "exclusion-lists-modal"' in template
    assert 'id="exclusion-lists-modal"' in template
    assert "Apply to companies" in template
    assert 'class="card defender-detections-section"' in template
    assert 'data-label="Infected files"' in template
    assert 'data-detection-exclude-value="{{ file }}"' in template
    assert 'data-detection-device-id="{{ d.tray_device_id }}"' in template
    assert 'data-detection-action="quarantine"' in template
    assert "Automatic ticket creation" in template
    assert "Anti Virus is off" in template
    assert "Real-time protection is off" in template
    assert "Tamper protection is off" in template
    assert "A threat is detected" in template
    assert "data-ticket-device" not in template
    assert "stat-strip" not in template  # rendered through the shared counter macro
    assert 'counter_strip([' in template


def test_defender_exclusion_list_payload_supports_reusable_types_and_companies():
    payload = DefenderExclusionListCreate(name="  Standard apps  ", exclusions=[
        {"exclusion_type": "path", "value": r"C:\Trusted"},
        {"exclusion_type": "process", "value": "agent.exe"},
        {"exclusion_type": "extension", "value": ".cache"},
        {"exclusion_type": "registry", "value": r"HKLM\Software\Contoso"},
    ], company_ids=[42, 84, 42])
    assert payload.name == "Standard apps"
    assert [item.exclusion_type for item in payload.exclusions] == ["path", "process", "extension", "registry"]
    assert payload.company_ids == [42, 84]


def test_defender_exclusion_list_routes_cover_management_workflow():
    routes = {(route.path, tuple(route.methods or [])) for route in router.routes}
    assert any(path == "/api/defender/exclusion-lists" and "POST" in methods for path, methods in routes)
    assert any(path == "/api/defender/exclusion-lists/{list_id}" and "PUT" in methods for path, methods in routes)
    assert any(path == "/api/defender/exclusion-lists/{list_id}" and "DELETE" in methods for path, methods in routes)


def test_defender_navigation_shows_active_detection_count():
    template = Path("app/templates/base.html").read_text()
    assert "defender_detection_count" in template
    assert 'class="menu__badge"' in template


def test_defender_ticket_options_default_to_disabled():
    settings = DefenderSettingsUpdate()
    assert settings.auto_ticket_antivirus_off is False
    assert settings.auto_ticket_realtime_off is False
    assert settings.auto_ticket_tamper_off is False
    assert settings.auto_ticket_threat_detected is False


def test_status_report_automatically_creates_configured_alert_ticket(monkeypatch):
    request = Request({"type": "http", "method": "POST", "path": "/api/tray/defender/status", "headers": []})
    created = []

    async def tray(_request):
        return {"id": 7, "company_id": 42, "hostname": "PC-07", "asset_id": 99}

    async def create_ticket(**kwargs):
        created.append(kwargs)
        return {"id": 123}

    monkeypatch.setattr(defender, "_tray", tray)
    monkeypatch.setattr(defender.repo, "report_status", lambda *_args: asyncio.sleep(0))
    monkeypatch.setattr(defender.repo, "settings", lambda _company_id: asyncio.sleep(0, result={"defender_auto_ticket_antivirus_off": True}))
    monkeypatch.setattr(defender.repo, "alert_ticket", lambda *_args: asyncio.sleep(0, result=None))
    monkeypatch.setattr(defender.repo, "link_alert_ticket", lambda *_args: asyncio.sleep(0))
    monkeypatch.setattr(defender.repo, "clear_alert_ticket", lambda *_args: asyncio.sleep(0))
    monkeypatch.setattr(defender.tickets_service, "create_ticket", create_ticket)
    monkeypatch.setattr(defender.tickets_repo, "replace_ticket_assets", lambda *_args: asyncio.sleep(0))

    response = asyncio.run(defender.tray_status(DefenderStatusReport(
        antivirus_enabled=False,
        realtime_protection_enabled=True,
        tamper_protection_enabled=True,
    ), request))

    assert response == {"status": "accepted"}
    assert len(created) == 1
    assert created[0]["subject"] == "Defender alert: Anti Virus is off on PC-07"
    assert created[0]["external_reference"] == "defender-alert:7:antivirus_off"


def test_defender_reporting_catalog_supports_reports_and_dashboard_panels():
    sql = Path("migrations/331_windows_defender_reporting_queries.sql").read_text()

    assert "'defender-device-status'" in sql
    assert "'defender-detections'" in sql
    assert "'dashboard-defender-devices'" in sql
    assert "'dashboard-defender-unhealthy-devices'" in sql
    assert "'dashboard-defender-health-by-status'" in sql
    assert "'dashboard-defender-active-detections-by-severity'" in sql
    assert sql.count("{{current.company}}") == 6
    assert sql.count(" AS X") == 2
    assert sql.count(" AS Y") == 2


def test_defender_infected_files_are_available_in_reporting():
    sql = Path("migrations/345_defender_infected_files.sql").read_text()

    assert "infected_files_json AS infected_files" in sql
    assert "WHERE slug = 'defender-detections'" in sql


def test_defender_device_queries_only_include_windows_agents(monkeypatch):
    queries = []

    async def fetch_all(sql, _params):
        queries.append(sql)
        return []

    monkeypatch.setattr(defender_repo.db, "fetch_all", fetch_all)

    devices, exclusions, detections = asyncio.run(defender_repo.dashboard(42))

    assert (devices, exclusions, detections) == ([], [], [])
    assert "LOWER(td.os)='windows'" in queries[0]


def test_defender_reporting_migration_excludes_non_windows_agents():
    sql = Path("migrations/333_defender_windows_devices_only.sql").read_text()

    assert sql.count("LOWER(td.os) = ''windows''") == 4
    assert "WHERE slug = 'defender-device-status'" in sql
    assert "WHERE slug = 'dashboard-defender-devices'" in sql
    assert "WHERE slug = 'dashboard-defender-unhealthy-devices'" in sql
    assert "WHERE slug = 'dashboard-defender-health-by-status'" in sql
