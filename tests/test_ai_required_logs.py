"""Logs the AI troubleshooter and health check need but cannot read, and the approved list."""
from __future__ import annotations

import json
import re
from pathlib import Path

import aiosqlite
import pytest

from app.core.database import Database
from app.repositories import ai_required_logs as repo
from app.services import ai_required_logs as required
from app.services import ai_troubleshooter as ts
from app.services import tray as tray_service
from tests.test_rmm_scripts import _SqliteDb, _environment, anyio_backend  # noqa: F401 - fixtures

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _no_approvals(monkeypatch):
    monkeypatch.setattr(required, "_approvals", required._ApprovalCache())


@pytest.fixture
async def store(monkeypatch):
    conn = await aiosqlite.connect(":memory:")
    conn.row_factory = aiosqlite.Row
    await conn.executescript("CREATE TABLE companies (id INTEGER PRIMARY KEY); CREATE TABLE assets (id INTEGER PRIMARY KEY);")
    await conn.executescript(Database()._adapt_sql_for_sqlite(
        (ROOT / "migrations" / "519_ai_health_checks.sql").read_text(encoding="utf-8")))
    fake = _SqliteDb(conn)
    monkeypatch.setattr(repo, "db", fake)
    yield fake
    await conn.close()


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def test_channel_names():
    for name in ("Application", "HardwareEvents", "Microsoft-Windows-Ntfs/Operational",
                 "Microsoft-Windows-Windows Defender/Operational", "Microsoft Office Alerts"):
        assert required.valid_event_log_channel(name), name
    for name in ("", "/q:*", "a/b/c", " Application", "Bad  Name", "x" * 300, "C:\\Windows\\x.log", "a;b"):
        assert not required.valid_event_log_channel(name), name


def test_channel_pattern_matches_tray_agent():
    go = (ROOT / "tray/internal/agent/logsources.go").read_text(encoding="utf-8")
    go_pattern = re.search(r"extraEventLogRe = regexp\.MustCompile\(`([^`]+)`\)", go).group(1)
    assert go_pattern == required._CHANNEL_RE.pattern


def test_only_real_log_sources_reach_the_review_list():
    assert required.looks_like_log_source("Microsoft-Windows-Kernel-EventTracing/Admin", "windows")
    assert required.looks_like_log_source("HardwareEvents", "windows")
    assert required.looks_like_log_source("file:C:\\Vendor\\Logs\\app.log", "windows")
    assert required.looks_like_log_source("file:%ProgramData%\\Vendor\\app.log", "windows")
    assert required.looks_like_log_source("file:~/Library/Logs/Vendor/app.log", "macos")
    assert required.looks_like_log_source("macos:thermal", "macos")
    for junk in ("Event Viewer", "check the logs", "file:relative.log", "file:C:\\x\\..\\y.log", "macos:../x"):
        assert not required.looks_like_log_source(junk, "windows"), junk


def test_suggested_approvals():
    assert required.suggest_approval("Microsoft-Windows-Kernel-EventTracing/Admin", "windows") == (
        "event_log", "Microsoft-Windows-Kernel-EventTracing/Admin")
    assert required.suggest_approval("file:C:\\ProgramData\\Vendor\\Logs\\app.log", "windows") == (
        "folder", "C:\\ProgramData\\Vendor\\Logs")
    assert required.suggest_approval("file:%LOCALAPPDATA%\\Vendor\\Logs\\*.log", "windows") == (
        "folder", "C:\\Users\\*\\AppData\\Local\\Vendor\\Logs")
    assert required.suggest_approval("file:/opt/vendor/logs/app.log", "macos") == ("folder", "/opt/vendor/logs")
    # Too shallow to approve as a folder: the admin types one instead.
    assert required.suggest_approval("file:C:\\app.log", "windows") == ("folder", None)
    assert required.suggest_approval("macos:thermal", "macos") == (None, None)


