"""Installed printers per RMM device: the built-in scan, run capture, first/last seen and the asset page."""
from __future__ import annotations

import base64
import gzip
import json
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode

import pytest
from starlette.requests import Request

from app.core.database import Database
from app.repositories import rmm as rmm_repo
from app.repositories import rmm_printers as repo
from app.services import rmm_printers as printers
from app.services import rmm_scripts
from app.services.rmm_scripts import RunRequestError
from tests.test_rmm_scripts import _enrol, _environment, anyio_backend, sqlite_db  # noqa: F401 - fixtures

ROOT = Path(__file__).resolve().parents[1]
UPSTAIRS = r"\\print01\Upstairs"


@pytest.fixture
async def printers_db(sqlite_db, monkeypatch):
    for name in ("574_rmm_printers.sql", "575_rmm_printer_job_count.sql", "577_rmm_printer_actions.sql"):
        sql = Database()._adapt_sql_for_sqlite((ROOT / "migrations" / name).read_text())
        await sqlite_db.conn.executescript(sql)
    monkeypatch.setattr(repo, "db", sqlite_db)
    yield sqlite_db


def _encode(payload) -> str:
    return base64.b64encode(gzip.compress(json.dumps(payload).encode())).decode()


def _scan(*names, **overrides):
    rows = {
        "Office HP": {"name": "Office HP", "driver": "HP Universal Printing PCL 6", "port": "IP_10.0.0.20",
                      "port_address": "10.0.0.20", "type": "Local", "device_type": "Print", "status": "Normal",
                      "job_count": 3, "shared": True, "share_name": "OfficeHP", "published": False, "location": "Level 2",
                      "comment": None, "computer_name": "PC-01", "users": [], "default_for": ["CONTOSO\\amy"]},
        "PDF": {"name": "Microsoft Print to PDF", "driver": "Microsoft Print To PDF", "port": "PORTPROMPT:",
                "type": "Local", "status": "7", "shared": False, "users": [], "default_for": []},
        "Upstairs": {"name": UPSTAIRS, "type": "Connection", "computer_name": "print01",
                     "users": ["CONTOSO\\amy", "CONTOSO\\bob", "CONTOSO\\amy"], "default_for": []},
    }
    payload = {"scanned_at": "2026-11-20T01:00:00Z", "printers": [rows[name] for name in names], "errors": []}
    payload.update(overrides)
    return payload


async def _finish_scan(agent, payload, *, user_id=None):
    run_id = await printers.queue_scan(agent, user_id=user_id)
    assert run_id
    [job] = await rmm_scripts.claim_jobs(agent)
    assert job["id"] == run_id and "Get-Printer" in job["script"]
    await rmm_scripts.record_result(agent, run_id, {
        "exit_code": 0, "stdout": "Scanning\n" + printers.MARKER + _encode(payload) + "\nPrinters found: 2\n",
    })
    return run_id


def test_migration_and_script_exist():
    sql = (ROOT / "migrations" / "574_rmm_printers.sql").read_text()
    assert "-- phase: expand" in sql
    assert "CREATE TABLE IF NOT EXISTS rmm_printer_scans" in sql and "CREATE TABLE IF NOT EXISTS rmm_printers" in sql
    assert "first_seen_at" in sql and "last_seen_at" in sql and "is_installed" in sql
    assert "job_count INT" in (ROOT / "migrations" / "575_rmm_printer_job_count.sql").read_text()
    script = printers.scan_script_content()
    assert "JobCount" in script and "PrinterStatus" in script
    assert "Get-Printer" in script and "Get-PrinterPort" in script and printers.MARKER in script


