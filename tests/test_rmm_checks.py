"""RMM checks: which checks reach a device, saving, judging agent reports,
server-side checks, actions and the stats kept for the asset page."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.core.stats_database import StatsDatabase
from app.repositories import rmm as rmm_repo
from app.repositories import rmm_checks as checks_repo
from app.repositories import rmm_stats as stats_repo
from app.services import rmm_checks as checks
from app.services import rmm_scripts
from app.services import rmm_stats
from tests.test_rmm_scripts import _enrol, _environment, _sync, anyio_backend, sqlite_db  # noqa: F401 - fixtures

ROOT = Path(__file__).resolve().parents[1]
CLEANUP = "param([string]$Reason)\nWrite-Output $Reason\n"
HEALTH = "param([string]$Site)\nWrite-Output OK\n"


@pytest.fixture
async def stats_db(monkeypatch):
    database = StatsDatabase(sqlite_path=":memory:")
    monkeypatch.setattr(stats_repo, "stats_db", database)
    monkeypatch.setattr(checks, "stats_db", database)
    monkeypatch.setattr(rmm_stats, "stats_db", database)
    yield database
    await database.disconnect()


@pytest.fixture
def tickets(monkeypatch):
    from app.repositories import tickets as tickets_repo
    from app.services import tickets as tickets_service

    store: dict[str, object] = {"tickets": {}, "replies": [], "links": {}}

    async def create_ticket(**kwargs):
        ticket_id = len(store["tickets"]) + 100
        store["tickets"][ticket_id] = {"id": ticket_id, "ticket_number": str(ticket_id), "status": "open", **kwargs}
        return store["tickets"][ticket_id]

    async def get_ticket(ticket_id):
        return store["tickets"].get(ticket_id)

    async def create_reply(**kwargs):
        store["replies"].append(kwargs)
        return kwargs

    async def replace_ticket_assets(ticket_id, asset_ids):
        store["links"][ticket_id] = list(asset_ids)
        return []

    monkeypatch.setattr(tickets_service, "create_ticket", create_ticket)
    monkeypatch.setattr(tickets_repo, "get_ticket", get_ticket)
    monkeypatch.setattr(tickets_repo, "create_reply", create_reply)
    monkeypatch.setattr(tickets_repo, "replace_ticket_assets", replace_ticket_assets)
    return store


async def _save(form, *, check_id=None, company_id=1, super_admin=False):
    return await checks.save_check(check_id=check_id, company_id=company_id, form={"is_enabled": "1", **form},
                                   user_id=9, is_super_admin=super_admin)


def _clock(monkeypatch, start: datetime):
    now = {"value": start}
    monkeypatch.setattr(checks, "_utcnow", lambda: now["value"])
    return now


def test_device_checks_replace_company_and_global_ones():
    rows = [
        {"id": 1, "check_type": "disk", "target": "C:", "company_id": None, "asset_id": None, "is_enabled": True},
        {"id": 2, "check_type": "disk", "target": "c:", "company_id": 1, "asset_id": None, "is_enabled": True},
        {"id": 3, "check_type": "disk", "target": "C:", "company_id": 1, "asset_id": 10, "is_enabled": False},
        {"id": 4, "check_type": "cpu", "target": "", "company_id": None, "asset_id": None, "is_enabled": True},
        {"id": 5, "check_type": "cpu", "target": "", "company_id": 1, "asset_id": None, "is_enabled": False},
        {"id": 6, "check_type": "ping", "target": "8.8.8.8", "company_id": 1, "asset_id": None, "is_enabled": True},
        {"id": 7, "check_type": "ping", "target": "8.8.8.8", "company_id": 1, "asset_id": None, "is_enabled": True},
        {"id": 8, "check_type": "ping", "target": "1.1.1.1", "company_id": 2, "asset_id": None, "is_enabled": True},
        {"id": 9, "check_type": "memory", "target": "", "company_id": 1, "asset_id": 11, "is_enabled": True},
    ]
    # Device 10 switched its disk check off; the company switched CPU off; both ping checks at one level count.
    assert [c["id"] for c in checks.effective_checks(rows, 1, 10)] == [6, 7]
    assert [c["id"] for c in checks.effective_checks(rows, 1, 11)] == [2, 6, 7, 9]
    assert [c["id"] for c in checks.effective_checks(rows, 2, 20)] == [1, 4, 8]
    assert [c["id"] for c in checks.effective_checks(rows, 1, 10, include_off=True)] == [3, 5, 6, 7]
    assert [c["id"] for c in checks.replaced_by(rows[0], rows)] == [2, 3]
    assert checks.normalise_drive("c:\\") == "C:" and checks.normalise_drive("d") == "D:"
    assert checks.normalise_drive("/var/") == "/var" and checks.normalise_drive("/") == "/"


def test_parse_settings_validates_each_type():
    target, settings, duration, errors = checks.parse_settings(
        "disk", {"disk_drive": "c:\\", "disk_metric": "free_gb", "disk_operator": "below", "disk_threshold": "20"})
    assert (target, settings, duration, errors) == ("C:", {"metric": "free_gb", "operator": "below", "threshold": 20.0}, 0, {})
    assert checks.parse_settings("disk", {"disk_metric": "free_percent", "disk_threshold": "120"})[3].keys() == {"disk_threshold"}
    assert checks.parse_settings("ping", {"ping_target": "-c 9 host"})[3].keys() == {"ping_target"}
    assert checks.parse_settings("ping", {"ping_target": "gw.example.com", "ping_latency": "150"})[1] == {
        "max_loss_percent": 100, "max_latency_ms": 150}
    assert checks.parse_settings("service", {"service_name": "Spooler", "service_expected": "stopped",
                                             "service_fix": "start"})[3].keys() == {"service_fix"}
    assert checks.parse_settings("service", {"service_name": "--now", "service_expected": "running"})[3].keys() == {
        "service_name"}
    assert checks.parse_settings("script", {"script_expect": "exit_code", "script_value": "ok"})[3].keys() == {
        "script_value"}
    assert checks.parse_settings("offline", {"duration_minutes": "1"})[2] == checks.MIN_OFFLINE_MINUTES
    assert checks.parse_settings("cpu", {"cpu_threshold": "80", "duration_minutes": "30"})[1:3] == ({"threshold": 80.0}, 30)


def test_reports_are_judged_for_each_type():
    report = {
        "cpu_percent": 91.5,
        "memory": {"total_bytes": 16 * checks.GB, "available_bytes": 1 * checks.GB},
        "disks": [{"name": "C:\\", "total_bytes": 100 * checks.GB, "free_bytes": 5 * checks.GB},
                  {"name": "D:", "total_bytes": 100 * checks.GB, "free_bytes": 60 * checks.GB}],
        "network": [{"name": "Ethernet", "rx_bps": 25_000_000, "tx_bps": 1_000_000}],
        "pings": [{"target": "gw", "sent": 4, "received": 4, "avg_ms": 12.5},
                  {"target": "down", "sent": 4, "received": 0, "error": "timed out"}],
        "services": [{"name": "Spooler", "status": "running", "action": "started it"},
                     {"name": "W32Time", "status": "running"}, {"name": "Gone", "status": "missing"}],
    }

    def judge(kind, target="", **settings):
        return checks.evaluate({"check_type": kind, "target": target, "settings": settings}, report)

    assert judge("cpu", threshold=90) == (True, "91.5% CPU")
    assert judge("cpu", threshold=95)[0] is False
    assert judge("memory", metric="free_percent", threshold=10) == (True, "1 GB free (6.2%)")
    assert judge("memory", metric="free_gb", threshold=0.5)[0] is False
    assert judge("disk", "C:", metric="free_percent", operator="below", threshold=10) == (True, "C: 5 GB free (5%)")
    assert judge("disk", "*", metric="free_gb", operator="below", threshold=10)[0] is True
    assert judge("disk", "*", metric="free_gb", operator="below", threshold=1) == (False, "Lowest: C: 5 GB free (5%)")
    assert judge("disk", "E:", metric="free_gb", operator="below", threshold=1) == (True, "E: was not found on the device")
    assert judge("network", "", direction="in", operator="above", threshold_mbps=150) == (
        True, "200 Mbps received, 8 Mbps sent")
    assert judge("network", "ethernet", direction="out", operator="above", threshold_mbps=150)[0] is False
    assert judge("ping", "gw", max_loss_percent=100, max_latency_ms=10) == (True, "4/4 replies, 12.5 ms")
    assert judge("ping", "gw", max_loss_percent=100)[0] is False
    assert judge("ping", "down", max_loss_percent=100) == (True, "0/4 replies (timed out)")
    assert judge("ping", "missing", max_loss_percent=100) is None
    assert judge("service", "spooler", expected="running") == (True, "Running; the agent started it")
    assert judge("service", "W32Time", expected="running") == (False, "Running")
    assert judge("service", "W32Time", expected="stopped")[0] is True
    assert judge("service", "Gone", expected="running") == (True, "Not installed")
    assert checks.evaluate({"check_type": "cpu", "target": "", "settings": {"threshold": 5}}, {}) is None


def test_agent_config_lists_what_to_measure():
    config = checks.agent_config_for([
        {"check_type": "disk", "target": "C:", "settings": {}},
        {"check_type": "ping", "target": "gw", "settings": {}},
        {"check_type": "ping", "target": "gw", "settings": {}},
        {"check_type": "service", "target": "Spooler", "settings": {"expected": "running", "fix": "none"}},
        {"check_type": "service", "target": "spooler", "settings": {"expected": "running", "fix": "restart"}},
        {"check_type": "offline", "target": "", "settings": {}},
    ])
    assert config["enabled"] and config["disks"] and not config["cpu"] and not config["network"]
    assert config["pings"] == ["gw"]
    assert config["services"] == [{"name": "spooler", "expected": "running", "fix": "restart"}]
    assert not checks.agent_config_for([{"check_type": "offline", "target": "", "settings": {}}])["enabled"]


@pytest.mark.anyio
async def test_save_check_checks_scope_type_and_scripts(sqlite_db, monkeypatch):
    await _sync(monkeypatch, {"rmm/Common/Cleanup.ps1": CLEANUP, "rmm/Companies/Contoso/Health.ps1": HEALTH})
    scripts = {script["name"]: script for script in await rmm_repo.list_scripts()}

    with pytest.raises(checks.CheckError) as exc:
        await _save({"name": "", "scope": "global", "check_type": "nope", "action": "bogus"})
    assert set(exc.value.errors) == {"name", "scope", "check_type", "action"}
    with pytest.raises(checks.CheckError) as exc:
        await _save({"name": "x", "scope": "asset", "asset_id": "20", "check_type": "cpu", "cpu_threshold": "80"})
    assert "asset_id" in exc.value.errors
    with pytest.raises(checks.CheckError) as exc:
        await _save({"name": "x", "scope": "global", "check_type": "script",
                     "check_script_id": str(scripts["Health"]["id"])}, super_admin=True)
    assert "Common" in exc.value.errors["check_script_id"]

    disk_id = await _save({"name": "Low disk", "scope": "company", "check_type": "disk", "disk_drive": "c",
                           "disk_metric": "free_percent", "disk_operator": "below", "disk_threshold": "10",
                           "duration_minutes": "5", "action": "create_ticket"})
    disk = await checks_repo.get_check(disk_id)
    assert (disk["target"], disk["settings"]["threshold"], disk["duration_minutes"], disk["action"]) == ("C:", 10.0, 5, "create_ticket")
    assert checks.describe(disk) == "Free space on C: is below 10% for 5 minutes"

    form = {"name": "Site health", "scope": "asset", "asset_id": "10", "check_type": "script",
            "check_script_id": str(scripts["Health"]["id"]), "script_expect": "output_contains", "script_value": "OK",
            "action": "run_script", "script_id": str(scripts["Cleanup"]["id"]),
            "check_entry:param:Site": "{{asset.name}}", "entry:param:Reason": "{{check.result}}"}
    script_id = await _save(form)
    await _save(form, check_id=script_id)  # values for a newly chosen script are read on the next save
    stored = await checks_repo.get_check(script_id, with_secrets=True)
    assert stored["target"] == str(scripts["Health"]["id"])
    assert checks.script_values(stored, scripts["Health"], own=True)[0]["value"] == "{{asset.name}}"
    assert checks.script_values(stored, scripts["Cleanup"])[0]["value"] == "{{check.result}}"

    every = await _save({"name": "Offline", "scope": "global", "check_type": "offline", "duration_minutes": "30"},
                        super_admin=True)
    assert not checks.can_edit(await checks_repo.get_check(every), company_id=1, is_super_admin=False)
    assert [c["name"] for c in await checks_repo.list_checks_for_company(1)] == ["Offline", "Low disk", "Site health"]


@pytest.mark.anyio
async def test_cpu_check_waits_for_its_duration_then_raises_and_updates_a_ticket(sqlite_db, stats_db, tickets, monkeypatch):
    _result, agent = await _enrol(sqlite_db)
    check_id = await _save({"name": "Busy CPU", "scope": "company", "check_type": "cpu", "cpu_threshold": "80",
                            "duration_minutes": "30", "action": "create_ticket"})
    start = datetime(2026, 10, 12, 9, 0)
    now = _clock(monkeypatch, start)

    assert (await checks.agent_config(agent))["cpu"] is True
    assert (await checks.ingest_report(agent, {"cpu_percent": 95}))["checked"] == 1
    state = await checks_repo.get_state(check_id, agent["id"])
    assert state["status"] == "warning" and state["problem_since"] == start and not tickets["tickets"]

    now["value"] = start + timedelta(minutes=31)
    assert (await checks.ingest_report(agent, {"cpu_percent": 97}))["failing"] == 1
    ticket = tickets["tickets"][100]
    assert ticket["subject"] == "Busy CPU: PC" and "97% CPU" in ticket["description"] and tickets["links"][100] == [10]
    assert "Raised ticket #100" in (await checks_repo.get_check(check_id))["last_summary"]

    # Still failing: nothing more until it recovers.
    now["value"] = start + timedelta(minutes=40)
    await checks.ingest_report(agent, {"cpu_percent": 99})
    assert len(tickets["tickets"]) == 1 and not tickets["replies"]

    now["value"] = start + timedelta(minutes=45)
    await checks.ingest_report(agent, {"cpu_percent": 20})
    assert (await checks_repo.get_state(check_id, agent["id"]))["status"] == "passing"
    assert tickets["replies"][-1]["ticket_id"] == 100 and "passing again" in tickets["replies"][-1]["body"]

    # Failing again while the ticket is open updates it instead of raising another.
    now["value"] = start + timedelta(minutes=50)
    await checks.ingest_report(agent, {"cpu_percent": 90})
    now["value"] = start + timedelta(minutes=81)
    await checks.ingest_report(agent, {"cpu_percent": 90})
    assert len(tickets["tickets"]) == 1 and "is failing" in tickets["replies"][-1]["body"]

    # Stats: one sample per period.
    samples = await stats_repo.list_samples(asset_id=10, since=start - timedelta(days=1))
    assert [s["metric"] for s in samples] == ["cpu_percent"] * 6


@pytest.mark.anyio
async def test_failing_check_runs_its_script_again_after_the_cooldown(sqlite_db, stats_db, monkeypatch):
    await _sync(monkeypatch, {"rmm/Common/Cleanup.ps1": CLEANUP})
    script = (await rmm_repo.list_scripts())[0]
    _result, agent = await _enrol(sqlite_db)
    form = {"name": "Disk", "scope": "asset", "asset_id": "10", "check_type": "disk", "disk_drive": "C:",
            "disk_metric": "free_gb", "disk_operator": "below", "disk_threshold": "10", "action": "run_script",
            "script_id": str(script["id"]), "cooldown_minutes": "60", "entry:param:Reason": "{{check.result}}"}
    check_id = await _save(form)
    await _save(form, check_id=check_id)
    start = datetime(2026, 10, 12, 9, 0)
    now = _clock(monkeypatch, start)
    low = {"disks": [{"name": "C:", "total_bytes": 100 * checks.GB, "free_bytes": 2 * checks.GB}]}

    await checks.ingest_report(agent, low)
    runs = await rmm_repo.list_runs(company_id=1)
    assert len(runs) == 1 and runs[0]["run_source"] == checks.RUN_SOURCE
    jobs = await rmm_scripts.wait_for_jobs(agent, wait_seconds=0)
    assert jobs[0]["parameters"]["Reason"] == "C: 2 GB free (2%)"

    now["value"] = start + timedelta(minutes=30)
    await checks.ingest_report(agent, low)
    assert len(await rmm_repo.list_runs(company_id=1)) == 1
    now["value"] = start + timedelta(minutes=61)
    await checks.ingest_report(agent, low)
    assert len(await rmm_repo.list_runs(company_id=1)) == 2

    samples = await stats_repo.list_samples(asset_id=10, since=start - timedelta(days=1))
    assert {s["metric"] for s in samples} == {"disk_free_percent", "disk_free_bytes"}


@pytest.mark.anyio
async def test_agent_offline_is_judged_from_the_last_check_in(sqlite_db, tickets):
    _result, agent = await _enrol(sqlite_db)
    check_id = await _save({"name": "Server offline", "scope": "asset", "asset_id": "10", "check_type": "offline",
                            "duration_minutes": "30", "action": "create_ticket"})
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    assert await checks.evaluate_offline(now) == 1
    assert (await checks_repo.get_state(check_id, agent["id"]))["status"] == "passing"

    later = now + timedelta(minutes=45)
    await checks.evaluate_offline(later)
    state = await checks_repo.get_state(check_id, agent["id"])
    assert state["status"] == "failing" and state["value_text"] == "Last checked in 45 minutes ago"
    assert tickets["tickets"][100]["subject"] == "Server offline: PC"

    await rmm_repo.record_agent_checkin(agent["id"], {})
    await checks.evaluate_offline()
    assert (await checks_repo.get_state(check_id, agent["id"]))["status"] == "passing"
    assert "passing again" in tickets["replies"][-1]["body"]


@pytest.mark.anyio
async def test_script_checks_run_on_their_interval_and_judge_the_output(sqlite_db, monkeypatch):
    await _sync(monkeypatch, {"rmm/Common/Health.ps1": HEALTH})
    script = (await rmm_repo.list_scripts())[0]
    _result, agent = await _enrol(sqlite_db)
    check_id = await _save({"name": "Health", "scope": "company", "check_type": "script",
                            "check_script_id": str(script["id"]), "script_expect": "output_contains",
                            "script_value": "healthy", "script_interval": "15"})
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    assert await checks.run_due_script_checks(now) == 1
    assert await checks.run_due_script_checks(now + timedelta(minutes=1)) == 0  # not due yet
    state = await checks_repo.get_state(check_id, agent["id"])
    run_id = state["run_id"]
    assert run_id and (await rmm_repo.get_run(run_id))["run_source"] == checks.RUN_SOURCE

    jobs = await rmm_scripts.wait_for_jobs(agent, wait_seconds=0)
    assert jobs[0]["id"] == run_id
    await rmm_scripts.record_result(agent, run_id, {"exit_code": 0, "stdout": "Service is NOT ok\n"})
    assert await checks.run_finished(run_id) == 1
    state = await checks_repo.get_state(check_id, agent["id"])
    assert state["status"] == "failing" and state["run_id"] is None
    assert state["value_text"] == "Exit code 0: Service is NOT ok"

    # Due again: the next run passes.
    assert await checks.run_due_script_checks(now + timedelta(minutes=16)) == 1
    run_id = (await checks_repo.get_state(check_id, agent["id"]))["run_id"]
    await rmm_scripts.wait_for_jobs(agent, wait_seconds=0)
    await rmm_scripts.record_result(agent, run_id, {"exit_code": 0, "stdout": "All Healthy"})
    await checks.run_finished(run_id)
    assert (await checks_repo.get_state(check_id, agent["id"]))["status"] == "passing"

    assert checks.script_outcome({"settings": {"expect": "exit_code", "value": "0"}},
                                 {"status": "timed_out", "exit_code": None}) == (True, "The script timed out")
    assert checks.script_outcome({"settings": {"expect": "exit_code", "value": "3"}},
                                 {"status": "failed", "exit_code": 3, "stdout": ""}) == (False, "Exit code 3")


@pytest.mark.anyio
async def test_asset_context_and_charts(sqlite_db, stats_db, monkeypatch):
    from app.features.rmm import check_routes

    _result, agent = await _enrol(sqlite_db)
    company = await _save({"name": "CPU", "scope": "company", "check_type": "cpu", "cpu_threshold": "80"})
    await _save({"name": "CPU here", "scope": "asset", "asset_id": "10", "check_type": "cpu", "cpu_threshold": "95"})
    await _save({"name": "Gateway", "scope": "company", "check_type": "ping", "ping_target": "gw"})
    await _save({"name": "Memory", "scope": "company", "check_type": "memory", "memory_threshold": "10"})
    await _save({"name": "Net", "scope": "company", "check_type": "network", "network_threshold": "100"})
    base = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=2)
    now = _clock(monkeypatch, base)
    for minute in range(0, 60, 5):
        now["value"] = base + timedelta(minutes=minute)
        await checks.ingest_report(agent, {
            "cpu_percent": 40 + minute, "memory": {"total_bytes": 8 * checks.GB, "available_bytes": 4 * checks.GB},
            "network": [{"name": "Ethernet", "rx_bps": 125_000, "tx_bps": 0}],
            "pings": [{"target": "gw", "sent": 4, "received": 4, "avg_ms": 3}],
        })

    context = await check_routes.asset_checks_context(1, 10, can_edit=True, is_super_admin=False)
    by_name = {row["name"]: row for row in context["checks"]}
    assert by_name["CPU"]["replaced"] and not by_name["CPU"]["applies"] and by_name["CPU"]["state"] is None
    assert by_name["CPU here"]["state"]["status"] == "passing"
    assert context["checks"][-1]["name"] == "CPU"  # replaced checks last
    charts = {chart["title"]: chart for chart in context["stats"]["charts"]}
    assert set(charts) == {"CPU", "Memory used", "Network", "Ping"}
    assert charts["CPU"]["series"][0]["latest"] == "95 %" and charts["CPU"]["top"] == "100 %"
    assert charts["Network"]["series"][0]["latest"] == "1 Mbps"
    assert charts["Memory used"]["series"][0]["peak"] == "50 %"
    assert len(charts["CPU"]["series"][0]["segments"]) == 1

    # A gap longer than three sample periods breaks the line.
    since = base
    lines = rmm_stats.chart("x", "%", [("a", [(since, 1), (since + timedelta(minutes=5), 2),
                                             (since + timedelta(hours=1), 3)])],
                            since=since, until=since + timedelta(hours=2), gap=timedelta(minutes=15))
    assert len(lines["series"][0]["segments"]) == 2 and len(lines["series"][0]["dots"]) == 1
    assert check_routes.form_values(await checks_repo.get_check(company))["cpu_threshold"] == "80"


def test_checks_page_and_asset_sections_render():
    env = _environment()
    env.globals["static_url"] = lambda path: path
    from app.features.rmm import check_routes

    row = {"id": 3, "name": "<b>Disk</b>", "check_type": "disk", "type_label": "Disk space", "scope_label": "Company: Contoso",
           "description": "Free space on C: is below 10%", "action": "run_script", "action_label": "Run Cleanup",
           "cooldown_minutes": 0, "counts": {"failing": 2, "passing": 1}, "last_summary": "Failed on PC",
           "is_enabled": True, "can_edit": True, "replaced": True, "asset_id": None}
    editing = {**check_routes._blank_check(10, "service"), "id": None, "name": ""}
    html = env.get_template("rmm/checks.html").render(
        company={"name": "Contoso"}, checks=[row], can_edit=True, is_super_admin=False, editing=editing,
        errors={"service_name": "Enter the service's name."}, check_types=checks.CHECK_TYPES, actions=checks.ACTIONS,
        disk_metrics=checks.DISK_METRICS, memory_metrics=checks.MEMORY_METRICS, comparisons=checks.COMPARISONS,
        network_directions=checks.NETWORK_DIRECTIONS, service_states=checks.SERVICE_STATES,
        service_fixes=checks.SERVICE_FIXES, script_expectations=checks.SCRIPT_EXPECTATIONS, statuses=checks.STATUSES,
        devices=[{"asset_id": 10, "asset_name": "PC-01"}], scripts=[], script=None, script_fields=[],
        check_script=None, check_script_fields=[], variables=[], csrf_token="tok",
    )
    assert "&lt;b&gt;Disk&lt;/b&gt;" in html and "<b>Disk</b>" not in html
    assert "[Add check][Scripts]" in html and "Enter the service&#39;s name." in html
    assert "2 failing" in html and "once per failure" in html and "Some devices have their own" in html
    assert '<option value="service" selected>' in html and 'value="global"' not in html
    assert 'action="/rmm/checks/3/enabled"' in html and "/static/js/rmm_checks.js" in html

    detail = (ROOT / "app/templates/assets/detail.html").read_text()
    env.parse(detail)  # compiles
    assert 'id="checks"' in detail and 'id="machine-stats"' in detail
    assert "rmm_ui.check_menu(check, csrf_token, asset.id)" in detail
