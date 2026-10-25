"""Health check detections: their event log monitors, the scripts that fix them and the monthly checks."""
from __future__ import annotations

import inspect
import json
import re
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from app.repositories import ai_health_check_groups as groups_repo
from app.repositories import ai_health_check_remediations as remediation_repo
from app.repositories import ai_health_check_schedules as schedule_repo
from app.repositories import ai_health_checks as repo
from app.repositories import rmm as rmm_repo
from app.services import ai_health_check as hc
from app.services import ai_health_check_schedule as schedule
from app.services import health_check_detections as detections
from app.services import health_check_remediation as remediation
from app.services import rmm_event_log_rules
from app.services import tray as tray_service
from tests.test_ai_health_check import WINDOWS_SNAPSHOT, _render, client, store  # noqa: F401 - fixtures
from tests.test_rmm_scripts import anyio_backend  # noqa: F401 - fixture

NOW = datetime(2026, 10, 25, 9, 30, 0)


# --------------------------------------------------------------------------- #
# The catalog
# --------------------------------------------------------------------------- #


def test_every_rule_reports_a_known_detection_and_every_detection_is_reported():
    source = inspect.getsource(hc)
    reported = set(re.findall(r'_signal\("(\w+)"', source)) | {"cpu_temperature_high", "gpu_temperature_high"}
    assert reported == set(detections.DETECTIONS)
    for key, item in detections.DETECTIONS.items():
        assert item["area"] in hc.AREAS, key
        assert item["severity"] in {"critical", "warning", "info"}, key
        assert set(item["platforms"]) <= {"windows", "macos"}, key
        assert all(name in detections.MONITORS for name in item["monitors"]), key
    referenced = {name for item in detections.DETECTIONS.values() for name in item["monitors"]}
    assert referenced == set(detections.MONITORS)


def test_every_monitor_is_a_valid_event_log_rule():
    for key, monitor in detections.MONITORS.items():
        conditions = monitor["conditions"]
        assert conditions[0]["field"] == "log_name" and conditions[0]["value"] == monitor["log"], key
        parsed = rmm_event_log_rules.parse_conditions(
            [c["field"] for c in conditions], [c["operator"] for c in conditions], [c["value"] for c in conditions])
        assert parsed == conditions, key
        assert monitor["why"] and monitor["name"], key
    # The monitors match the events they describe.
    bad_block = {"log_name": "System", "provider": "disk", "event_id": 7, "level": 2, "message": "bad block"}
    assert rmm_event_log_rules.rule_matches(detections.MONITORS["disk_errors"], bad_block)
    assert not rmm_event_log_rules.rule_matches(detections.MONITORS["disk_errors"], {**bad_block, "event_id": 8})
    healthy = {"log_name": "System", "provider": "Microsoft-Windows-Ntfs", "event_id": 98, "level": 4, "message": "ok"}
    assert not rmm_event_log_rules.rule_matches(detections.MONITORS["ntfs_corruption"], healthy)
    assert rmm_event_log_rules.rule_matches(detections.MONITORS["ntfs_corruption"], {**healthy, "level": 3})
    memory_test = {"log_name": "System", "provider": "Microsoft-Windows-MemoryDiagnostics-Results", "event_id": 1202,
                   "level": 2, "message": "detected hardware errors"}
    assert rmm_event_log_rules.rule_matches(detections.MONITORS["memory_diagnostics"], memory_test)
    assert not rmm_event_log_rules.rule_matches(detections.MONITORS["memory_diagnostics"], {**memory_test, "level": 4})
    start_type = {"log_name": "System", "provider": "Service Control Manager", "event_id": 7040, "level": 4,
                  "message": "The start type of the Windows Update service was changed from demand start to disabled."}
    assert rmm_event_log_rules.rule_matches(detections.MONITORS["update_service_changed"], start_type)
    assert not rmm_event_log_rules.rule_matches(
        detections.MONITORS["update_service_changed"], {**start_type, "message": "The start type of the Print Spooler service was changed."})


def test_signals_carry_their_detection_key():
    signals = hc.evaluate_snapshot(WINDOWS_SNAPSHOT)
    by_title = {signal["title"]: signal for signal in signals}
    assert by_title["The Windows component store is damaged but repairable"]["key"] == "component_store_repairable"
    assert by_title["C: is almost full (3.5% free)"]["key"] == "volume_almost_full"
    assert by_title["CPU temperature is 97 °C"]["key"] == "cpu_temperature_high"
    assert all(signal["area"] == detections.DETECTIONS[signal["key"]]["area"] for signal in signals)


def test_monitors_for_signals_lists_each_monitor_once_with_what_it_watches():
    signals = hc.evaluate_snapshot(WINDOWS_SNAPSHOT)
    monitors = detections.monitors_for_signals(signals)
    keys = [monitor["key"] for monitor in monitors]
    assert len(keys) == len(set(keys)) and "disk_errors" in keys and "update_failures" in keys
    disk = next(monitor for monitor in monitors if monitor["key"] == "disk_errors")
    assert "Old HDD is failing or about to fail" in disk["detections"] and disk["origin"] == "catalog"
    assert detections.monitors_for_signals([{"key": "cpu_busy", "title": "x"}, {"title": "no key"}]) == []


def test_model_monitors_are_checked_like_the_rule_form():
    known = [detections.monitor("disk_errors")]
    entries = [
        {"title": "Storport timeouts", "why": "Seen at 10:00", "log": "Microsoft-Windows-Storage-Storport/Operational",
         "source": "Microsoft-Windows-StorPort", "event_ids": [549, "549", "x", 70000], "level": "Error"},
        {"title": "Bad path", "log": "../System", "event_ids": [1]},
        {"title": "No event", "log": "System"},
        {"title": "Same as the catalog", "log": "System", "source": "disk", "event_ids": [7, 11, 51, 52, 153, 154]},
        {"title": "Message only", "log": "Application", "source": "Vendor", "message_contains": "<b>failed</b>"},
        "not a dict",
    ]
    monitors = detections.parse_model_monitors(entries, "windows", known=known)
    assert [m["name"] for m in monitors] == ["Storport timeouts", "Message only"]
    storport = monitors[0]
    assert storport["origin"] == "model" and storport["key"] is None
    assert {(c["field"], c["operator"], c["value"]) for c in storport["conditions"]} == {
        ("log_name", "is", "Microsoft-Windows-Storage-Storport/Operational"),
        ("provider", "is", "Microsoft-Windows-StorPort"), ("event_id", "is", "549"), ("level", "at_least", "Error")}
    assert ("message", "contains", "failed") in {(c["field"], c["operator"], c["value"]) for c in monitors[1]["conditions"]}
    assert detections.parse_model_monitors(entries, "macos") == []
    many = [{"log": "System", "event_ids": [index]} for index in range(1, 20)]
    assert len(detections.parse_model_monitors(many, "windows")) == detections.MAX_MODEL_MONITORS