def test_approval_values_are_validated():
    assert required.validate_approval("folder", "C:/ProgramData/Vendor/Logs/", "windows") == "C:\\ProgramData\\Vendor\\Logs"
    assert required.validate_approval("folder", "C:\\Users", "windows") is None
    assert required.validate_approval("folder", "C:\\", "windows") is None
    assert required.validate_approval("folder", "C:\\Vendor\\..\\Windows", "windows") is None
    assert required.validate_approval("event_log", "Microsoft-Windows-Ntfs/Operational", "windows")
    assert required.validate_approval("event_log", "Microsoft-Windows-Ntfs/Operational", "macos") is None
    assert required.validate_approval(None, "anything", "windows") is None


def test_unavailable_placeholders():
    assert required.unavailable_detail("[unavailable: exit status 15007]") == "exit status 15007"
    assert required.unavailable_detail("[refused: path must be absolute]") == "path must be absolute"
    assert required.unavailable_detail("[no matching log files found]").startswith("No matching log files")
    assert required.unavailable_detail("[no entries in this window]") is None
    assert required.unavailable_detail("2026-10-22 [Error] boom") is None


# --------------------------------------------------------------------------- #
# Recording and approving
# --------------------------------------------------------------------------- #


@pytest.mark.anyio
async def test_record_inserts_counts_and_reopens(store):
    entry = {"source": "Microsoft-Windows-Kernel-EventTracing/Admin", "reason_code": "not_allowed", "reason": "ETW"}
    assert await required.record([entry, dict(entry)], feature="troubleshooter", platform="windows",
                                 company_id=1, asset_id=10, ticket_id=77) == 1
    rows = await repo.list_entries()
    assert len(rows) == 1 and rows[0]["occurrences"] == 1 and rows[0]["features"] == "troubleshooter"
    assert rows[0]["approve_kind"] == "event_log" and rows[0]["last_ticket_id"] == 77

    await required.record([entry], feature="health_check", platform="windows", health_check_id=4)
    row = (await repo.list_entries())[0]
    assert row["occurrences"] == 2 and row["features"] == "troubleshooter,health_check"
    assert row["last_ticket_id"] == 77 and row["last_health_check_id"] == 4

    # Asked for again after approval: the approval did not cover it.
    await repo.set_status(row["id"], status="approved", note=None, user_id=9, approve_value="Other/Channel")
    await required.record([entry], feature="health_check", platform="windows")
    assert (await repo.get(row["id"]))["status"] == "open"
    # Dismissed stays dismissed.
    await repo.set_status(row["id"], status="dismissed", note="noise", user_id=9)
    await required.record([entry], feature="health_check", platform="windows")
    assert (await repo.get(row["id"]))["status"] == "dismissed"

    # Bad entries are skipped and a database failure never raises.
    assert await required.record([{"source": "", "reason_code": "not_allowed"},
                                  {"source": "x", "reason_code": "other"}], feature="health_check",
                                 platform="windows") == 0


@pytest.mark.anyio
async def test_record_survives_a_database_failure(monkeypatch):
    async def broken(*args, **kwargs):
        raise RuntimeError("database down")

    monkeypatch.setattr(repo, "get_by_key", broken)
    assert await required.record([{"source": "System", "reason_code": "unavailable"}],
                                 feature="troubleshooter", platform="windows") == 0


@pytest.mark.anyio
async def test_approvals_join_the_allowlists(store, monkeypatch):
    monkeypatch.setattr(ts.get_settings(), "troubleshoot_event_logs_windows", "HardwareEvents; bad//name")
    monkeypatch.setattr(ts.get_settings(), "troubleshoot_log_folders_windows", None)
    await required.record([
        {"source": "Microsoft-Windows-Kernel-EventTracing/Admin", "reason_code": "not_allowed"},
        {"source": "file:C:\\ProgramData\\Vendor\\Logs\\app.log", "reason_code": "not_allowed"},
    ], feature="troubleshooter", platform="windows")
    rows = {row["source"]: row for row in await repo.list_entries()}
    for row in rows.values():
        await repo.set_status(row["id"], status="approved", note=None, user_id=9, approve_value=row["approve_value"])
    await required.refresh_approvals(force=True)

    assert ts.extra_event_logs("windows") == ["HardwareEvents", "Microsoft-Windows-Kernel-EventTracing/Admin"]
    allowed = ts.allowed_log_sources("windows")
    assert allowed["Microsoft-Windows-Kernel-EventTracing/Admin"] == "Approved by this portal's owner"
    assert "C:\\ProgramData\\Vendor\\Logs" in ts.extra_log_folders("windows")
    assert ts.normalise_file_source("file:C:\\ProgramData\\Vendor\\Logs\\app.log", "windows")
    assert ts.extra_event_logs("macos") == [] and "C:\\ProgramData\\Vendor\\Logs" not in ts.extra_log_folders("macos")

    # Dismissing removes it again.
    await repo.set_status(rows["file:C:\\ProgramData\\Vendor\\Logs\\app.log"]["id"], status="dismissed", note=None, user_id=9)
    await required.refresh_approvals(force=True)
    assert "C:\\ProgramData\\Vendor\\Logs" not in ts.extra_log_folders("windows")


