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
    sql = Database()._adapt_sql_for_sqlite((ROOT / "migrations" / "574_rmm_printers.sql").read_text())
    await sqlite_db.conn.executescript(sql)
    monkeypatch.setattr(repo, "db", sqlite_db)
    yield sqlite_db


def _encode(payload) -> str:
    return base64.b64encode(gzip.compress(json.dumps(payload).encode())).decode()


def _scan(*names, **overrides):
    rows = {
        "Office HP": {"name": "Office HP", "driver": "HP Universal Printing PCL 6", "port": "IP_10.0.0.20",
                      "port_address": "10.0.0.20", "type": "Local", "device_type": "Print", "status": "Normal",
                      "shared": True, "share_name": "OfficeHP", "published": False, "location": "Level 2",
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
    script = printers.scan_script_content()
    assert "Get-Printer" in script and "Get-PrinterPort" in script and printers.MARKER in script


def test_normalise_cleans_printers_and_names_statuses():
    state = printers.normalise(_scan("Office HP", "PDF", "Upstairs", errors=["ports: denied", ""]))
    names = [row["name"] for row in state["printers"]]
    assert names == [UPSTAIRS, "Microsoft Print to PDF", "Office HP"]
    hp = state["printers"][2]
    assert hp["status"] == "Ready" and hp["is_shared"] == 1 and hp["port_address"] == "10.0.0.20"
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
        "printer_type": "Local", "status": "Paper jam", "is_shared": 1, "share_name": "OfficeHP", "location": "L2",
        "connected_users": None, "default_for": "CONTOSO\\amy", "is_installed": 1,
        "first_seen_at": datetime(2025, 1, 2), "last_seen_at": datetime(2026, 11, 20),
    }), printers._display({
        "name": "Old", "printer_type": "Connection", "status": None, "is_shared": 0, "connected_users": "CONTOSO\\bob",
        "is_installed": 0, "first_seen_at": datetime(2025, 1, 2), "last_seen_at": datetime(2026, 1, 1),
    })]
    context = {"scan": {"scanned_at": "2026-11-20T01:00:00", "errors": []}, "printers": rows,
               "counts": {"total": 1, "shared": 1, "attention": 1, "removed": 1},
               "type_options": ["Local", "Network connection"], "status_options": ["Paper jam", "Unknown"],
               "scan_pending": False, "can_run": True}
    template = env.from_string('{% include "rmm/_asset_printers.html" %}')
    html = template.render(rmm={"printers": context}, asset={"id": 10}, csrf_token="t")
    assert 'id="printers"' in html and 'data-table data-table-id="asset-printers"' in html
    assert 'data-table-filter="rmm-printers-table"' in html and 'data-sort="date"' in html
    assert 'data-filter-options="multiple"' in html and "status--danger" in html
    assert "&lt;b&gt;Office&lt;/b&gt;" in html and "<b>Office</b>" not in html
    assert "No longer seen" in html and "Default for CONTOSO\\amy" in html and "10.0.0.20" in html
    assert 'data-utc="2025-01-02T00:00:00"' in html and "/rmm/assets/10/printers/scan" in html