def test_normalise_cleans_printers_and_names_statuses():
    state = printers.normalise(_scan("Office HP", "PDF", "Upstairs", errors=["ports: denied", ""]))
    names = [row["name"] for row in state["printers"]]
    assert names == [UPSTAIRS, "Microsoft Print to PDF", "Office HP"]
    hp = state["printers"][2]
    assert hp["status"] == "Ready" and hp["is_shared"] == 1 and hp["port_address"] == "10.0.0.20"
    assert hp["printer_status"] == "Normal" and hp["job_count"] == 3
    assert state["printers"][1]["printer_status"] == "7" and state["printers"][1]["job_count"] is None
    assert hp["default_for"] == "CONTOSO\\amy" and hp["comment"] is None
    assert state["printers"][1]["status"] == "Offline"
    assert state["printers"][0]["connected_users"] == "CONTOSO\\amy, CONTOSO\\bob"
    assert state["errors"] == ["ports: denied"]
    # A one-item array collapsed by PowerShell 5 still reads.
    assert len(printers.normalise({"printers": _scan("PDF")["printers"][0]})["printers"]) == 1
    # The list itself failing keeps what MyPortal already has.
    assert printers.normalise({"printers": [], "errors": ["printers: spooler stopped"]})["printers"] is None
    with pytest.raises(ValueError):
        printers.normalise(["not", "an", "object"])


@pytest.mark.anyio
async def test_scan_captures_printers_and_hides_the_marker(printers_db):
    _, agent = await _enrol(printers_db)
    run_id = await _finish_scan(agent, _scan("Office HP", "PDF"), user_id=9)
    run = await rmm_repo.get_run(run_id)
    assert run["run_source"] == "printer_scan" and run["status"] == "completed"
    assert printers.MARKER not in run["stdout"] and "MyPortal saved 2 printers." in run["stdout"]
    rows = await repo.list_printers(int(agent["id"]))
    assert {row["name"] for row in rows} == {"Office HP", "Microsoft Print to PDF"}
    assert all(row["is_installed"] == 1 and row["first_seen_at"] and row["last_seen_at"] for row in rows)
    assert {row["name"]: row["job_count"] for row in rows} == {"Office HP": 3, "Microsoft Print to PDF": None}
    scan = await repo.get_scan(int(agent["id"]))
    assert scan["printer_count"] == 2 and scan["scan_requested_by_user_id"] == 9


@pytest.mark.anyio
async def test_missing_printers_are_kept_until_unseen_for_a_year(printers_db):
    _, agent = await _enrol(printers_db)
    agent_id = int(agent["id"])
    await _finish_scan(agent, _scan("Office HP", "PDF", "Upstairs"))
    long_ago = datetime.utcnow() - timedelta(days=400)
    months_ago = datetime.utcnow() - timedelta(days=200)
    await printers_db.execute("UPDATE rmm_printers SET first_seen_at = %s, last_seen_at = %s WHERE agent_id = %s",
                              (long_ago, long_ago, agent_id))
    await printers_db.execute("UPDATE rmm_printers SET last_seen_at = %s WHERE name = %s",
                              (months_ago, UPSTAIRS))

    # Only the HP is still installed: it stays and is seen again, however old it is.
    # The PDF printer was last seen over a year ago and goes; Upstairs, gone for
    # 200 days, is kept as no longer seen.
    await _finish_scan(agent, _scan("Office HP"))
    rows = {row["name"]: row for row in await repo.list_printers(agent_id)}
    assert set(rows) == {"Office HP", UPSTAIRS}
    hp = rows["Office HP"]
    assert hp["is_installed"] == 1 and str(hp["first_seen_at"])[:10] == long_ago.date().isoformat()
    assert str(hp["last_seen_at"])[:10] == datetime.utcnow().date().isoformat()
    upstairs = rows[UPSTAIRS]
    assert upstairs["is_installed"] == 0 and str(upstairs["last_seen_at"])[:10] == months_ago.date().isoformat()

    # Seen again: installed, with its original first seen date.
    await _finish_scan(agent, _scan("Office HP", "Upstairs"))
    upstairs = {row["name"]: row for row in await repo.list_printers(agent_id)}[UPSTAIRS]
    assert upstairs["is_installed"] == 1 and str(upstairs["first_seen_at"])[:10] == long_ago.date().isoformat()


