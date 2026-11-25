"""The Scripts page's *Usage and recent runs* section: everywhere a script is linked."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from app.features.rmm import routes
from app.repositories import rmm as rmm_repo
from app.services import rmm_script_usage as usage
from tests.test_rmm_scripts import _detail, _render_library, _sync, anyio_backend, sqlite_db  # noqa: F401 - fixtures

CLEANUP = "param([string]$Drive)\nWrite-Output cleanup\n"
OTHER = "Write-Output other\n"


async def _setup(monkeypatch):
    await _sync(monkeypatch, {"rmm/Common/disk/Cleanup.ps1": CLEANUP, "rmm/Common/Other.ps1": OTHER})
    scripts = {script["name"]: script for script in await rmm_repo.list_scripts()}
    return scripts["Cleanup"], scripts["Other"]


async def _link_everywhere(fake, script_id: int, other_id: int) -> None:
    run_steps = json.dumps([{"key": "a1", "type": "create_ticket"}, {"key": "a2", "type": "run_script", "script_id": script_id}])
    other_steps = json.dumps([{"key": "a1", "type": "run_script", "script_id": other_id}])
    statements = [
        ("INSERT INTO rmm_schedules (name, company_id, script_id, cron, is_enabled) VALUES (%s, %s, %s, %s, %s)",
         ("Nightly cleanup", None, script_id, "0 2 * * *", 1)),
        ("INSERT INTO rmm_schedules (name, company_id, script_id, cron, is_enabled) VALUES (%s, %s, %s, %s, %s)",
         ("Other company cleanup", 2, script_id, "0 3 * * *", 0)),
        ("INSERT INTO rmm_onboarding_steps (company_id, position, script_id) VALUES (%s, %s, %s)", (1, 0, script_id)),
        ("INSERT INTO rmm_checks (name, company_id, asset_id, check_type, check_script_id, actions_json) "
         "VALUES (%s, %s, %s, %s, %s, %s)", ("Disk space", None, 10, "disk", None, run_steps)),
        ("INSERT INTO rmm_checks (name, company_id, check_type, check_script_id) VALUES (%s, %s, %s, %s)",
         ("Script test", 1, "script", script_id)),
        ("INSERT INTO rmm_checks (name, company_id, check_type, actions_json) VALUES (%s, %s, %s, %s)",
         ("Unrelated", 1, "disk", other_steps)),
        ("INSERT INTO rmm_event_log_rules (name, company_id, asset_id, action, conditions_json, script_id) "
         "VALUES (%s, %s, %s, %s, %s, %s)", ("Legacy rule", 1, None, "run_script", "[]", script_id)),
        ("INSERT INTO rmm_event_log_rules (name, company_id, action, conditions_json, actions_json) "
         "VALUES (%s, %s, %s, %s, %s)", ("Other company rule", 2, "none", "[]", run_steps)),
        ("INSERT INTO rmm_script_result_rules (name, company_id, script_id, conditions_json, action, next_script_id) "
         "VALUES (%s, %s, %s, %s, %s, %s)", ("Clean after report", None, other_id, "[]", "run_script", script_id)),
        ("INSERT INTO automations (name, kind, status, trigger_event, action_module, action_payload) "
         "VALUES (%s, %s, %s, %s, %s, %s)",
         ("Low disk ticket", "event", "active", "tickets.created", None,
          json.dumps({"actions": [{"module": "rmm-script", "payload": {"script": "rmm/Common/disk/Cleanup.ps1"}}]}))),
        ("INSERT INTO automations (name, kind, status, action_module, action_payload) VALUES (%s, %s, %s, %s, %s)",
         ("By ID", "scheduled", "inactive", "rmm-script", json.dumps({"script": str(script_id)}))),
        ("INSERT INTO automations (name, kind, status, action_module, action_payload) VALUES (%s, %s, %s, %s, %s)",
         ("Different script", "event", "active", "rmm-script", json.dumps({"script": "Other"}))),
        ("INSERT INTO tray_menu_configs (name, scope, scope_ref_id, payload_json) VALUES (%s, %s, %s, %s)",
         ("Contoso menu", "company", 1, json.dumps([{"type": "submenu", "label": "Fix", "children": [
             {"type": "rmm_script", "label": "Free up space", "script_id": script_id}]}]))),
        ("INSERT INTO tray_menu_configs (name, scope, scope_ref_id, payload_json) VALUES (%s, %s, %s, %s)",
         ("Other device menu", "device", 20, json.dumps([{"type": "rmm_script", "label": "Clean", "script_id": script_id}]))),
        ("INSERT INTO ai_health_check_remediations (detection_key, company_id, script_id) VALUES (%s, %s, %s)",
         ("volume_low_space", None, script_id)),
        ("UPDATE rmm_remote_control_providers SET activation_script_id = %s WHERE provider = %s", (script_id, "rustdesk")),
        ("INSERT INTO rmm_remote_control_providers (provider, is_enabled, activation_script_id) VALUES (%s, %s, %s)",
         ("meshcentral", 0, script_id)),
    ]
    for sql, params in statements:
        await fake.execute(sql, params)


def _names(result):
    return {group["key"]: [item["name"] for item in group["items"]] for group in result["groups"]}


@pytest.mark.anyio
async def test_usage_lists_every_link_for_super_admins(sqlite_db, monkeypatch):
    script, other = await _setup(monkeypatch)
    await _link_everywhere(sqlite_db, script["id"], other["id"])

    result = await usage.script_usage(script, company_id=None, is_super_admin=True)
    names = _names(result)
    assert names["schedules"] == ["Nightly cleanup", "Other company cleanup"]
    assert names["onboarding"] == ["Onboarding step 1"]
    assert names["checks"] == ["Disk space", "Script test"]
    assert names["event_log_rules"] == ["Legacy rule", "Other company rule"]
    assert names["result_rules"] == ["Clean after report"]
    assert names["automations"] == ["By ID", "Low disk ticket"]
    assert names["tray_menus"] == ["Contoso menu", "Other device menu"]
    assert names["health_checks"] == ["A drive is low on space"]
    assert names["remote_control"] == ["MeshCentral"]
    assert [group["key"] for group in result["groups"]] == [key for key, _label, _help in usage.GROUPS]

    checks = {item["name"]: item for item in result["groups"][2]["items"]}
    assert checks["Disk space"]["scope"] == "PC-01 (Contoso)" and checks["Disk space"]["detail"] == "Run a script when it fails"
    assert checks["Script test"]["detail"] == "Tests the device with this script"
    tray = {item["name"]: item for item in next(g for g in result["groups"] if g["key"] == "tray_menus")["items"]}
    assert "“Free up space”" in tray["Contoso menu"]["detail"] and tray["Other device menu"]["scope"] == "OTHER-PC (Other)"
    assert tray["Contoso menu"]["href"].startswith("/admin/tray/configurations/")
    automations = {item["name"]: item for item in next(g for g in result["groups"] if g["key"] == "automations")["items"]}
    assert automations["Low disk ticket"]["detail"] == "When: Ticket created" and automations["Low disk ticket"]["enabled"]
    assert result["all_companies"] and not result["unused"]


@pytest.mark.anyio
async def test_usage_is_limited_to_the_selected_company_for_technicians(sqlite_db, monkeypatch):
    script, other = await _setup(monkeypatch)
    await _link_everywhere(sqlite_db, script["id"], other["id"])

    names = _names(await usage.script_usage(script, company_id=1, is_super_admin=False))
    assert names["schedules"] == ["Nightly cleanup"]
    assert names["event_log_rules"] == ["Legacy rule"]
    assert names["tray_menus"] == ["Contoso menu"]
    result = await usage.script_usage(script, company_id=1, is_super_admin=False)
    tray = next(g for g in result["groups"] if g["key"] == "tray_menus")["items"][0]
    assert tray["href"] == ""  # the tray menu editor is for super admins


@pytest.mark.anyio
async def test_unlinked_script_without_runs_is_flagged_and_runs_span_companies(sqlite_db, monkeypatch):
    script, _other = await _setup(monkeypatch)
    result = await usage.script_usage(script, company_id=None, is_super_admin=True)
    assert result["groups"] == [] and result["links"] == 0 and result["unused"] and result["last_run_at"] is None

    old = (datetime.now(timezone.utc) - timedelta(days=200)).strftime("%Y-%m-%d %H:%M:%S")
    for company_id, asset_id, queued in ((1, 10, None), (2, 20, None), (2, 20, old)):
        await sqlite_db.execute(
            "INSERT INTO rmm_script_runs (script_id, script_name, script_path, language, script_content, content_sha256, "
            "company_id, asset_id, status" + (", queued_at" if queued else "") + ") VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s"
            + (", %s" if queued else "") + ")",
            (script["id"], "Cleanup", script["path"], "powershell", CLEANUP, "a" * 64, company_id, asset_id, "succeeded")
            + ((queued,) if queued else ()),
        )
    result = await usage.script_usage(script, company_id=None, is_super_admin=True)
    assert result["recent_runs"] == 2 and result["last_run_at"] and not result["unused"]
    assert (await usage.script_usage(script, company_id=1, is_super_admin=False))["recent_runs"] == 1

    everywhere = await routes._script_detail(script["id"], 1, is_super_admin=True)
    assert {run["company_name"] for run in everywhere["runs"]} == {"Contoso", "Other"} and len(everywhere["runs"]) == 3
    own = await routes._script_detail(script["id"], 1, is_super_admin=False)
    assert {run["company_id"] for run in own["runs"]} == {1} and own["usage"]["all_companies"] is False


def test_reference_matching_and_tray_items():
    script = {"id": 7, "name": "Cleanup", "path": "rmm/Common/disk/Cleanup.ps1"}
    assert usage.reference_matches("7", script) and usage.reference_matches("cleanup", script)
    assert usage.reference_matches("RMM/Common/disk/Cleanup.ps1", script)
    assert not usage.reference_matches("8", script) and not usage.reference_matches("", script)
    assert not usage.reference_matches("{{ticket.script}}", script)
    nodes = [{"type": "rmm_script", "script_id": "bad"}, {"type": "submenu", "children": [{"type": "rmm_script", "script_id": 7}]}]
    assert usage.tray_items_for(nodes, 7) == ["Untitled item"]
    broken = usage.automation_references("not json", "rmm-script")
    assert not any(usage.reference_matches(payload.get("script"), script) for payload in broken)


def _usage(**overrides):
    data = {"groups": [], "links": 0, "active_links": 0, "recent_runs": 0, "recent_days": 90, "last_run_at": None,
            "unused": True, "all_companies": False}
    data.update(overrides)
    return data


def test_section_renders_links_runs_and_unused_warning():
    html = _render_library(selected_script=_detail(usage=_usage()))
    assert "Usage and recent runs" in html and "may no longer be needed" in html
    assert "Other companies may still use it" in html and "is not linked to any schedule" in html

    groups = [{"key": "schedules", "label": "Schedules", "help": "Runs on a timetable.", "items": [
        {"name": "<b>Nightly</b>", "scope": "All companies", "enabled": True, "detail": "Cron 0 2 * * *",
         "href": "/rmm/automation", "when": "2026-11-26T02:00:00", "when_label": "Next run"}]}]
    runs = [{"id": 5, "script_id": 7, "script_name": "Check-Disk", "company_id": 2, "company_name": "Other",
             "asset_id": 20, "asset_name": "OTHER-PC", "status": "succeeded", "exit_code": 0, "run_source": "manual",
             "queued_at": "2026-11-20T00:00:00"}]
    html = _render_library(selected_script=_detail(runs=runs, usage=_usage(
        groups=groups, links=1, active_links=1, recent_runs=1, unused=False, all_companies=True,
        last_run_at="2026-11-20T00:00:00")))
    assert "may no longer be needed" not in html and "&lt;b&gt;Nightly&lt;/b&gt;" in html
    assert 'href="/rmm/automation"' in html and "Next run" in html and "1 link" in html
    assert 'data-rmm-run-row-asset="20"' in html and 'data-label="Company"' in html and "Other" in html
    assert "every company&#39;s devices" in html or "every company's devices" in html