def test_present_monitor_and_rule_prefill():
    monitor = detections.monitor("slow_boot")
    shown = detections.present_monitor(monitor, ["Application", "System"])
    assert shown["log_collected"] is False and shown["conditions_text"][0].startswith("Log is")
    assert detections.present_monitor(detections.monitor("bugcheck"), ["system"])["log_collected"] is True
    prefill = detections.rule_prefill(monitor)
    assert prefill["action"] == "create_ticket" and prefill["name"] == "Slow start-up"
    assert prefill["log"] == "Microsoft-Windows-Diagnostics-Performance/Operational"


# --------------------------------------------------------------------------- #
# Monthly schedule
# --------------------------------------------------------------------------- #


def test_slots_fall_in_a_random_time_of_the_right_month():
    for _ in range(50):
        run_at, ends = schedule.first_slot(NOW)
        assert NOW <= run_at < NOW + timedelta(days=30)
        assert ends == schedule.next_month_start(run_at) and ends.day == 1
        run_at, ends = schedule.next_slot(datetime(2026, 11, 1), NOW)
        assert datetime(2026, 11, 1) <= run_at < datetime(2026, 12, 1) and ends == datetime(2026, 12, 1)
        run_at, ends = schedule.next_slot(datetime(2027, 1, 1), datetime(2026, 12, 20))
        assert datetime(2027, 1, 1) <= run_at < datetime(2027, 2, 1) and ends == datetime(2027, 2, 1)
        # A month that was missed entirely runs later in the current month.
        run_at, ends = schedule.next_slot(datetime(2026, 8, 1), NOW)
        assert NOW <= run_at < datetime(2026, 11, 1) and ends == datetime(2026, 11, 1)
    assert schedule.month_label(datetime(2026, 11, 1)) == "October 2026"


@pytest.fixture
def scheduled(store, monkeypatch):
    conn = store["db"].conn
    online: set[str] = set()
    assets = {10: {"id": 10, "company_id": 1, "name": "PC-01", "os_name": "Windows 11 Pro"},
              11: {"id": 11, "company_id": 1, "name": "PC-02", "os_name": "Windows 11 Pro"}}

    async def get_asset(asset_id):
        return assets.get(asset_id)

    audits = []

    async def log_action(**kwargs):
        audits.append(kwargs)

    monkeypatch.setattr(schedule.assets_repo, "get_asset_by_id", get_asset)
    monkeypatch.setattr(schedule.audit_service, "log_action", log_action)
    monkeypatch.setattr(tray_service, "is_device_connected", lambda uid: uid in online)
    monkeypatch.setattr(schedule, "get_component_availability",
                        lambda: SimpleNamespace(feature_pack_available=lambda slug: True))
    monkeypatch.setitem(schedule._state, "last_seeded", None)
    monkeypatch.setattr(schedule.get_settings(), "health_check_monthly_enabled", True)
    return {"conn": conn, "online": online, "assets": assets, "audits": audits}


async def _set_due(conn, asset_id, next_run_at, window_ends_at=None):
    if window_ends_at is None:
        await conn.execute("UPDATE ai_health_check_schedules SET next_run_at = ? WHERE asset_id = ?",
                           (next_run_at, asset_id))
    else:
        await conn.execute("UPDATE ai_health_check_schedules SET next_run_at = ?, window_ends_at = ? WHERE asset_id = ?",
                           (next_run_at, window_ends_at, asset_id))


@pytest.mark.anyio
async def test_monthly_checks_are_scheduled_started_and_moved_on(store, scheduled):
    conn = scheduled["conn"]

    async def _due(asset_id, next_run_at, window_ends_at=None):
        await _set_due(conn, asset_id, next_run_at, window_ends_at)

    await conn.executescript("""
        INSERT INTO rmm_agents (id, asset_id, company_id, status) VALUES
          (1, 10, 1, 'active'), (2, 11, 1, 'active'), (3, 20, 2, 'revoked'), (4, 10, 1, 'active');
    """)
    assert await schedule.tick(NOW) == 0
    rows = {row["asset_id"]: row for row in await store["db"].fetch_all("SELECT * FROM ai_health_check_schedules")}
    assert set(rows) == {10, 11}  # one row per asset, none for revoked agents
    for row in rows.values():
        run_at = datetime.fromisoformat(str(row["next_run_at"]))
        assert NOW <= run_at < NOW + timedelta(days=30)

    # Due and online: an ordinary health check starts, and the next is next month.
    scheduled["online"].add("dev-1")
    await _due(10, next_run_at=NOW - timedelta(minutes=1), window_ends_at=datetime(2026, 11, 1))
    await _due(11, next_run_at=NOW - timedelta(minutes=1), window_ends_at=datetime(2026, 11, 1))
    assert await schedule.tick(NOW) == 1
    check = (await repo.list_checks(asset_id=10))[0]
    assert check["run_source"] == "scheduled" and check["requested_by_user_id"] is None
    assert hc.present(check)["is_scheduled"] is True
    assert store["sent"][-1][0] == "dev-1" and store["sent"][-1][1]["type"] == "health_check"
    row = await schedule_repo.get(10)
    assert datetime(2026, 11, 1) <= datetime.fromisoformat(str(row["next_run_at"])) < datetime(2026, 12, 1)
    assert row["last_health_check_id"] == check["id"] and row["last_result"] == f"Started health check #{check['id']}."
    assert scheduled["audits"][-1]["action"] == "health_check.start"
    assert scheduled["audits"][-1]["new_value"]["run_source"] == "scheduled"

    # Offline: tried again in half an hour, in the same month.
    row = await schedule_repo.get(11)
    assert datetime.fromisoformat(str(row["next_run_at"])) == NOW + schedule.OFFLINE_RETRY
    assert "come online" in row["last_result"] and await repo.list_checks(asset_id=11) == []

    # Still offline when the month ends: on to the next month.
    later = datetime(2026, 11, 1, 0, 5)
    await _due(10, next_run_at=datetime(2026, 11, 20))
    await _due(11, next_run_at=datetime(2026, 10, 31, 23, 59))
    await schedule.tick(later)
    row = await schedule_repo.get(11)
    assert row["last_result"].startswith("Not run in October 2026")
    assert datetime(2026, 11, 1) <= datetime.fromisoformat(str(row["next_run_at"])) < datetime(2026, 12, 1)

    # A month with a completed health check is skipped.
    await repo.update(check["id"], status="completed", completed=True)
    await conn.execute("UPDATE ai_health_checks SET created_at = ? WHERE id = ?", (NOW - timedelta(days=2), check["id"]))
    await _due(10, next_run_at=NOW, window_ends_at=datetime(2026, 11, 1))
    await schedule.tick(NOW + timedelta(minutes=5))
    row = await schedule_repo.get(10)
    assert row["last_result"].startswith("Skipped October 2026") and len(await repo.list_checks(asset_id=10)) == 1

    # Assets that lose their RMM agent lose their schedule.
    agent = store["agents"].pop(11)
    await _due(11, next_run_at=NOW, window_ends_at=datetime(2026, 11, 1))
    await schedule.tick(NOW + timedelta(minutes=6))
    assert await schedule_repo.get(11) is None
    store["agents"][11] = agent