@pytest.mark.anyio
async def test_recently_removed_printer_is_reported_as_no_longer_seen(printers_db):
    _, agent = await _enrol(printers_db)
    await _finish_scan(agent, _scan("Office HP", "PDF"))
    await _finish_scan(agent, _scan("Office HP"))
    context = await printers.asset_context(agent, can_run=True)
    by_name = {row["name"]: row for row in context["printers"]}
    assert by_name["Office HP"]["presence_label"] == "Installed"
    assert by_name["Microsoft Print to PDF"]["presence_label"] == "No longer seen"
    assert context["counts"]["total"] == 1 and context["counts"]["removed"] == 1
    assert context["printers"][0]["name"] == "Office HP"  # installed first


@pytest.mark.anyio
async def test_failed_list_keeps_stored_printers(printers_db):
    _, agent = await _enrol(printers_db)
    await _finish_scan(agent, _scan("Office HP"))
    await _finish_scan(agent, {"printers": [], "errors": ["printers: The spooler service is not running."]})
    rows = await repo.list_printers(int(agent["id"]))
    assert [row["is_installed"] for row in rows] == [1]
    assert (await repo.get_scan(int(agent["id"])))["errors"] == ["printers: The spooler service is not running."]


@pytest.mark.anyio
async def test_other_runs_cannot_report_printers(printers_db):
    _, agent = await _enrol(printers_db)
    stdout = printers.MARKER + _encode(_scan("PDF")) + "\n"
    assert await printers.capture(agent, {"id": 1, "run_source": "manual"}, stdout) == stdout
    assert await repo.list_printers(int(agent["id"])) == []


@pytest.mark.anyio
async def test_queue_scan_needs_an_active_windows_agent(printers_db):
    _, agent = await _enrol(printers_db)
    with pytest.raises(RunRequestError):
        await printers.queue_scan({**agent, "os": "linux"})
    with pytest.raises(RunRequestError):
        await printers.queue_scan(None)
    assert await printers.queue_scan(agent)
    assert await printers.queue_scan(agent) is None  # already waiting
    assert await printers.asset_context({**agent, "os": "linux"}, can_run=True) is None


def _form_request(path: str) -> Request:
    body = urlencode([]).encode()

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    return Request({"type": "http", "method": "POST", "path": path, "headers": [
        (b"content-type", b"application/x-www-form-urlencoded")]}, receive)


@pytest.mark.anyio
async def test_routes_queue_scan_with_audit_and_api(printers_db, monkeypatch):
    from fastapi import HTTPException
    from app.features.rmm import printer_routes as routes

    _, agent = await _enrol(printers_db)
    audits = []

    async def context(request, **kwargs):
        return {"id": 9}, {}, 1, True

    async def audit(**kwargs):
        audits.append(kwargs)

    monkeypatch.setattr(routes, "_context", context)
    monkeypatch.setattr(routes.audit_service, "record", audit)
    response = await routes.scan_printers(10, _form_request("/scan"))
    assert response.headers["location"] == "/assets/10#printers" and audits[-1]["action"] == "rmm.printers.scan"
    await routes.scan_printers(10, _form_request("/scan"))
    assert len(audits) == 1  # already waiting
    [job] = await rmm_scripts.claim_jobs(agent)
    await rmm_scripts.record_result(agent, job["id"], {
        "exit_code": 0, "stdout": printers.MARKER + _encode(_scan("Office HP")) + "\n"})
    api = await routes.printers_api(10, _form_request("/api"))
    assert api["supported"] and [row["name"] for row in api["printers"]] == ["Office HP"]
    with pytest.raises(HTTPException) as exc:
        await routes.scan_printers(20, _form_request("/scan"))
    assert exc.value.status_code == 404