# --------------------------------------------------------------------------- #
# The troubleshooter records what it could not have
# --------------------------------------------------------------------------- #


def test_plan_splits_allowed_and_refused_logs():
    allowed = ts.allowed_log_sources("windows")
    requests, refused = ts.parse_log_requests([
        {"source": "System", "reason": "drivers", "hours": 24},
        {"source": "Microsoft-Windows-Kernel-EventTracing/Admin", "reason": "ETW"},
        {"source": "file:C:\\Vendor\\trace.log", "reason": "vendor"},
        {"source": "Event Viewer", "reason": "look around"},
        {"source": "microsoft-windows-kernel-eventtracing/admin", "reason": "duplicate"},
    ], allowed, "windows")
    assert [r["source"] for r in requests] == ["System"]
    assert [r["source"] for r in refused] == ["Microsoft-Windows-Kernel-EventTracing/Admin", "file:C:\\Vendor\\trace.log"]
    html = ts.refused_logs_html(refused)
    assert 'href="/admin/required-logs"' in html and "vendor" in html
    assert ts.refused_logs_html([]) == ""


def test_unavailable_entries_from_a_bundle():
    sections = {
        "System": "2026 [Error] x",
        "Microsoft-Windows-DNS-Client/Operational": "[unavailable: The specified channel is disabled]",
        "file:C:\\Windows\\Logs\\CBS\\CBS.log": "[no matching log files found]",
    }
    requests = [
        {"source": "System", "reason": "a"},
        {"source": "Microsoft-Windows-DNS-Client/Operational", "reason": "dns"},
        {"source": "file:C:\\Windows\\Logs\\CBS\\*.log", "reason": "cbs"},
        {"source": "Microsoft-Windows-Time-Service/Operational", "reason": "time"},
    ]
    entries = {e["source"]: e for e in ts.unavailable_log_entries(sections, requests)}
    assert set(entries) == {"Microsoft-Windows-DNS-Client/Operational", "file:C:\\Windows\\Logs\\CBS\\CBS.log",
                            "Microsoft-Windows-Time-Service/Operational"}
    assert entries["Microsoft-Windows-DNS-Client/Operational"]["detail"] == "The specified channel is disabled"
    assert entries["file:C:\\Windows\\Logs\\CBS\\CBS.log"]["reason"] == "cbs"
    assert "did not collect" in entries["Microsoft-Windows-Time-Service/Operational"]["detail"]