@pytest.mark.anyio
async def test_monthly_checks_wait_for_the_setting_and_a_model(store, scheduled, monkeypatch):
    await scheduled["conn"].execute("INSERT INTO rmm_agents (id, asset_id, company_id, status) VALUES (1, 10, 1, 'active')")
    monkeypatch.setattr(schedule.get_settings(), "health_check_monthly_enabled", False)
    assert await schedule.tick(NOW) == 0 and await schedule_repo.get(10) is None
    assert await schedule.schedule_for(10) is None
    monkeypatch.setattr(schedule.get_settings(), "health_check_monthly_enabled", True)
    monkeypatch.setattr(tray_service, "build_troubleshoot_llm_config", lambda model=None: {"model": ""})
    assert await schedule.tick(NOW) == 0 and await schedule_repo.get(10) is None
    monkeypatch.setattr(tray_service, "build_troubleshoot_llm_config", lambda model=None: {"model": "m", "base_url": "x"})
    await schedule.tick(NOW)
    assert (await schedule.schedule_for(10))["asset_id"] == 10


# --------------------------------------------------------------------------- #
# Remediation scripts
# --------------------------------------------------------------------------- #

SCRIPTS = {
    1: {"id": 1, "name": "Repair component store", "path": "Common/Repair.ps1", "company_id": None, "is_active": True,
        "default_timeout_seconds": 3600, "parameters": [{"name": "Reason", "type": "string"}], "env_vars": []},
    2: {"id": 2, "name": "Contoso clean-up", "path": "Companies/Contoso/Clean.ps1", "company_id": 1, "is_active": True,
        "default_timeout_seconds": 600, "parameters": [], "env_vars": []},
    3: {"id": 3, "name": "Other company", "path": "Companies/Other/x.ps1", "company_id": 2, "is_active": True,
        "default_timeout_seconds": 600, "parameters": [], "env_vars": []},
}


@pytest.fixture
def scripts(store, monkeypatch):
    conn = store["db"].conn

    async def get_script(script_id, with_content=False):
        return dict(SCRIPTS[int(script_id)]) if int(script_id) in SCRIPTS else None

    async def list_scripts(company_id=None):
        return [dict(s) for s in SCRIPTS.values() if company_id is None or s["company_id"] in (None, company_id)]

    runs = []

    async def run_script(**kwargs):
        runs.append(kwargs)
        return 500 + len(runs), f"Ran {SCRIPTS[kwargs['script_id']]['name']} on PC-01", True

    monkeypatch.setattr(rmm_repo, "get_script", get_script)
    monkeypatch.setattr(rmm_repo, "list_scripts", list_scripts)
    monkeypatch.setattr(remediation.rmm_actions, "run_script", run_script)

    async def setup():
        for script in SCRIPTS.values():
            await conn.execute("INSERT INTO rmm_scripts (id, name, path, company_id, is_active) VALUES (?, ?, ?, ?, 1)",
                               (script["id"], script["name"], script["path"], script["company_id"]))

    return {"runs": runs, "setup": setup}


@pytest.mark.anyio
async def test_links_are_scoped_and_validated(store, scripts):
    await scripts["setup"]()
    save = remediation.save_link
    with pytest.raises(remediation.RemediationError, match="Only super admins"):
        await save(detection_key="component_store_repairable", scope="global", company_id=1,
                   form={"script_id": "1", "is_enabled": "1"}, user_id=9, is_super_admin=False)
    with pytest.raises(remediation.RemediationError, match="Common"):
        await save(detection_key="component_store_repairable", scope="global", company_id=1,
                   form={"script_id": "2", "is_enabled": "1"}, user_id=9, is_super_admin=True)
    with pytest.raises(remediation.RemediationError, match="Choose a script"):
        await save(detection_key="component_store_repairable", scope="company", company_id=1,
                   form={"script_id": "3", "is_enabled": "1"}, user_id=9, is_super_admin=True)
    with pytest.raises(remediation.RemediationError, match="detection"):
        await save(detection_key="nope", scope="company", company_id=1, form={}, user_id=9, is_super_admin=True)

    global_id = await save(detection_key="component_store_repairable", scope="global", company_id=1,
                           form={"script_id": "1", "is_enabled": "1", "timeout_seconds": "5"}, user_id=9,
                           is_super_admin=True)
    link = await remediation_repo.get(global_id, with_secrets=True)
    assert link["company_id"] is None and link["script_name"] == "Repair component store"
    assert link["is_enabled"] and link["timeout_seconds"] == 10 and link["entries_encrypted"] is None
    # The script's values are read once it is the saved script, and kept encrypted.
    again = await save(detection_key="component_store_repairable", scope="global", company_id=1,
                       form={"script_id": "1", "is_enabled": "1", "entry:param:Reason": "{{health.title}}"},
                       user_id=9, is_super_admin=True)
    assert again == global_id
    link = await remediation_repo.get(global_id, with_secrets=True)
    assert "health.title" not in str(link["entries_encrypted"]) and link["inputs"][0]["value"] == "{{health.title}}"
    fields = remediation.script_values(link, SCRIPTS[1])
    assert fields[0]["value"] == "{{health.title}}"

    # The company's own link wins; without a script it turns the fix off there.
    links = await remediation_repo.list_for_company(1)
    assert remediation.resolve(links, "component_store_repairable", 1)["id"] == global_id
    own = await save(detection_key="component_store_repairable", scope="company", company_id=1,
                     form={"script_id": "", "is_enabled": "1"}, user_id=9, is_super_admin=False)
    links = await remediation_repo.list_for_company(1)
    chosen = remediation.resolve(links, "component_store_repairable", 1)
    assert chosen["id"] == own and not chosen["is_enabled"] and chosen["script_id"] is None
    assert remediation.resolve(await remediation_repo.list_for_company(2), "component_store_repairable", 2)["id"] == global_id


