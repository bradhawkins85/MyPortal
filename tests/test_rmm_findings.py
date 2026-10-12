"""RMM findings: signal parsing, rule findings, AI output checks, the analysis lifecycle and the page."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.core.event_log_database import EventLogDatabase
from app.core.stats_database import StatsDatabase
from app.repositories import rmm_findings as repo
from app.services import rmm_findings as findings
from tests.test_rmm_scripts import _environment, anyio_backend, sqlite_db  # noqa: F401 - fixtures

ROOT = Path(__file__).resolve().parents[1]

LOCKOUT = (
    "A user account was locked out.\r\n\r\nSubject:\r\n\tSecurity ID:\t\tS-1-5-18\r\n\tAccount Name:\t\tDC01$\r\n"
    "\tAccount Domain:\t\tCONTOSO\r\n\r\nAccount That Was Locked Out:\r\n\tSecurity ID:\t\tS-1-5-21-1\r\n"
    "\tAccount Name:\t\tjsmith\r\n\r\nAdditional Information:\r\n\tCaller Computer Name:\tPC-02\r\n"
)
ACCOUNT_CREATED = (
    "A user account was created.\r\n\r\nSubject:\r\n\tSecurity ID:\t\tS-1-5-21-500\r\n\tAccount Name:\t\tadmin\r\n\r\n"
    "New Account:\r\n\tSecurity ID:\t\tS-1-5-21-1001\r\n\tAccount Name:\t\t{name}\r\n\tAccount Domain:\t\tPC\r\n\r\n"
    "Attributes:\r\n\tSAM Account Name:\t{name}\r\n\tDisplay Name:\t\t<value not set>\r\n"
)
GROUP_ADD = (
    "A member was added to a security-enabled local group.\r\n\r\nSubject:\r\n\tAccount Name:\t\tadmin\r\n\r\n"
    "Member:\r\n\tSecurity ID:\t\tCONTOSO\\evil\r\n\tAccount Name:\t\t-\r\n\r\nGroup:\r\n"
    "\tSecurity ID:\t\tBUILTIN\\Administrators\r\n\tGroup Name:\t\tAdministrators\r\n"
)
SERVICE = (
    "A service was installed in the system.\r\n\r\nService Name:  Updater\r\n"
    "Service File Name:  C:\\Users\\Public\\upd.exe\r\nService Type:  user mode service\r\n"
    "Service Start Type:  auto start\r\nService Account:  LocalSystem\r\n"
)
AUDIT = "Microsoft-Windows-Security-Auditing"


def _now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _event(event_id, message, *, agent_id=1, company_id=1, provider=AUDIT):
    return {"event_id": event_id, "provider": provider, "message": message, "agent_id": agent_id,
            "company_id": company_id, "log_name": "Security", "time_created": _now() - timedelta(hours=1)}


def _devices():
    return findings.Devices([
        {"agent_id": 1, "company_id": 1, "asset_id": 10, "hostname": "DC01", "asset_name": "DC01", "company_name": "Contoso"},
        {"agent_id": 2, "company_id": 1, "asset_id": 11, "hostname": "PC-02", "asset_name": "PC-02", "company_name": "Contoso"},
        {"agent_id": 3, "company_id": 2, "asset_id": 20, "hostname": "OTHER-PC", "asset_name": "OTHER-PC",
         "company_name": "Other"},
    ])


# --------------------------------------------------------------------------- #
# Reading security events
# --------------------------------------------------------------------------- #


def test_security_events_are_read_from_windows_messages():
    lockout = findings.parse_security_event(_event(4740, LOCKOUT))
    assert (lockout["kind"], lockout["account"], lockout["caller"]) == ("lockout", "jsmith", "PC-02")

    created = findings.parse_security_event(_event(4720, ACCOUNT_CREATED.format(name="support1")))
    assert (created["account"], created["creator"]) == ("support1", "admin")

    added = findings.parse_security_event(_event(4732, GROUP_ADD))
    assert (added["member"], added["group"]) == ("evil", "Administrators")

    service = findings.parse_security_event(_event(7045, SERVICE, provider="Service Control Manager"))
    assert service["service"] == "Updater" and service["path"] == "C:\\Users\\Public\\upd.exe"

    # The same event ID from another source is not a security event.
    assert findings.parse_security_event(_event(4740, LOCKOUT, provider="Some App")) is None


def test_similar_account_names_cluster_across_companies():
    events = [findings.parse_security_event(_event(4720, ACCOUNT_CREATED.format(name=name), agent_id=agent, company_id=company))
              for name, agent, company in (("jsmith", 1, 1), ("j.smith", 2, 1), ("jsmith2", 3, 2), ("backup", 2, 1))]
    clusters = findings.cluster_accounts(events)
    assert clusters[0]["names"] == {"jsmith", "j.smith", "jsmith2"}
    assert clusters[0]["companies"] == {1, 2} and clusters[0]["agents"] == {1, 2, 3}
    assert findings.account_base("CONTOSO\\J.Smith2") == "jsmith"

    found = findings.security_findings(events, _devices(), now=_now())
    accounts = [finding for finding in found if finding["key"].startswith("accounts:")]
    assert len(accounts) == 1 and accounts[0]["severity"] == "high"
    assert accounts[0]["asset_ids"] == [10, 11, 20] and accounts[0]["company_ids"] == [1, 2]


def test_lockouts_name_the_caller_and_link_managed_devices():
    events = [findings.parse_security_event(_event(4740, LOCKOUT)) for _ in range(4)]
    found = findings.security_findings(events, _devices(), now=_now())
    lockout = next(finding for finding in found if finding["key"].startswith("lockout:"))
    assert lockout["title"] == "jsmith locked out 4 times in 24 hours" and lockout["severity"] == "medium"
    assert "PC-02" in lockout["summary"]
    # The domain controller logging it and the managed caller are both affected.
    assert lockout["asset_ids"] == [10, 11]
    assert any("managed device" in line for line in lockout["evidence"])


def test_services_from_unusual_locations_and_cleared_logs_are_high():
    events = [findings.parse_security_event(_event(7045, SERVICE, provider="Service Control Manager")),
              findings.parse_security_event(_event(1102, "The audit log was cleared.", provider="Microsoft-Windows-Eventlog"))]
    found = {finding["key"]: finding for finding in findings.security_findings(events, _devices(), now=_now())}
    assert found["service:updater"]["severity"] == "high"
    assert found["log-cleared:1"]["severity"] == "high"


# --------------------------------------------------------------------------- #
# Software, correlation and performance
# --------------------------------------------------------------------------- #


def test_software_changes_against_the_previous_snapshot():
    now = _now()
    rows = [{"name": "Chrome", "version": "120", "install_date": (now - timedelta(days=1)).date()},
            {"name": "Zoom", "version": "5", "install_date": (now - timedelta(days=60)).date()}]
    # First time: only programs installed inside the look-back window count.
    current, changes = findings.diff_software(None, rows, now=now, since=now - timedelta(days=8))
    assert current == {"Chrome": "120", "Zoom": "5"}
    assert [(c["name"], c["change_type"]) for c in changes] == [("Chrome", "installed")]

    rows = [{"name": "Chrome", "version": "121"}, {"name": "AnyDesk", "version": "8"}]
    _current, changes = findings.diff_software(current, rows, now=now, since=now)
    assert sorted((c["name"], c["change_type"], c["old_version"], c["new_version"]) for c in changes) == [
        ("AnyDesk", "installed", None, "8"), ("Chrome", "updated", "120", "121"), ("Zoom", "removed", "5", None)]


def test_spike_after_update_on_the_same_devices_is_correlated():
    now = _now()
    devices = _devices()
    spike = {"log_name": "Application", "provider": "Application Error", "event_id": 1000, "level": 2,
             "sample": "Faulting application name: outlook.exe",
             "spiking_devices": [{"agent_id": a, "current": 50, "baseline_daily": 1, "first_seen": now - timedelta(hours=10)}
                                 for a in (1, 2, 3)]}
    change = {"name": "Microsoft 365 Apps", "change_type": "updated", "new_version": "16.0.2", "old_versions": {"16.0.1"},
              "agents": {1: now - timedelta(hours=12), 2: now - timedelta(hours=12)}, "companies": {1}}
    unrelated = {"name": "Chrome", "change_type": "updated", "new_version": "121", "old_versions": set(),
                 "agents": {3: now}, "companies": {2}}
    found = findings.correlate([spike], [change, unrelated])
    assert len(found) == 1 and found[0]["agents"] == [1, 2]
    finding = findings.correlation_findings(found, devices)[0]
    assert finding["category"] == "software" and "Microsoft 365 Apps 16.0.2" in finding["title"]
    assert finding["asset_ids"] == [10, 11]


def test_devices_slower_than_identical_devices_are_outliers():
    rows = [{"agent_id": i, "company_id": 1, "asset_id": 100 + i, "hostname": f"PC-{i}", "os": "windows",
             "cpu_model": "Intel(R) Core(TM) i5-8500 CPU @ 3.00GHz", "ram_bytes": 16 * 1024 ** 3} for i in range(1, 6)]
    devices = findings.Devices(rows)
    averages = [{"agent_id": i, "metric": "cpu_percent", "average": 10 + i, "samples": 100} for i in range(1, 5)]
    averages.append({"agent_id": 5, "metric": "cpu_percent", "average": 88, "samples": 100})
    outliers = findings.find_performance_outliers(averages, devices)
    assert len(outliers) == 1 and list(outliers[0]["devices"]) == [5]
    finding = findings.performance_findings(outliers, devices)[0]
    assert finding["severity"] == "medium" and finding["asset_ids"] == [105]


# --------------------------------------------------------------------------- #
# AI output is checked before it is stored
# --------------------------------------------------------------------------- #


def test_ai_findings_keep_only_known_devices_and_valid_fields():
    devices = _devices()
    signals = {"devices": [{"device_id": 1, "company_id": 1}, {"device_id": 2, "company_id": 1}],
               "companies": [{"company_id": 1, "name": "Contoso"}]}
    reply = "```json\n" + json.dumps({"findings": [
        {"key": "Outlook Crashes!", "title": "Outlook crashing after update", "category": "events", "severity": "high",
         "confidence": 140, "summary": "Crashes rose.", "evidence": ["50 crashes", {"bad": 1}],
         "recommendation": "Roll back.", "device_ids": [1, 2, 3, 999, True], "company_ids": [1, 2]},
        {"title": "x", "summary": "too short a title"},
        {"key": "odd", "title": "Odd thing", "category": "nonsense", "severity": "urgent", "summary": "Hmm."},
        "not an object",
    ]}) + "\n```"
    parsed = findings.parse_ai_findings(reply, devices, signals)
    assert [f["key"] for f in parsed] == ["outlook-crashes", "odd"]
    first = parsed[0]
    assert first["source"] == "ai" and first["confidence"] == 100
    assert first["asset_ids"] == [10, 11] and first["company_ids"] == [1]
    assert first["evidence"] == ["50 crashes"]
    assert first["fingerprint"] == findings._fingerprint("ai", "outlook-crashes")
    assert (parsed[1]["category"], parsed[1]["severity"]) == ("other", "info")

    with pytest.raises(ValueError):
        findings.parse_ai_findings("I could not find anything", devices, signals)


def test_llm_config_prefers_findings_settings(monkeypatch):
    settings = findings.get_settings()
    monkeypatch.setattr(settings, "findings_llm_base_url", "https://llm.example.com/")
    monkeypatch.setattr(settings, "findings_llm_model", "big-model")
    monkeypatch.setattr(settings, "findings_llm_api_key", "")
    assert findings.build_llm_config() == {"base_url": "https://llm.example.com", "model": "big-model", "api_key": ""}

    for name in ("findings_llm_base_url", "findings_llm_model", "findings_llm_api_key"):
        monkeypatch.setattr(settings, name, None)
    monkeypatch.setattr(findings.tray_service, "build_troubleshoot_llm_config",
                        lambda: {"base_url": "http://ts", "model": "ts-model", "api_key": "k"})
    assert findings.build_llm_config()["model"] == "ts-model"


# --------------------------------------------------------------------------- #
# A whole analysis against SQLite
# --------------------------------------------------------------------------- #


@pytest.fixture
async def platform(sqlite_db, monkeypatch):
    from app.core.database import Database

    sql = Database()._adapt_sql_for_sqlite((ROOT / "migrations" / "481_rmm_findings.sql").read_text())
    await sqlite_db.conn.executescript(sql)
    await sqlite_db.conn.executescript("""
        INSERT INTO companies (id, name) VALUES (3, 'Third');
        INSERT INTO assets (id, company_id, name) VALUES (12, 1, 'DC01'), (30, 3, 'THIRD-PC');
    """)
    now = _now()
    agents = [(1, 1, 10, "PC-01"), (2, 1, 11, "PC-02"), (3, 2, 20, "OTHER-PC"), (4, 1, 12, "DC01"), (5, 3, 30, "THIRD-PC")]
    for agent_id, company_id, asset_id, hostname in agents:
        await sqlite_db.execute(
            "INSERT INTO rmm_agents (id, agent_uid, company_id, asset_id, auth_token_hash, auth_token_prefix, hostname, "
            "os, last_seen_utc, status) VALUES (%s, %s, %s, %s, %s, %s, %s, 'windows', %s, 'active')",
            (agent_id, f"uid-{agent_id}", company_id, asset_id, f"hash-{agent_id}", "p", hostname, now),
        )
        await sqlite_db.execute(
            "INSERT INTO rmm_device_inventory (agent_id, company_id, asset_id, hostname, cpu_model, ram_bytes, "
            "system_disk_free_bytes, last_boot_at) VALUES (%s, %s, %s, %s, 'Intel i5-8500', %s, %s, %s)",
            (agent_id, company_id, asset_id, hostname, 16 * 1024 ** 3,
             1024 ** 3 if agent_id == 3 else 100 * 1024 ** 3, now - timedelta(days=2)),
        )
        await sqlite_db.execute(
            "INSERT INTO rmm_device_software (agent_id, name, version) VALUES (%s, 'Microsoft 365 Apps', '16.0.1')",
            (agent_id,),
        )
    monkeypatch.setattr(repo, "db", sqlite_db)

    events = EventLogDatabase(sqlite_path=":memory:")
    stats = StatsDatabase(sqlite_path=":memory:")
    for module in (repo, findings):
        monkeypatch.setattr(module, "event_log_db", events)
        monkeypatch.setattr(module, "stats_db", stats)

    async def acquire(_name, _ttl):
        return True

    async def release(_name):
        return None

    monkeypatch.setattr(findings.singleton_jobs, "acquire", acquire)
    monkeypatch.setattr(findings.singleton_jobs, "release", release)
    settings = findings.get_settings()
    monkeypatch.setattr(settings, "findings_ai_enabled", True)
    monkeypatch.setattr(settings, "findings_baseline_days", 7)
    yield sqlite_db, events, stats
    await events.disconnect()
    await stats.disconnect()


async def _add_events(events, now):
    from app.repositories import rmm_event_logs as event_log_repo

    entries, record = [], 0

    def add(agent_id, company_id, asset_id, event_id, provider, log, level, message, when):
        nonlocal record
        record += 1
        entries.append({"company_id": company_id, "asset_id": asset_id, "agent_id": agent_id, "log_name": log,
                        "record_id": record, "event_id": event_id, "level": level, "provider": provider,
                        "computer": "x", "message": message, "time_created": when, "received_at": now})

    # Outlook crashes: rare before, 30 an hour today on PC-01 and PC-02.
    for agent_id, asset_id in ((1, 10), (2, 11)):
        add(agent_id, 1, asset_id, 1000, "Application Error", "Application", 2,
            "Faulting application name: outlook.exe", now - timedelta(days=4))
        for minute in range(30):
            add(agent_id, 1, asset_id, 1000, "Application Error", "Application", 2,
                "Faulting application name: outlook.exe", now - timedelta(hours=2, minutes=minute))
    for _ in range(3):
        add(4, 1, 12, 4740, AUDIT, "Security", 4, LOCKOUT, now - timedelta(hours=1))
    add(3, 2, 20, 1102, "Microsoft-Windows-Eventlog", "Security", 4, "The audit log was cleared.", now - timedelta(hours=3))
    original = event_log_repo.event_log_db
    event_log_repo.event_log_db = events
    try:
        await event_log_repo.insert_entries(entries)
    finally:
        event_log_repo.event_log_db = original


@pytest.mark.anyio
async def test_analysis_stores_rule_and_ai_findings_and_keeps_reviews(platform, monkeypatch):
    fake, events, _stats = platform
    now = _now()
    calls = []

    async def chat(llm, messages):
        calls.append(messages)
        payload = json.loads(messages[1]["content"].split("BEGIN_UNTRUSTED_RECORDS\n")[1].split("\nEND_UNTRUSTED_RECORDS")[0])
        signals = payload["records"][0]["content"]
        assert signals["event_spikes"][0]["event_id"] == 1000
        assert {d["device_id"] for d in signals["devices"]} >= {1, 2}
        return json.dumps({"findings": [{
            "key": "outlook-crash-wave", "title": "Outlook crash wave at Contoso", "category": "events",
            "severity": "high", "confidence": 70, "summary": "Outlook crashes on two PCs.", "evidence": ["60 crashes"],
            "recommendation": "Repair Office.", "device_ids": [1, 2, 42], "company_ids": [1, 3]}]})

    monkeypatch.setattr(findings, "_chat", chat)

    # First analysis: snapshots software, finds nothing in event logs yet.
    first = await findings.start_analysis(trigger_source="manual", user_id=9, wait=True)
    assert (await repo.get_run(first))["status"] == "succeeded"

    # Office updates on two PCs and the crashes start.
    await fake.execute("UPDATE rmm_device_software SET version = '16.0.2' WHERE agent_id IN (1, 2)")
    await events.connect()
    await _add_events(events, now)
    second = await findings.start_analysis(trigger_source="schedule", wait=True)
    run = await repo.get_run(second)
    assert run["status"] == "succeeded" and run["ai_status"] == "used" and run["stats"]["event_logs"] is True

    stored = {f["finding_key"]: f for f in await repo.list_findings()}
    assert "spike:application:application error:1000" in stored
    assert any(key.startswith("correlation:application:application error:1000:microsoft 365 apps") for key in stored)
    assert "lockout:1:jsmith" in stored and "log-cleared:2" in stored
    assert stored["health:low-disk"]["asset_ids"] == [20]
    ai = stored["outlook-crash-wave"]
    assert ai["source"] == "ai" and ai["asset_ids"] == [10, 11] and ai["company_ids"] == [1]

    # A dismissed finding stays dismissed when found again; occurrences grow.
    await repo.set_status(ai["id"], status="dismissed", note="Known issue", user_id=9)
    await findings.start_analysis(trigger_source="schedule", wait=True)
    again = await repo.get_finding(ai["id"])
    assert again["status"] == "dismissed" and again["occurrences"] == 2 and again["is_active"]

    # A failed AI assessment keeps earlier AI findings active.
    async def broken(llm, messages):
        raise RuntimeError("model offline")

    monkeypatch.setattr(findings, "_chat", broken)
    failed = await findings.start_analysis(trigger_source="schedule", wait=True)
    run = await repo.get_run(failed)
    assert run["status"] == "succeeded" and run["ai_status"] == "failed" and "model offline" in run["ai_error"]
    assert (await repo.get_finding(ai["id"]))["is_active"]

    # Findings the next analysis no longer sees stop being active.
    await fake.execute("UPDATE rmm_device_inventory SET system_disk_free_bytes = %s", (100 * 1024 ** 3,))
    await findings.start_analysis(trigger_source="schedule", wait=True)
    assert not (await repo.get_finding(stored["health:low-disk"]["id"]))["is_active"]


@pytest.mark.anyio
async def test_without_event_logs_their_findings_are_kept(platform, monkeypatch):
    fake, events, _stats = platform
    monkeypatch.setattr(findings.get_settings(), "findings_ai_enabled", False)
    await events.connect()
    await _add_events(events, _now())
    await findings.start_analysis(trigger_source="manual", wait=True)
    lockout = next(f for f in await repo.list_findings() if f["finding_key"] == "lockout:1:jsmith")

    async def offline():
        return False

    monkeypatch.setattr(events, "ensure_connected", offline)
    run_id = await findings.start_analysis(trigger_source="manual", wait=True)
    run = await repo.get_run(run_id)
    assert run["stats"]["event_logs"] is False and run["ai_status"] == "off"
    assert any("event log database" in problem for problem in run["stats"]["problems"])
    assert (await repo.get_finding(lockout["id"]))["is_active"]


@pytest.mark.anyio
async def test_only_one_analysis_runs_and_the_schedule_waits(platform, monkeypatch):
    monkeypatch.setattr(findings.get_settings(), "findings_ai_enabled", False)
    run_id = await repo.create_run(trigger_source="manual", requested_by_user_id=None)
    with pytest.raises(findings.AnalysisRunning):
        await findings.start_analysis(trigger_source="manual")

    # A run left behind by a stopped server is failed after a while, and the
    # schedule starts the next one once the interval has passed.
    await platform[0].execute("UPDATE rmm_finding_runs SET started_at = %s WHERE id = %s",
                              (_now() - timedelta(hours=7), run_id))
    await repo.fail_stale_runs(_now() - timedelta(minutes=findings.STALE_RUN_MINUTES))
    assert (await repo.get_run(run_id))["status"] == "failed" and await repo.get_running_run() is None
    started = []

    async def start(**kwargs):
        started.append(kwargs)
        return 0

    monkeypatch.setattr(findings, "start_analysis", start)
    monkeypatch.setattr(findings.get_settings(), "findings_interval_hours", 6)
    await findings.tick()
    assert started == [{"trigger_source": "schedule"}]

    await repo.create_run(trigger_source="schedule", requested_by_user_id=None)
    started.clear()
    await findings.tick()
    assert started == []

    monkeypatch.setattr(findings.get_settings(), "findings_enabled", False)
    await platform[0].execute("UPDATE rmm_finding_runs SET started_at = %s", (_now() - timedelta(days=1),))
    await findings.tick()
    assert started == []


# --------------------------------------------------------------------------- #
# The page
# --------------------------------------------------------------------------- #


def _presented():
    finding = {
        "id": 7, "finding_key": "k", "source": "ai", "category": "security", "severity": "critical",
        "title": "<script>alert(1)</script> accounts", "summary": "Summary </script><b>x</b>", "recommendation": "Do it",
        "evidence": ["line"], "confidence": 80, "status": "open", "is_active": True, "occurrences": 2,
        "first_seen_at": "2026-10-10 01:00:00", "last_seen_at": "2026-10-12 01:00:00",
        "asset_ids": [10], "company_ids": [1],
    }
    return findings.present(finding, {"assets": {10: {"name": "PC-01", "company_id": 1}}, "companies": {1: "Contoso"}})


def test_findings_page_renders_cards_modal_and_escapes():
    env = _environment()
    env.globals["static_url"] = lambda path: path
    presented = _presented()
    assert presented["first_seen_at"] == "2026-10-10T01:00:00Z"
    assert presented["devices"] == [{"asset_id": 10, "name": "PC-01", "company": "Contoso"}]
    html = env.get_template("rmm/findings.html").render(
        findings=[presented], view="active", counts={"critical": 1, "high": 0, "medium": 0, "low": 0, "info": 0},
        open_total=1, csrf_token="tok",
        latest_run=findings.run_summary({"id": 3, "status": "succeeded", "trigger_source": "schedule",
                                         "started_at": "2026-10-12 01:00:00", "finished_at": "2026-10-12 01:00:42",
                                         "ai_status": "failed", "ai_error": "timeout", "finding_count": 1,
                                         "stats": {"devices": 5, "event_logs": False, "device_stats": True}}),
        settings_summary={"enabled": True, "ai_enabled": True, "interval_hours": 6, "baseline_days": 7, "model": "m"},
        categories=[("security", "Security")], severities=findings.SEVERITIES, statuses=findings.STATUSES,
    )
    assert "[Run analysis]" in html
    assert "<script>alert(1)" not in html and "&lt;script&gt;alert(1)" in html
    # Data for the modal cannot end its script block early.
    data = html.split('id="findings-data">')[1].split("</script>")[0]
    assert json.loads(data)[0]["title"].startswith("<script>")
    assert 'id="finding-modal" role="dialog" aria-modal="true" aria-labelledby="finding-modal-title" hidden' in html
    assert 'data-finding-open="7" aria-haspopup="dialog" aria-controls="finding-modal"' in html
    assert "status--error" in html and "AI insight · 80% confident" in html
    assert "Not connected" in html and "The AI assessment failed (timeout)" in html
    assert "<noscript>" in html and 'action="/rmm/findings/7/status"' in html


def test_findings_page_empty_states():
    env = _environment()
    env.globals["static_url"] = lambda path: path
    common = dict(findings=[], view="active", counts={k: 0 for k in findings.SEVERITIES}, open_total=0, csrf_token="",
                  settings_summary={"enabled": True, "ai_enabled": False, "interval_hours": 6, "baseline_days": 7,
                                    "model": None},
                  categories=[], severities=findings.SEVERITIES, statuses=findings.STATUSES)
    assert "No findings yet" in env.get_template("rmm/findings.html").render(latest_run=None, **common)
    clear = findings.run_summary({"id": 1, "status": "succeeded", "started_at": "2026-10-12 01:00:00",
                                  "finished_at": "2026-10-12 01:01:00", "ai_status": "off", "stats": {"devices": 3}})
    assert "Nothing stands out" in env.get_template("rmm/findings.html").render(latest_run=clear, **common)


# --------------------------------------------------------------------------- #
# Routes: super admins only, statuses checked
# --------------------------------------------------------------------------- #


@pytest.fixture
def client(platform, monkeypatch):
    from types import SimpleNamespace

    from fastapi import FastAPI, HTTPException
    from fastapi.testclient import TestClient

    from app.features.rmm import finding_routes

    state = {"user": {"id": 9, "is_super_admin": True}}

    async def require_super_admin_page(request):
        if not state["user"].get("is_super_admin"):
            raise HTTPException(status_code=403, detail="Super admin privileges required")
        return state["user"], None

    async def render_template(template, request, user, extra=None):
        from fastapi.responses import HTMLResponse

        return HTMLResponse(json.dumps({"template": template, "count": len(extra["findings"]),
                                        "open_total": extra["open_total"]}))

    main = SimpleNamespace(_require_super_admin_page=require_super_admin_page, _render_template=render_template)
    monkeypatch.setattr(finding_routes, "_assets_routes", lambda: SimpleNamespace(_main=lambda: main))
    audits = []

    async def log_action(**kwargs):
        audits.append(kwargs)

    monkeypatch.setattr(finding_routes.audit_service, "log_action", log_action)
    app = FastAPI()
    app.include_router(finding_routes.router)
    with TestClient(app) as test_client:
        yield test_client, state, audits


@pytest.mark.anyio
async def test_routes_are_for_super_admins_and_check_statuses(client, monkeypatch):
    test_client, state, audits = client
    finding = findings._finding("health:low-disk", category="health", severity="low", title="Low disk", summary="s",
                                recommendation="r", evidence=[], devices=_devices(), agent_ids=[1])
    run_id = await repo.create_run(trigger_source="manual", requested_by_user_id=None)
    finding_id = await repo.upsert_finding(finding, run_id=run_id)

    page = test_client.get("/rmm/findings")
    assert page.status_code == 200 and page.json() == {"template": "rmm/findings.html", "count": 1, "open_total": 1}
    listed = test_client.get("/api/rmm/findings").json()
    assert listed["findings"][0]["devices"] == [{"asset_id": 10, "name": "PC-01", "company": "Contoso"}]

    assert test_client.patch(f"/api/rmm/findings/{finding_id}", json={"status": "closed"}).status_code == 422
    response = test_client.post(f"/rmm/findings/{finding_id}/status", data={"status": "bogus"}, follow_redirects=False)
    assert response.status_code == 400
    assert test_client.patch("/api/rmm/findings/999", json={"status": "dismissed"}).status_code == 404

    response = test_client.patch(f"/api/rmm/findings/{finding_id}", json={"status": "acknowledged", "note": "On it"})
    assert response.status_code == 200
    assert response.json()["finding"]["status"] == "acknowledged" and response.json()["finding"]["status_note"] == "On it"
    assert audits[-1]["action"] == "rmm.findings.status" and audits[-1]["new_value"] == {"status": "acknowledged"}

    # The run created above is still "running", so another cannot start.
    assert test_client.post("/api/rmm/findings/runs").status_code == 409
    assert test_client.get("/api/rmm/findings/runs/latest").json()["run"]["status"] == "running"

    state["user"] = {"id": 5, "is_super_admin": False}
    for method, path in (("get", "/rmm/findings"), ("get", "/api/rmm/findings"), ("post", "/rmm/findings/run"),
                         ("post", "/api/rmm/findings/runs")):
        assert getattr(test_client, method)(path).status_code == 403
    assert test_client.patch(f"/api/rmm/findings/{finding_id}", json={"status": "dismissed"}).status_code == 403
    assert (await repo.get_finding(finding_id))["status"] == "acknowledged"


@pytest.mark.anyio
async def test_a_failing_tick_never_stops_rmm_automation(monkeypatch):
    async def broken():
        raise RuntimeError("database gone")

    monkeypatch.setattr(findings.get_settings(), "findings_enabled", True)
    monkeypatch.setattr(repo, "get_latest_run", broken)
    await findings.tick()