@pytest.mark.anyio
async def test_troubleshooter_records_refused_and_unavailable_logs(monkeypatch):
    recorded = []

    async def record(entries, **kwargs):
        recorded.append((list(entries), kwargs))
        return len(entries)

    async def allowed(ticket_id):
        return True

    async def note(ticket_id, body_html):
        notes.append(body_html)

    async def dispatch(**kwargs):
        dispatched.append(kwargs)
        return 42, True

    async def no_kb(*args, **kwargs):
        return {"results": []}

    async def no_stored(requests, **kwargs):
        return [], [dict(r) for r in requests]

    responses = [
        json.dumps({"articles": [], "search_terms": []}),
        json.dumps({"summary": "s", "steps": [{"action": "Check DNS"}], "log_requests": [
            {"source": "Microsoft-Windows-DNS-Client/Operational", "reason": "dns", "hours": 4},
            {"source": "Microsoft-Windows-Kernel-EventTracing/Admin", "reason": "ETW"},
        ]}),
    ]

    async def chat(llm, messages, *, json_mode=False):
        return responses.pop(0)

    notes: list[str] = []
    dispatched: list[dict] = []
    monkeypatch.setattr(ts.ai_required_logs, "record", record)
    monkeypatch.setattr(ts.ai_consent, "is_ai_allowed_for_ticket_id", allowed)
    monkeypatch.setattr(tray_service, "add_troubleshoot_note", note)
    monkeypatch.setattr(tray_service, "dispatch_troubleshoot_command", dispatch)
    monkeypatch.setattr(ts, "_chat", chat)
    monkeypatch.setattr(ts.knowledge_base_service, "search_articles", no_kb)
    monkeypatch.setattr(ts, "split_stored_log_requests", no_stored)

    plan = await ts.run_research_and_plan(
        ticket={"id": 77, "company_id": 3, "subject": "DNS"}, asset={"asset_id": 1, "name": "PC"},
        device={"id": 5, "device_uid": "dev", "status": "active", "company_id": 3, "os": "windows"},
        problem="DNS fails", llm={"base_url": "http://llm", "model": "m"},
    )
    assert [r["source"] for r in plan["refused_logs"]] == ["Microsoft-Windows-Kernel-EventTracing/Admin"]
    entries, kwargs = recorded[0]
    assert entries[0]["reason_code"] == "not_allowed" and kwargs["feature"] == "troubleshooter"
    assert kwargs["ticket_id"] == 77 and kwargs["platform"] == "windows"
    assert any("Logs not on the approved list" in body for body in notes)
    payload = dispatched[0]["payload"]
    assert payload["extra_event_logs"] == [] and payload["server_context"]["platform"] == "windows"
    assert payload["server_context"]["asset_id"] == 1 and payload["server_context"]["company_id"] == 3

    # The device could not read the DNS log: recorded as unavailable.
    recorded.clear()
    responses.append(json.dumps({"summary": "ok"}))
    await ts.analyse_collected_logs(
        ticket_id=77, command={"payload_json": payload},
        log_text="### Microsoft-Windows-DNS-Client/Operational Log\n[unavailable: The channel is disabled]\n\n",
        endpoint_label="PC", llm={"base_url": "http://llm", "model": "m"},
    )
    entries, kwargs = recorded[0]
    assert entries == [{"source": "Microsoft-Windows-DNS-Client/Operational", "reason_code": "unavailable",
                        "reason": "dns", "detail": "The channel is disabled"}]
    assert kwargs["platform"] == "windows" and kwargs["asset_id"] == 1


# --------------------------------------------------------------------------- #
# The review page
# --------------------------------------------------------------------------- #


@pytest.fixture
def client(store, monkeypatch):
    from types import SimpleNamespace

    from fastapi import FastAPI
    from fastapi.responses import HTMLResponse
    from fastapi.testclient import TestClient

    from app.api.dependencies import auth
    from app.features.health_check import routes

    user = {"id": 9, "is_super_admin": True}
    rendered = []

    async def require_super_admin_page(request):
        return user, None

    async def render_template(template, request, current_user, extra=None):
        rendered.append((template, extra))
        return HTMLResponse(template)

    async def current_user():
        return user

    monkeypatch.setattr(routes, "_main", lambda: SimpleNamespace(
        _require_super_admin_page=require_super_admin_page, _render_template=render_template))
    audits = []

    async def log_action(**kwargs):
        audits.append(kwargs)

    monkeypatch.setattr(routes.audit_service, "log_action", log_action)
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[auth.require_super_admin] = current_user
    with TestClient(app) as test_client:
        yield test_client, rendered, audits