@pytest.mark.anyio
async def test_linked_scripts_run_once_when_a_check_finds_the_detection(store, scripts, monkeypatch):
    await scripts["setup"]()
    for key, script in (("component_store_repairable", "1"), ("volume_almost_full", "2"),
                        ("volume_dirty", "2"), ("ntfs_errors", "1")):
        await remediation.save_link(detection_key=key, scope="company", company_id=1,
                                    form={"script_id": script, "is_enabled": "1"}, user_id=9, is_super_admin=True)
    await remediation.save_link(detection_key="trim_disabled", scope="company", company_id=1,
                                form={"script_id": "2"}, user_id=9, is_super_admin=True)  # off
    check_id = await repo.create(company_id=1, asset_id=10, tray_device_id=5, ticket_id=None, platform="windows",
                                 model="m", allow_pawnio=False, requested_by_user_id=None)
    await repo.update(check_id, status="analysing", snapshot=WINDOWS_SNAPSHOT, logs={"sources": []})

    async def chat(llm, messages):
        assert "MONITORS_ALREADY_SUGGESTED:\n- " in messages[0]["content"]
        assert "\n- Disk errors before a drive fails: Log is “System”" in messages[0]["content"]
        return json.dumps({"overall_status": "critical", "summary": "Fix it", "steps": [], "monitors": [
            {"title": "Storport timeouts", "log": "Microsoft-Windows-Storage-Storport/Operational",
             "source": "Microsoft-Windows-StorPort", "event_ids": [549], "why": "Seen in the logs"}]})

    monkeypatch.setattr(hc, "_chat", chat)
    await hc.run_report(check_id)
    report = (await repo.get(check_id))["report"]
    fixes = report["remediations"]
    # One run per script, in the order the detections were found (most severe first).
    assert [(f["detection"], f["run_id"] is not None) for f in fixes] == [
        ("volume_almost_full", True), ("volume_dirty", False), ("ntfs_errors", True),
        ("component_store_repairable", False)]
    assert fixes[1]["summary"].startswith("Already queued") and fixes[0]["summary"].startswith("Queued Contoso clean-up")
    assert [run["script_id"] for run in scripts["runs"]] == [2, 1]
    run = scripts["runs"][0]
    assert run["run_source"] == "health_check" and run["agent"]["id"] == 1
    assert run["extra_context"]["health"]["detection"] == "volume_almost_full"
    assert run["extra_context"]["health"]["title"] == "C: is almost full (3.5% free)"
    assert run["extra_context"]["health"]["check_id"] == check_id
    link = await remediation_repo.get_for_scope("volume_almost_full", 1)
    assert link["run_count"] == 1 and link["last_summary"].startswith("Queued")

    # Event log monitors: the catalog's for what was found, then the model's own.
    monitors = report["monitors"]
    assert monitors[0]["origin"] == "catalog" and monitors[-1]["name"] == "Storport timeouts"
    assert monitors[-1]["origin"] == "model" and len({m["name"] for m in monitors}) == len(monitors)
    note = hc.report_note({"id": check_id, "asset_name": "PC-01"}, "critical", 0, report)
    assert "Automatic fixes" in note and "Queued Contoso clean-up" in note


@pytest.mark.anyio
async def test_no_scripts_run_without_an_rmm_agent_or_on_other_platforms(store, scripts):
    await scripts["setup"]()
    await remediation.save_link(detection_key="volume_almost_full", scope="company", company_id=1,
                                form={"script_id": "2", "is_enabled": "1"}, user_id=9, is_super_admin=True)
    signals = [{"key": "volume_almost_full", "title": "C: is almost full", "severity": "critical"}]
    store["agents"][10]["company_id"] = 2
    results = await remediation.run_for_check({"id": 1, "company_id": 1, "asset_id": 10}, signals)
    assert results[0]["run_id"] is None and "no active RMM agent" in results[0]["summary"]
    assert scripts["runs"] == []
    store["agents"][10]["company_id"] = 1
    assert await remediation.run_for_check({"id": 1, "company_id": 1, "asset_id": 10}, []) == []


@pytest.mark.anyio
async def test_links_can_raise_a_ticket_and_update_it_while_open(store, scripts, monkeypatch):
    await scripts["setup"]()
    # Every company: a ticket alone is enough, without a script.
    with pytest.raises(remediation.RemediationError, match="Choose a script, or create a ticket"):
        await remediation.save_link(detection_key="blue_screens", scope="global", company_id=1,
                                    form={"is_enabled": "1"}, user_id=9, is_super_admin=True)
    link_id = await remediation.save_link(detection_key="blue_screens", scope="global", company_id=1,
                                          form={"create_ticket": "1", "is_enabled": "1"}, user_id=9,
                                          is_super_admin=True)
    link = await remediation_repo.get(link_id)
    assert link["create_ticket"] and link["is_enabled"] and link["script_id"] is None
    await remediation.save_link(detection_key="volume_almost_full", scope="company", company_id=1,
                                form={"script_id": "2", "create_ticket": "1", "is_enabled": "1"}, user_id=9,
                                is_super_admin=True)

    tickets: dict[int, dict] = {}
    updates: list[tuple[int, str, str]] = []

    async def raise_ticket(*, agent, subject, description, reference, priority="normal"):
        ticket = {"id": 70 + len(tickets), "ticket_number": None, "status": "open", "agent": agent,
                  "subject": subject, "description": description, "reference": reference}
        tickets[ticket["id"]] = ticket
        return ticket

    async def open_ticket(ticket_id):
        ticket = tickets.get(ticket_id)
        return ticket if ticket and ticket["status"] != "closed" else None

    async def add_ticket_update(ticket, *, body, reference):
        updates.append((ticket["id"], body, reference))

    monkeypatch.setattr(remediation.rmm_actions, "raise_ticket", raise_ticket)
    monkeypatch.setattr(remediation.rmm_actions, "open_ticket", open_ticket)
    monkeypatch.setattr(remediation.rmm_actions, "add_ticket_update", add_ticket_update)
    signals = [{"key": "volume_almost_full", "title": "C: is almost full", "severity": "critical"},
               {"key": "blue_screens", "title": "3 blue <b>screens</b>", "detail": "in 30 days", "severity": "critical"}]
    check = {"id": 4, "company_id": 1, "asset_id": 10, "asset_name": "PC-01"}
    results = await remediation.run_for_check(check, signals)
    by_kind = [(r["detection"], r.get("kind", "script")) for r in results]
    assert by_kind == [("volume_almost_full", "script"), ("volume_almost_full", "ticket"), ("blue_screens", "ticket")]
    assert [r["ticket_id"] for r in results[1:]] == [70, 71]
    raised = tickets[71]
    assert raised["subject"] == detections.DETECTIONS["blue_screens"]["label"] + ": PC-01"
    assert raised["agent"] == {"company_id": 1, "asset_id": 10}
    assert raised["reference"] == "health-check-detection:blue_screens:10"
    assert "3 blue &lt;b&gt;screens&lt;/b&gt; — in 30 days" in raised["description"]
    assert 'href="/health-checks/4"' in raised["description"]
    assert results[2]["summary"] == "Raised ticket #71 for PC-01."
    # The ticket-only link counts its runs; a script link counts the script once.
    assert (await remediation_repo.get(link_id))["run_count"] == 1
    assert (await remediation_repo.get_for_scope("volume_almost_full", 1))["run_count"] == 1

    # Found again while the ticket is open: an update, not another ticket.
    results = await remediation.run_for_check({**check, "id": 5}, signals[1:])
    assert results[0]["ticket_id"] == 71 and results[0]["summary"] == "Updated ticket #71 for PC-01."
    assert updates[0][0] == 71 and "again" in updates[0][1] and len(tickets) == 2
    # Once it is closed, a new ticket.
    tickets[71]["status"] = "closed"
    results = await remediation.run_for_check({**check, "id": 6}, signals[1:])
    assert results[0]["ticket_id"] == 72 and await remediation_repo.get_ticket("blue_screens", 10) == 72

    # Turned off, or with neither a script nor a ticket for the company, nothing happens.
    await remediation.save_link(detection_key="blue_screens", scope="company", company_id=1,
                                form={"create_ticket": "1"}, user_id=9, is_super_admin=False)
    assert await remediation.run_for_check({**check, "id": 7}, signals[1:]) == []