def test_printers_section_renders_sortable_filterable_table():
    env = _environment()
    env.globals["static_url"] = lambda path: path
    rows = [printers._display({
        "name": "<b>Office</b>", "driver_name": "HP", "port_name": "IP_10.0.0.20", "port_address": "10.0.0.20",
        "printer_type": "Local", "status": "Paper jam", "printer_status": "PaperJam", "job_count": 4, "is_shared": 1, "share_name": "OfficeHP", "location": "L2",
        "connected_users": None, "default_for": "CONTOSO\\amy", "is_installed": 1,
        "first_seen_at": datetime(2025, 1, 2), "last_seen_at": datetime(2026, 11, 20),
    }), printers._display({
        "name": "Old", "printer_type": "Connection", "status": None, "is_shared": 0, "connected_users": "CONTOSO\\bob",
        "is_installed": 0, "first_seen_at": datetime(2025, 1, 2), "last_seen_at": datetime(2026, 1, 1),
    })]
    context = {"scan": {"scanned_at": "2026-11-20T01:00:00", "errors": []}, "printers": rows,
               "counts": {"total": 1, "shared": 1, "attention": 1, "removed": 1, "jobs": 4},
               "type_options": ["Local", "Network connection"], "status_options": ["Paper jam", "Unknown"],
               "scan_pending": False, "can_run": True}
    template = env.from_string('{% include "rmm/_asset_printers.html" %}')
    html = template.render(rmm={"printers": context}, asset={"id": 10}, csrf_token="t")
    assert 'id="printers"' in html and 'data-table data-table-id="asset-printers"' in html
    assert 'data-table-filter="rmm-printers-table"' in html and 'data-sort="date"' in html
    assert 'data-filter-options="multiple"' in html and "status--danger" in html
    assert "&lt;b&gt;Office&lt;/b&gt;" in html and "<b>Office</b>" not in html
    assert "No longer seen" in html and "Default for CONTOSO\\amy" in html and "10.0.0.20" in html
    assert 'data-column-key="jobs"' in html and 'data-value="4"' in html and 'title="PrinterStatus: PaperJam"' in html
    assert 'data-utc="2025-01-02T00:00:00"' in html and "/rmm/assets/10/printers/scan" in html


# --------------------------------------------------------------------------- #
# Renaming and removing printers
# --------------------------------------------------------------------------- #


async def _printer(agent, name):
    return next(row for row in await repo.list_printers(int(agent["id"])) if row["name"] == name)


async def _finish_action(agent, stdout, *, exit_code=0):
    [job] = await rmm_scripts.claim_jobs(agent)
    assert "MYPORTAL_PRINTER_ACTION" in job["script"]
    await rmm_scripts.record_result(agent, job["id"], {"exit_code": exit_code, "stdout": stdout})
    return job


def _action_line(status):
    return printers.ACTION_MARKER + json.dumps({"status": status, "message": "done"}) + "\n"


def test_action_migration_and_script():
    sql = (ROOT / "migrations" / "577_rmm_printer_actions.sql").read_text()
    assert "-- phase: expand" in sql and "pending_run_id" in sql and "pending_action" in sql
    script = printers.action_script_content()
    assert "Rename-Printer" in script and "Remove-Printer" in script and printers.ACTION_MARKER in script
    assert "$env:MYPORTAL_PRINTER_ACTION" in script


def test_clean_new_name():
    assert printers.clean_new_name("  Reception   laser ", "Office HP", ["Office HP"]) == "Reception laser"
    for bad in ("", "a\\b", "a,b", "Office HP", "office pdf", "x" * 221):
        with pytest.raises(RunRequestError) as exc:
            printers.clean_new_name(bad, "Office HP", ["Office HP", "Office PDF"])
        assert "new_name" in exc.value.errors