@pytest.mark.anyio
async def test_review_page_approves_dismisses_and_reopens(client, store):
    test_client, rendered, audits = client
    await required.record([
        {"source": "file:C:\\app.log", "reason_code": "not_allowed", "reason": "vendor"},
        {"source": "Microsoft-Windows-DNS-Client/Operational", "reason_code": "unavailable", "detail": "disabled"},
    ], feature="health_check", platform="windows", health_check_id=4)
    rows = {row["source"]: row for row in await repo.list_entries()}
    shallow = rows["file:C:\\app.log"]

    assert test_client.get("/admin/required-logs").status_code == 200
    template, extra = rendered[-1]
    assert template == "health_checks/required_logs.html" and extra["counts"]["open"] == 2
    presented = {e["source"]: e for e in extra["entries"]}
    assert presented["file:C:\\app.log"]["can_approve"] and not presented["file:C:\\app.log"]["currently_allowed"]
    assert not presented["Microsoft-Windows-DNS-Client/Operational"]["can_approve"]

    # A folder that is too broad is refused; a proper one is approved and takes effect.
    response = test_client.post(f"/admin/required-logs/{shallow['id']}/status",
                                data={"action": "approve", "approve_value": "C:\\"}, follow_redirects=False)
    assert response.status_code == 303 and (await repo.get(shallow["id"]))["status"] == "open"
    response = test_client.post(f"/admin/required-logs/{shallow['id']}/status",
                                data={"action": "approve", "approve_value": "C:\\Vendor\\Logs", "note": "ok"},
                                follow_redirects=False)
    assert response.status_code == 303
    row = await repo.get(shallow["id"])
    assert row["status"] == "approved" and row["approve_value"] == "C:\\Vendor\\Logs" and row["status_note"] == "ok"
    assert "C:\\Vendor\\Logs" in ts.extra_log_folders("windows")
    assert audits[-1]["action"] == "ai_required_logs.approve"

    unavailable = rows["Microsoft-Windows-DNS-Client/Operational"]
    api = test_client.patch(f"/api/required-logs/{unavailable['id']}", json={"action": "approve"})
    assert api.status_code == 400
    api = test_client.patch(f"/api/required-logs/{unavailable['id']}", json={"action": "dismiss"})
    assert api.status_code == 200 and api.json()["required_log"]["status"] == "dismissed"
    api = test_client.patch(f"/api/required-logs/{unavailable['id']}", json={"action": "reopen"})
    assert api.json()["required_log"]["status"] == "open"
    assert test_client.patch("/api/required-logs/999", json={"action": "dismiss"}).status_code == 404
    listed = test_client.get("/api/required-logs?view=approved").json()
    assert [e["source"] for e in listed["required_logs"]] == ["file:C:\\app.log"]
    assert listed["approved"]["folders"] == ["C:\\Vendor\\Logs"]


def test_review_template_renders():
    env = _environment()
    env.globals["static_url"] = lambda path: path
    entry = {
        "id": 3, "source": "file:C:\\<b>app</b>.log", "kind": "file", "kind_label": "Log file",
        "reason_code": "not_allowed", "reason_label": "Not on the approved list", "status": "open",
        "status_label": "Needs review", "platform": "windows", "feature_labels": ["Health check"],
        "occurrences": 2, "reason": "vendor", "detail": None, "approve_kind": "folder", "approve_value": "",
        "approve_label": "Log folder to allow", "can_approve": True, "currently_allowed": False,
        "seen_since_review": False, "last_seen_at": "2026-10-22 01:00:00", "first_seen_at": "2026-10-21 01:00:00",
        "last_asset_id": 10, "last_ticket_id": None, "last_health_check_id": 4, "status_note": None,
    }
    html = env.get_template("health_checks/required_logs.html").render(
        entries=[entry], view="open", counts={"open": 1, "approved": 0, "dismissed": 0}, csrf_token="tok",
        status_labels={"open": "Needs review"}, approved={"event_logs": [], "folders": [], "env_event_logs": False,
                                                          "env_folders": True},
    )
    assert "&lt;b&gt;app&lt;/b&gt;" in html and "<b>app</b>" not in html
    assert 'action="/admin/required-logs/3/status"' in html and 'name="approve_value"' in html
    assert 'href="/health-checks/4"' in html and "log folders" in html