@pytest.mark.anyio
async def test_assets_reporting_a_detection_come_from_their_latest_check(store):
    from app.features.health_check.detection_routes import reporting_assets

    async def check(asset_id, status, signals, company_id=1):
        check_id = await repo.create(company_id=company_id, asset_id=asset_id, tray_device_id=None, ticket_id=None,
                                     platform="windows", model="m", allow_pawnio=False, requested_by_user_id=None)
        await repo.update(check_id, status=status, report={"signals": signals}, completed=status == "completed")
        return check_id

    full = {"key": "volume_almost_full", "title": "C: is almost full", "severity": "critical"}
    low = {"key": "volume_low_space", "title": "D: is low on space", "severity": "warning"}
    await check(10, "completed", [full])
    latest_10 = await check(10, "completed", [low, {**full, "title": "E: is almost full", "severity": "warning"}, full])
    await check(10, "queued", [])  # not completed yet: the last completed check still counts
    await check(11, "completed", [full])
    await check(11, "completed", [])  # fixed since
    await check(20, "completed", [full], company_id=2)
    found = reporting_assets(await repo.latest_completed_for_company(1))
    assert set(found) == {"volume_almost_full", "volume_low_space"}
    entry = found["volume_almost_full"][0]
    assert len(found["volume_almost_full"]) == 1 and entry["asset_id"] == 10 and entry["asset_name"] == "PC-01"
    assert entry["check_id"] == latest_10 and entry["severity"] == "critical"
    assert entry["titles"] == ["E: is almost full", "C: is almost full"]
    assert reporting_assets([{"id": 1, "asset_id": 3, "report": {"signals": ["junk", {"title": "no key"}]}}]) == {}


# --------------------------------------------------------------------------- #
# Event log rules from a health check
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_event_log_rule_form_is_filled_in_from_a_monitor(store, monkeypatch):
    from app.features.rmm import event_log_rule_routes as rule_routes

    assets = {10: {"id": 10, "company_id": 1}, 20: {"id": 20, "company_id": 2}}

    async def get_asset(asset_id):
        return assets.get(asset_id)

    async def logs_for(company_id, asset_id):
        return ["Application", "System"]

    monkeypatch.setattr(rule_routes.assets_repo, "get_asset_by_id", get_asset)
    monkeypatch.setattr(rule_routes.event_logs, "logs_for", logs_for)
    rule, problem = await rule_routes._suggested_rule(1, 10, "slow_boot", None, None)
    assert problem is None and rule["scope"] == "company" and rule["asset_id"] == 10
    assert rule["action"] == "create_ticket" and rule["name"] == "Slow start-up"
    assert rule["conditions"][0] == {"field": "log_name", "operator": "is",
                                     "value": "Microsoft-Windows-Diagnostics-Performance/Operational"}
    assert rule["suggestion"]["log_collected"] is False
    assert (await rule_routes._suggested_rule(1, 20, "slow_boot", None, None))[1].startswith("That device belongs")
    assert (await rule_routes._suggested_rule(1, None, "nope", None, None))[1].startswith("That suggested rule")

    check_id = await repo.create(company_id=1, asset_id=10, tray_device_id=5, ticket_id=None, platform="windows",
                                 model="m", allow_pawnio=False, requested_by_user_id=None)
    model = detections.parse_model_monitors([{"title": "Vendor", "log": "System", "source": "Vendor", "event_ids": [9]}],
                                            "windows")
    await repo.update(check_id, report={"monitors": model})
    rule, problem = await rule_routes._suggested_rule(1, None, None, check_id, 0)
    assert problem is None and rule["name"] == "Vendor" and rule["asset_id"] == 10
    assert rule["suggestion"]["log_collected"] is True
    assert (await rule_routes._suggested_rule(2, None, None, check_id, 0))[1].startswith("That health check belongs")
    assert (await rule_routes._suggested_rule(1, None, None, check_id, 5))[1].startswith("That suggested rule")


# --------------------------------------------------------------------------- #
# Pages
# --------------------------------------------------------------------------- #


@pytest.fixture
def detection_client(client, scripts, monkeypatch):
    from app.features.health_check import detection_routes

    test_client, state, rendered, audits = client
    test_client.app.include_router(detection_routes.router)

    async def get_company(company_id):
        return {"id": company_id, "name": "Contoso"}

    async def logs_for(company_id, asset_id):
        return ["Application", "System"]

    async def variables():
        return []

    monkeypatch.setattr(detection_routes.company_repo, "get_company_by_id", get_company)
    monkeypatch.setattr(detection_routes.rmm_event_logs, "logs_for", logs_for)
    monkeypatch.setattr(detection_routes.rmm_scripts, "available_variables", variables)
    return test_client, state, rendered, audits