@pytest.mark.anyio
async def test_rename_runs_on_the_device_and_updates_the_list(printers_db):
    _, agent = await _enrol(printers_db)
    await _finish_scan(agent, _scan("Office HP", "PDF"))
    hp = await _printer(agent, "Office HP")
    result = await printers.queue_action(agent, hp["id"], "rename", new_name="Reception laser", user_id=9)
    assert result["run_id"] and result["new_name"] == "Reception laser"
    run = await rmm_repo.get_run(result["run_id"])
    assert run["run_source"] == "printer_action" and run["requested_by_user_id"] == 9

    shown = {row["name"]: row for row in (await printers.asset_context(agent, can_run=True))["printers"]}
    assert shown["Office HP"]["pending_label"] == "Rename pending"
    assert shown["Office HP"]["pending_name"] == "Reception laser"
    assert not shown["Office HP"]["can_rename"] and not shown["Office HP"]["can_remove"]
    with pytest.raises(RunRequestError):  # one change at a time
        await printers.queue_action(agent, hp["id"], "remove")

    job = await _finish_action(agent, "Renaming\n" + _action_line("renamed"))
    request = json.loads(job["env"]["MYPORTAL_PRINTER_ACTION"])
    assert request == {"action": "rename", "name": "Office HP", "new_name": "Reception laser"}
    run = await rmm_repo.get_run(result["run_id"])
    assert printers.ACTION_MARKER not in run["stdout"]
    rows = {row["name"]: row for row in await repo.list_printers(int(agent["id"]))}
    assert set(rows) == {"Reception laser", "Microsoft Print to PDF"}
    renamed = rows["Reception laser"]
    assert renamed["id"] == hp["id"] and renamed["pending_action"] is None and renamed["pending_run_id"] is None


@pytest.mark.anyio
async def test_failed_action_leaves_the_printer_unchanged(printers_db):
    _, agent = await _enrol(printers_db)
    await _finish_scan(agent, _scan("Office HP"))
    hp = await _printer(agent, "Office HP")
    await printers.queue_action(agent, hp["id"], "remove")
    await _finish_action(agent, "The printer is busy\n", exit_code=1)
    row = await _printer(agent, "Office HP")
    assert row["pending_action"] is None and row["is_installed"] == 1
    assert (await printers.asset_context(agent, can_run=True))["printers"][0]["can_remove"]


@pytest.mark.anyio
async def test_remove_installed_printer_and_connection(printers_db):
    _, agent = await _enrol(printers_db)
    await _finish_scan(agent, _scan("Office HP", "Upstairs"))
    upstairs = await _printer(agent, UPSTAIRS)
    shown = {row["name"]: row for row in (await printers.asset_context(agent, can_run=True))["printers"]}
    assert shown[UPSTAIRS]["is_connection"] and not shown[UPSTAIRS]["can_rename"]
    with pytest.raises(RunRequestError):
        await printers.queue_action(agent, upstairs["id"], "rename", new_name="Upstairs")
    result = await printers.queue_action(agent, upstairs["id"], "remove")
    assert result["run_id"]
    await _finish_action(agent, _action_line("removed"))
    assert [row["name"] for row in await repo.list_printers(int(agent["id"]))] == ["Office HP"]
    assert (await repo.get_scan(int(agent["id"])))["printer_count"] == 1


@pytest.mark.anyio
async def test_removing_a_printer_no_longer_seen_only_forgets_it(printers_db):
    _, agent = await _enrol(printers_db)
    await _finish_scan(agent, _scan("Office HP", "PDF"))
    await _finish_scan(agent, _scan("Office HP"))
    pdf = await _printer(agent, "Microsoft Print to PDF")
    with pytest.raises(RunRequestError):
        await printers.queue_action(agent, pdf["id"], "rename", new_name="PDF")
    result = await printers.queue_action(agent, pdf["id"], "remove")
    assert result["run_id"] is None
    assert [row["name"] for row in await repo.list_printers(int(agent["id"]))] == ["Office HP"]
    assert await rmm_scripts.claim_jobs(agent) == []


@pytest.mark.anyio
async def test_actions_only_reach_the_agents_own_printers(printers_db):
    _, agent = await _enrol(printers_db)
    await _finish_scan(agent, _scan("Office HP"))
    with pytest.raises(RunRequestError):
        await printers.queue_action(agent, 99999, "remove")
    hp = await _printer(agent, "Office HP")
    with pytest.raises(RunRequestError):
        await printers.queue_action({**agent, "id": int(agent["id"]) + 1}, hp["id"], "remove")
    with pytest.raises(RunRequestError):
        await printers.queue_action(agent, hp["id"], "delete")


def _rename_request(path: str, new_name: str) -> Request:
    body = urlencode([("new_name", new_name)]).encode()

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    return Request({"type": "http", "method": "POST", "path": path, "headers": [
        (b"content-type", b"application/x-www-form-urlencoded")]}, receive)


@pytest.mark.anyio
async def test_rename_and_remove_routes_audit(printers_db, monkeypatch):
    from app.features.rmm import printer_routes as routes

    _, agent = await _enrol(printers_db)
    await _finish_scan(agent, _scan("Office HP", "PDF"))
    audits = []

    async def context(request, **kwargs):
        return {"id": 9}, {}, 1, True

    async def audit(**kwargs):
        audits.append(kwargs)

    monkeypatch.setattr(routes, "_context", context)
    monkeypatch.setattr(routes.audit_service, "record", audit)
    hp = await _printer(agent, "Office HP")
    response = await routes.rename_printer(10, hp["id"], _rename_request("/rename", "a,b"))
    assert response.headers["location"] == "/assets/10#printers" and audits == []
    response = await routes.rename_printer(10, hp["id"], _rename_request("/rename", "Reception laser"))
    assert response.headers["location"] == "/assets/10#printers"
    assert audits[-1]["action"] == "rmm.printers.rename"
    assert audits[-1]["metadata"]["new_name"] == "Reception laser"
    pdf = await _printer(agent, "Microsoft Print to PDF")
    await routes.remove_printer(10, pdf["id"], _form_request("/remove"))
    assert audits[-1]["action"] == "rmm.printers.remove" and audits[-1]["metadata"]["run_id"]


def test_printers_section_renders_row_actions():
    env = _environment()
    env.globals["static_url"] = lambda path: path
    base = {"printer_type": "Local", "status": "Normal", "is_shared": 0, "connected_users": None,
            "first_seen_at": datetime(2025, 1, 2), "last_seen_at": datetime(2026, 11, 20)}
    rows = [
        printers._display({**base, "id": 1, "name": "Office HP", "is_installed": 1}),
        printers._display({**base, "id": 2, "name": UPSTAIRS, "printer_type": "Connection",
                           "connected_users": "CONTOSO\\bob", "is_installed": 1}),
        printers._display({**base, "id": 3, "name": "Old", "is_installed": 0}),
        printers._display({**base, "id": 4, "name": "Busy", "is_installed": 1, "pending_action": "rename",
                           "pending_name": "Front desk", "pending_run_status": "queued"}),
    ]
    context = {"scan": {"scanned_at": "2026-11-20T01:00:00", "errors": []}, "printers": rows,
               "counts": {"total": 3, "shared": 0, "attention": 0, "removed": 1, "jobs": 0},
               "type_options": [], "status_options": [], "scan_pending": False, "can_run": True}
    template = env.from_string('{% include "rmm/_asset_printers.html" %}')
    html = template.render(rmm={"printers": context}, asset={"id": 10}, csrf_token="t")
    assert 'data-printer-rename data-asset-id="10" data-printer-id="1"' in html
    assert 'data-printer-rename data-asset-id="10" data-printer-id="2"' not in html
    assert 'data-printer-remove data-asset-id="10" data-printer-id="2"' in html and 'data-printer-kind="connection"' in html
    assert 'data-printer-kind="forget"' in html and "Remove from list…" in html
    assert "Rename pending</span>" in html and "to Front desk" in html and 'data-printer-id="4"' not in html
    assert 'id="rmm-printer-rename-modal"' in html and 'name="new_name"' in html and 'name="_csrf"' in html
    context["can_run"] = False
    html = template.render(rmm={"printers": context}, asset={"id": 10}, csrf_token="t")
    assert "data-printer-rename" not in html and "rmm-printer-rename-modal" not in html