@pytest.mark.anyio
async def test_detections_page_links_and_removes_scripts(detection_client, scripts):
    await scripts["setup"]()
    test_client, state, rendered, audits = detection_client
    assert test_client.get("/health-checks/detections").status_code == 200
    template, extra = rendered[-1]
    assert template == "health_checks/detections.html" and extra["editor"] is None
    keys = [item["key"] for group in extra["groups"] for item in group["items"]]
    assert sorted(keys) == sorted(detections.DETECTIONS) and extra["counts"]["with_script"] == 0
    store_item = next(i for g in extra["groups"] for i in g["items"] if i["key"] == "component_store_repairable")
    assert "RestoreHealth" in store_item["remedy"]
    assert store_item["monitors"][0]["url"] == "/rmm/event-logs/rules?new=1&suggest=update_failures"

    assert test_client.get("/health-checks/detections?edit=component_store_repairable&scope=global").status_code == 200
    editor = rendered[-1][1]["editor"]
    assert editor["scope"] == "global" and [s["id"] for s in editor["scripts"]] == [1]

    response = test_client.post("/health-checks/detections/component_store_repairable",
                                data={"scope": "global", "script_id": "1", "is_enabled": "1"}, follow_redirects=False)
    assert response.status_code == 303 and "edit=component_store_repairable" in response.headers["location"]
    assert audits[-1]["action"] == "health_check.remediation.save"
    response = test_client.post("/health-checks/detections/component_store_repairable",
                                data={"scope": "global", "script_id": "2", "is_enabled": "1"})
    assert response.status_code == 400 and rendered[-1][1]["editor"]["errors"]
    test_client.get("/health-checks/detections")
    assert rendered[-1][1]["counts"]["with_script"] == 1

    # Company users with write access manage their own company only.
    state["user"] = {"id": 12, "is_super_admin": False}
    test_client.get("/health-checks/detections?edit=component_store_repairable&scope=global")
    assert rendered[-1][1]["editor"] is None
    response = test_client.post("/health-checks/detections/component_store_repairable",
                                data={"scope": "global", "script_id": "1"}, follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"] == "/health-checks/detections"
    assert test_client.post("/health-checks/detections/component_store_repairable/remove",
                            data={"scope": "global"}).status_code == 403
    response = test_client.post("/health-checks/detections/volume_low_space",
                                data={"scope": "company", "script_id": "2", "is_enabled": "1"}, follow_redirects=False)
    assert response.headers["location"] == "/health-checks/detections#detection-volume_low_space"
    assert (await remediation_repo.get_for_scope("volume_low_space", 1))["script_id"] == 2
    response = test_client.post("/health-checks/detections/volume_low_space/remove", data={"scope": "company"},
                                follow_redirects=False)
    assert response.status_code == 303 and await remediation_repo.get_for_scope("volume_low_space", 1) is None
    assert test_client.post("/health-checks/detections/nope", data={"scope": "company"}).status_code == 404
    state["rmm_write"] = False
    assert test_client.post("/health-checks/detections/volume_low_space", data={"scope": "company"}).status_code == 403


@pytest.mark.anyio
async def test_detections_api(detection_client, scripts):
    await scripts["setup"]()
    test_client, state, _rendered, _audits = detection_client
    response = test_client.put("/api/health-checks/detections/trim_disabled/remediation",
                               json={"company_id": 1, "script_id": 2, "values": {}})
    assert response.status_code == 200 and response.json()["remediation"]["script_name"] == "Contoso clean-up"
    items = {item["key"]: item for item in test_client.get("/api/health-checks/detections?company_id=1").json()["detections"]}
    assert items["trim_disabled"]["company_link"]["script_id"] == 2 and items["trim_disabled"]["runs_script"]
    assert test_client.put("/api/health-checks/detections/trim_disabled/remediation",
                           json={"script_id": 2}).status_code == 400  # every company: Common scripts only
    state["user"] = {"id": 12, "is_super_admin": False}
    assert test_client.put("/api/health-checks/detections/trim_disabled/remediation",
                           json={"script_id": 1}).status_code == 403
    assert test_client.delete("/api/health-checks/detections/trim_disabled/remediation").status_code == 403
    assert test_client.delete("/api/health-checks/detections/trim_disabled/remediation?company_id=1").status_code == 200
    assert test_client.delete("/api/health-checks/detections/trim_disabled/remediation?company_id=1").status_code == 404


@pytest.mark.anyio
async def test_report_page_offers_monitors_and_shows_fixes(detection_client, store, monkeypatch):
    test_client, _state, rendered, _audits = detection_client
    from app.features.health_check import routes

    async def logs_for(company_id, asset_id):
        return ["Application", "System"]

    monkeypatch.setattr(routes.rmm_event_logs, "logs_for", logs_for)
    signals = hc.evaluate_snapshot(WINDOWS_SNAPSHOT)
    model = detections.parse_model_monitors([{"title": "Vendor", "log": "System", "source": "Vendor", "event_ids": [9]}],
                                            "windows")
    check_id = await repo.create(company_id=1, asset_id=10, tray_device_id=5, ticket_id=None, platform="windows",
                                 model="m", allow_pawnio=False, requested_by_user_id=None)
    await repo.update(check_id, status="completed", completed=True, snapshot=WINDOWS_SNAPSHOT, report={
        "signals": signals, "monitors": [*detections.monitors_for_signals(signals), *model],
        "remediations": [{"detection": "volume_almost_full", "label": "A drive is almost full", "script_name": "Clean",
                          "scope": "company", "run_id": 7, "summary": "Queued Clean on PC-01"}],
    })
    assert test_client.get(f"/health-checks/{check_id}").status_code == 200
    extra = rendered[-1][1]
    urls = [monitor["url"] for monitor in extra["monitors"]]
    assert urls[0].startswith("/rmm/event-logs/rules?new=1&suggest=") and urls[0].endswith("&asset_id=10")
    assert urls[-1] == f"/rmm/event-logs/rules?new=1&health_check={check_id}&monitor={len(urls) - 1}&asset_id=10"
    assert extra["remediated"] == {"volume_almost_full"} and extra["can_manage"] is True

    html = _render("health_checks/report.html", check=hc.present(await repo.get(check_id)),
                   report=(await repo.get(check_id))["report"], signals=signals, snapshot={"sections": {}},
                   logs=[], areas=hc.AREAS, previous=[], can_run_again=True, can_manage=True,
                   monitors=extra["monitors"], remediations=extra["remediations"], remediated=extra["remediated"],
                   detections=detections.DETECTIONS)
    assert "Watch for these problems" in html and "Create this rule" in html and "AI suggestion" in html
    assert "Automatic fixes" in html and "Queued Clean on PC-01" in html and "Fix script queued" in html
    assert 'href="/health-checks/detections?edit=component_store_repairable#hc-remediation-modal"' in html


def test_detections_template_renders_the_list_and_editor():
    from app.features.health_check.detection_routes import catalog_view

    links = [{"id": 1, "detection_key": "component_store_repairable", "company_id": None, "script_id": 1,
              "script_name": "<b>Repair</b>", "script_active": True, "is_enabled": True, "run_count": 2,
              "last_run_at": "2026-10-20 01:00:00", "last_summary": "Queued"},
             {"id": 2, "detection_key": "blue_screens", "company_id": 1, "script_id": None, "create_ticket": True,
              "is_enabled": True}]
    reporting = {"blue_screens": [{"asset_id": 10, "asset_name": "PC-<i>01</i>", "check_id": 4, "severity": "critical",
                                   "completed_at": "2026-10-24 01:00:00", "titles": ["3 blue screen crashes"]}]}
    groups = catalog_view(links, 1, ["Application", "System"], reporting)
    crashes = next(i for g in groups for i in g["items"] if i["key"] == "blue_screens")
    assert crashes["raises_ticket"] and not crashes["runs_script"] and crashes["assets"] == reporting["blue_screens"]
    editor = {"key": "component_store_repairable", "detection": detections.detection("component_store_repairable"),
              "scope": "company", "link": None, "script_id": 2, "script": SCRIPTS[1], "timeout_seconds": 600,
              "fields": [{"key": "param:Reason", "name": "Reason", "value": "{{health.title}}", "sensitive": False,
                          "mandatory": False, "default": None, "help": "", "value_set": True}],
              "is_enabled": True, "create_ticket": True, "scripts": list(SCRIPTS.values())[:2],
              "errors": {"script": "Choose a script."}}
    html = _render("health_checks/detections.html", company={"name": "Contoso"}, groups=groups,
                   counts={"detections": 55, "with_script": 1, "with_remedy": 10, "with_monitors": 40},
                   can_edit=True, editor=editor, variables=[{"group": "Health check", "label": "Detection",
                                                             "token": "{{health.detection}}"}])
    assert "&lt;b&gt;Repair&lt;/b&gt;" in html and "Uses the every-company script" in html
    assert 'id="detection-component_store_repairable"' in html and "Create this rule" in html
    assert 'id="hc-remediation-modal"' in html and 'name="entry:param:Reason"' in html
    assert "Choose a script." in html and "DISM /Online /Cleanup-Image /RestoreHealth" in html
    assert "Collect Microsoft-Windows-Diagnostics-Performance/Operational first" in html
    assert 'name="create_ticket" value="1" checked' in html
    assert 'data-hc-assets-open="hc-assets-blue_screens"' in html and "Assets (1)" in html and "Assets (0)" in html
    assert 'id="hc-assets-blue_screens"' in html and 'href="/assets/10">PC-&lt;i&gt;01&lt;/i&gt;</a>' in html
    assert 'href="/health-checks/4"' in html and "3 blue screen crashes" in html and "Raises a ticket" in html
    listed = _render("health_checks/detections.html", company=None, groups=groups, counts={"detections": 1,
                     "with_script": 0, "with_remedy": 0, "with_monitors": 0}, can_edit=False, editor=None, variables=[])
    assert 'id="hc-remediation-modal"' not in listed and "Link a script" not in listed


# --------------------------------------------------------------------------- #
# Monthly check groups
# --------------------------------------------------------------------------- #

WINDOWS_11 = {"operator": "and", "children": [
    {"field": "asset.os_name", "operator": "contains", "value": "Windows 11"},
    {"operator": "or", "children": [
        {"field": "company.vip", "operator": "equals", "value": True},
        {"field": "asset.tags", "operator": "contains", "value": "workstation"},
    ]},
]}


def test_group_rules_are_described_in_words():
    assert schedule.describe_rules(None) == "Every computer"
    assert schedule.describe_rules(WINDOWS_11) == (
        "Asset operating system contains Windows 11 and "
        "(Company VIP status is Yes or Asset tag (including automatic tags) contains workstation)")
    assert schedule.describe_rules({"operator": "and", "children": [
        {"field": "asset.ram_gb", "operator": "between", "value": [8, 16]}]}).endswith("is between 8 and 16")


@pytest.fixture
def matching(monkeypatch):
    """Device rules match the assets listed here (inventory is not in the test database)."""

    assets: set[int] = {10}
    seen = []

    async def filter_agents(agents, rules):
        seen.append(rules)
        return [agent for agent in agents if int(agent["asset_id"]) in assets]

    monkeypatch.setattr(schedule.rmm_device_targeting, "filter_agents", filter_agents)
    return {"assets": assets, "seen": seen}


@pytest.mark.anyio
async def test_groups_are_scoped_and_validated(store, matching):
    default = (await groups_repo.list_for_company(1))[0]
    assert default["is_default"] and default["company_id"] is None and default["target_rules"] is None
    save = schedule.save_group
    with pytest.raises(schedule.GroupError, match="Only super admins"):
        await save(group_id=None, company_id=1, form={"name": "All", "scope": "global"}, user_id=9, is_super_admin=False)
    with pytest.raises(schedule.GroupError, match="name"):
        await save(group_id=None, company_id=1, form={"name": " "}, user_id=9, is_super_admin=True)
    with pytest.raises(schedule.GroupError) as error:
        await save(group_id=None, company_id=1, form={"name": "Bad", "target_mode": "rules", "target_rules": "{}"},
                   user_id=9, is_super_admin=True)
    assert "target_rules" in error.value.errors
    group_id = await save(group_id=None, company_id=1, user_id=9, is_super_admin=False, form={
        "name": "Windows 11 VIP workstations", "scope": "company", "target_mode": "rules",
        "target_rules": json.dumps(WINDOWS_11), "is_enabled": "1"})
    group = await groups_repo.get(group_id)
    assert group["company_id"] == 1 and group["is_enabled"] and group["target_rules"]["operator"] == "and"
    assert len(group["target_rules"]["children"]) == 2
    assert [g["id"] for g in await groups_repo.list_for_company(1)] == [default["id"], group_id]
    assert [g["id"] for g in await groups_repo.list_for_company(2)] == [default["id"]]
    assert schedule.can_edit_group(group, company_id=1, is_super_admin=False)
    assert not schedule.can_edit_group(default, company_id=1, is_super_admin=False)
    assert not schedule.can_edit_group(group, company_id=2, is_super_admin=True)


@pytest.mark.anyio
async def test_only_computers_in_a_group_get_monthly_checks(store, scheduled, matching):
    conn = scheduled["conn"]
    await conn.executescript("""
        INSERT INTO rmm_agents (id, asset_id, company_id, status) VALUES (1, 10, 1, 'active'), (2, 11, 1, 'active');
    """)
    agents = [{"id": 1, "asset_id": 10, "company_id": 1}, {"id": 2, "asset_id": 11, "company_id": 1},
              {"id": 3, "asset_id": 20, "company_id": 2}]
    # The default group checks every computer.
    assert await schedule.eligible_agent_ids(agents) == {1, 2, 3}
    # Narrowed: only computers matching an enabled group, in its company.
    default = (await groups_repo.list_for_company(1))[0]
    await groups_repo.update(default["id"], name=default["name"], company_id=None, target_rules=None,
                             is_enabled=False, user_id=9)
    assert await schedule.eligible_agent_ids(agents) == set()
    group_id = await schedule.save_group(group_id=None, company_id=1, user_id=9, is_super_admin=True, form={
        "name": "Windows 11", "scope": "company", "target_mode": "rules", "target_rules": json.dumps(WINDOWS_11),
        "is_enabled": "1"})
    matching["assets"].add(20)  # matches the rules, but is in another company
    assert await schedule.eligible_agent_ids(agents) == {1}
    assert matching["seen"][-1]["children"][0]["value"] == "Windows 11"
    matching["assets"].discard(20)

    await schedule.tick(NOW)
    assert await schedule_repo.get(10) is not None and await schedule_repo.get(11) is None

    # A second group adds more computers: groups combine.
    everyone = await schedule.save_group(group_id=None, company_id=1, user_id=9, is_super_admin=True, form={
        "name": "PC-02", "scope": "company", "target_mode": "all", "is_enabled": "1"})
    seeded_at = schedule._state["last_seeded"]
    await schedule.resync()
    assert schedule._state["last_seeded"] is None and seeded_at is not None
    await schedule.tick(NOW + timedelta(minutes=1))
    assert await schedule_repo.get(11) is not None

    # Narrowing again drops the computers no group matches any more.
    await groups_repo.delete(everyone)
    assert await schedule.resync() == 1 and await schedule_repo.get(11) is None
    # A due computer that stopped matching is dropped rather than checked.
    matching["assets"].clear()
    await _set_due(conn, 10, NOW)
    scheduled["online"].add("dev-1")
    assert await schedule.tick(NOW + timedelta(minutes=2)) == 0
    assert await schedule_repo.get(10) is None and await repo.list_checks(asset_id=10) == []
    assert group_id


@pytest.fixture
def monthly_client(client, monkeypatch):
    from app.features.health_check import schedule_routes

    test_client, state, rendered, audits = client
    test_client.app.include_router(schedule_routes.router)

    async def get_company(company_id):
        return {"id": company_id, "name": "Contoso"}

    monkeypatch.setattr(schedule_routes.company_repo, "get_company_by_id", get_company)
    return test_client, state, rendered, audits


@pytest.mark.anyio
async def test_monthly_page_manages_groups(monthly_client, store, matching):
    test_client, state, rendered, audits = monthly_client
    assert test_client.get("/health-checks/monthly").status_code == 200
    template, extra = rendered[-1]
    assert template == "health_checks/monthly.html" and extra["editing"] is None and extra["enabled_groups"] == 1
    default = extra["groups"][0]
    assert default["rules_text"] == "Every computer" and default["can_edit"]
    test_client.get("/health-checks/monthly?new=1")
    assert rendered[-1][1]["editing"]["id"] is None

    response = test_client.post("/health-checks/monthly/groups", follow_redirects=False, data={
        "name": "Windows 11", "scope": "company", "target_mode": "rules", "target_rules": json.dumps(WINDOWS_11),
        "is_enabled": "1"})
    assert response.status_code == 303 and audits[-1]["action"] == "health_check.group.save"
    group_id = audits[-1]["entity_id"]
    assert response.headers["location"] == f"/health-checks/monthly#group-{group_id}"
    bad = test_client.post(f"/health-checks/monthly/groups/{group_id}", data={
        "name": "Windows 11", "scope": "company", "target_mode": "rules", "target_rules": "not json"})
    assert bad.status_code == 400 and "target_rules" in rendered[-1][1]["errors"]
    test_client.get(f"/health-checks/monthly?group={group_id}")
    assert rendered[-1][1]["editing"]["target_mode"] == "rules"

    # Company users manage their own company's groups only.
    state["user"] = {"id": 12, "is_super_admin": False}
    test_client.get("/health-checks/monthly")
    assert [g["can_edit"] for g in rendered[-1][1]["groups"]] == [False, True]
    assert test_client.get(f"/health-checks/monthly?group={default['id']}").status_code == 403
    assert test_client.post(f"/health-checks/monthly/groups/{default['id']}/delete").status_code == 403
    response = test_client.post(f"/health-checks/monthly/groups/{group_id}/delete", follow_redirects=False)
    assert response.status_code == 303 and await groups_repo.get(group_id) is None
    state["company_id"] = 2
    other = await groups_repo.create(name="Theirs", company_id=1, target_rules=None, is_enabled=True, user_id=1)
    assert test_client.post(f"/health-checks/monthly/groups/{other}/delete").status_code == 404
    state["rmm_write"] = False
    state["company_id"] = 1
    assert test_client.post("/health-checks/monthly/groups", data={"name": "x"}).status_code == 403


@pytest.mark.anyio
async def test_monthly_groups_api(monthly_client, store, matching):
    test_client, state, _rendered, _audits = monthly_client
    created = test_client.post("/api/health-checks/monthly/groups",
                               json={"name": "VIP", "company_id": 1, "target_rules": WINDOWS_11})
    assert created.status_code == 201, created.text
    group = created.json()["group"]
    assert group["company_id"] == 1 and group["rules_text"].startswith("Asset operating system contains")
    updated = test_client.put(f"/api/health-checks/monthly/groups/{group['id']}",
                              json={"name": "Everyone", "company_id": 1, "is_enabled": False})
    assert updated.status_code == 200 and updated.json()["group"]["target_rules"] is None
    names = [g["name"] for g in test_client.get("/api/health-checks/monthly/groups?company_id=1").json()["groups"]]
    assert names == ["All computers", "Everyone"]
    assert test_client.post("/api/health-checks/monthly/groups", json={"name": "Bad", "target_rules": {}}).status_code == 400
    state["user"] = {"id": 12, "is_super_admin": False}
    assert test_client.post("/api/health-checks/monthly/groups", json={"name": "Global"}).status_code == 403
    default_id = (await groups_repo.list_for_company(None))[0]["id"]
    assert test_client.delete(f"/api/health-checks/monthly/groups/{default_id}").status_code == 403
    assert test_client.delete(f"/api/health-checks/monthly/groups/{group['id']}").status_code == 200
    assert test_client.delete(f"/api/health-checks/monthly/groups/{group['id']}").status_code == 404


def test_monthly_template_renders_groups_upcoming_and_editor():
    groups = [{"id": 1, "name": "<b>All</b>", "scope": "global", "scope_label": "Every company", "is_enabled": True,
               "has_rules": False, "is_default": True, "rules_text": "Every computer", "can_edit": False},
              {"id": 2, "name": "Windows 11", "scope": "company", "scope_label": "Contoso", "is_enabled": False,
               "has_rules": True, "is_default": False, "rules_text": schedule.describe_rules(WINDOWS_11),
               "can_edit": True}]
    upcoming = [{"asset_id": 10, "asset_name": "PC-01", "next_run_at": "2026-11-03 02:00:00",
                 "last_health_check_id": 4, "last_result": "Started health check #4."}]
    editing = {"id": 2, "name": "Windows 11", "scope": "company", "target_mode": "rules", "target_rules": WINDOWS_11,
               "is_enabled": False}
    html = _render("health_checks/monthly.html", company={"name": "Contoso"}, groups=groups, upcoming=upcoming,
                   scheduled_count=1, enabled_groups=1, monthly_enabled=False, can_edit=True, editing=editing,
                   errors={"target_rules": "Add at least one valid device rule."})
    assert "&lt;b&gt;All&lt;/b&gt;" in html and "HEALTH_CHECK_MONTHLY_ENABLED=false" in html
    assert "Asset operating system contains Windows 11" in html and 'href="/health-checks/4"' in html
    assert 'id="hc-group-modal"' in html and "data-rmm-rule-builder" in html and "Add at least one valid" in html
    assert "Windows 11" in html and "/static/js/health_check_groups.js" in html
    empty = _render("health_checks/monthly.html", company=None, groups=[], upcoming=[], scheduled_count=0,
                    enabled_groups=0, monthly_enabled=True, can_edit=False, editing=None)
    assert "No groups, so no computers get monthly health checks." in empty and 'id="hc-group-modal"' not in empty
