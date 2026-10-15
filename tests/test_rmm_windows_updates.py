"""Windows Update status per RMM device: scans, run capture, chosen installs and the asset page."""
from __future__ import annotations

import base64
import gzip
import json
import os
import shutil
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode

import pytest
from starlette.requests import Request

from app.core.database import Database
from app.repositories import rmm as rmm_repo
from app.repositories import rmm_windows_updates as repo
from app.services import rmm_patching, rmm_scripts
from app.services import rmm_windows_updates as updates
from app.services.rmm_scripts import RunRequestError
from tests.test_rmm_scripts import _enrol, _environment, anyio_backend, sqlite_db  # noqa: F401 - fixtures

ROOT = Path(__file__).resolve().parents[1]
POWERSHELL = shutil.which("powershell") or shutil.which("pwsh")
CU = "0b3a2c51-6d6c-4b4e-9e57-6f7d4b0a1c01"
EDGE = "1c2d3e4f-5a6b-4c7d-8e9f-a0b1c2d3e4f5"
DEFENDER = "2d3e4f5a-6b7c-4d8e-9fa0-b1c2d3e4f5a6"
OLD = "3e4f5a6b-7c8d-4e9f-a0b1-c2d3e4f5a6b7"


@pytest.fixture
async def updates_db(sqlite_db, monkeypatch):
    sql = (ROOT / "migrations" / "496_rmm_windows_updates.sql").read_text()
    await sqlite_db.conn.executescript(Database()._adapt_sql_for_sqlite(sql))

    async def execute_many(sql, rows):
        await sqlite_db.conn.executemany(sqlite_db._sql(sql), list(rows))

    sqlite_db.execute_many = execute_many
    monkeypatch.setattr(repo, "db", sqlite_db)
    yield sqlite_db


def _ago(**delta) -> str:
    return (datetime.utcnow().replace(microsecond=0) - timedelta(**delta)).isoformat() + "Z"


def _status(**overrides):
    status = {
        "version": 1,
        "scanned_at": "2026-10-14T01:00:00Z",
        "source": "WSUS (http://wsus.example.com:8530)",
        "reboot_pending": False,
        "errors": [],
        "missing": [
            {"update_id": CU.upper(), "revision": 200, "title": "2026-10 Cumulative Update (KB5031356)",
             "kb": "KB5031356", "classification": "Security Updates", "severity": "Critical",
             "size_bytes": 734003200, "released": "2026-10-08", "downloaded": True, "reboot_behavior": 1,
             "interactive": False, "optional": False},
            {"update_id": EDGE, "revision": 1, "title": "<b>Edge</b> setup", "kb": "", "classification": "Updates",
             "severity": "", "size_bytes": 1024, "released": "2026-10-01", "downloaded": False, "reboot_behavior": 0,
             "interactive": True, "optional": True},
            {"update_id": "not-a-guid", "title": "Dropped"},
            {"update_id": CU, "title": "Duplicate"},
        ],
        "history": [
            {"update_id": CU, "title": "2026-10 Cumulative Update (KB5031356)", "operation": 1, "result": 4,
             "hresult": "0x80240022", "date": _ago(days=3), "client": "MyPortal Patch Management"},
            {"update_id": DEFENDER, "title": "Security Intelligence Update (KB2267602)", "classification": "Definition Updates",
             "operation": 1, "result": 2, "hresult": "0x00000000", "date": _ago(days=2), "client": "UpdateOrchestrator"},
            {"update_id": OLD, "title": "2026-09 Cumulative Update (KB5030000)", "operation": 2, "result": 2,
             "hresult": "0x00000000", "date": _ago(days=40), "client": "wusa"},
            {"update_id": OLD, "title": "2026-09 Cumulative Update (KB5030000)", "operation": 1, "result": 2,
             "hresult": "0x00000000", "date": _ago(days=50), "client": "UpdateOrchestrator"},
            {"update_id": "", "title": "", "operation": 1, "result": 2},
        ],
        "hotfixes": [
            {"kb": "KB2267602", "description": "Update", "installed_on": "2026-10-13"},
            {"kb": "KB5029999", "description": "Security Update", "installed_on": "2026-08-01", "installed_by": "NT AUTHORITY\\SYSTEM"},
            {"kb": "not a kb"},
        ],
    }
    status.update(overrides)
    return status


def _encode(payload) -> str:
    return base64.b64encode(gzip.compress(json.dumps(payload).encode())).decode()


def _output(payload, before="Scanning\n", after="") -> str:
    return before + updates.MARKER + _encode(payload) + "\n" + after


async def _finish_scan(agent, payload, *, user_id=None):
    run_id = await updates.queue_scan(agent, user_id=user_id)
    assert run_id
    [job] = await rmm_scripts.claim_jobs(agent)
    assert job["id"] == run_id
    await rmm_scripts.record_result(agent, run_id, {"exit_code": 0, "stdout": _output(payload)})
    return run_id


def test_decode_refuses_malformed_or_oversized_status():
    assert updates.decode(_encode({"version": 1})) == {"version": 1}
    for bad, message in (("%%%", "base64"), (base64.b64encode(b"plain").decode(), "decompressed"),
                         (base64.b64encode(gzip.compress(b"{")).decode(), "JSON"),
                         (base64.b64encode(gzip.compress(b"\0" * (updates.MAX_DECODED_BYTES + 1))).decode(), "too large"),
                         ("A" * (updates.MAX_ENCODED_CHARS + 4), "too large")):
        with pytest.raises(ValueError, match=message):
            updates.decode(bad)
    with pytest.raises(ValueError, match="not supported"):
        updates.normalise({"version": 2})
    with pytest.raises(ValueError, match="not supported"):
        updates.normalise(["not", "a", "status"])


def test_normalise_trims_and_keeps_unreadable_parts_unknown():
    state = updates.normalise(_status())
    assert [item["update_id"] for item in state["missing"]] == [CU, EDGE]
    assert state["missing"][0]["kb"] == "KB5031356" and state["missing"][0]["released_on"].isoformat() == "2026-10-08"
    assert state["missing"][1]["is_interactive"] and state["missing"][1]["kb"] is None
    assert len(state["history"]) == 4
    assert state["history"][0]["result"] == "failed" and state["history"][0]["hresult"] == "0x80240022"
    assert state["history"][1]["hresult"] is None and state["history"][1]["kb"] == "KB2267602"
    assert state["history"][2]["operation"] == "uninstall"
    assert [fix["title"] for fix in state["hotfixes"]] == ["Update (KB2267602)", "Security Update (KB5029999)"]
    assert state["scanned_at"] == datetime(2026, 10, 14, 1)
    partial = updates.normalise(_status(missing=None, history="broken", errors=["The search failed (0x8024402C)"]))
    assert partial["missing"] is None and partial["history"] is None and partial["errors"] == ["The search failed (0x8024402C)"]


@pytest.mark.anyio
async def test_scan_run_saves_status_and_removes_the_status_line(updates_db):
    _, agent = await _enrol(updates_db)
    run_id = await _finish_scan(agent, _status(), user_id=9)
    run = await rmm_repo.get_run(run_id)
    assert run["status"] == "completed" and run["run_source"] == updates.RUN_SOURCE
    assert updates.MARKER not in run["stdout"] and "Scanning" in run["stdout"]
    assert "MyPortal saved the Windows Update status: 2 missing updates, 4 history entries, 2 installed hotfixes." in run["stdout"]
    scan = await repo.get_scan(agent["id"])
    assert scan["missing_count"] == 2 and scan["update_source"].startswith("WSUS") and scan["run_id"] == run_id
    assert scan["scan_requested_by_user_id"] == 9 and scan["missing_checked_at"]
    missing = await repo.list_missing(agent["id"])
    first_seen = {row["update_id"]: row["first_seen_at"] for row in missing}
    assert set(first_seen) == {CU, EDGE}

    # The search failed this time: missing updates and hotfixes stay as they were.
    await updates_db.execute("UPDATE rmm_windows_updates SET first_seen_at = '2026-10-01 00:00:00'")
    await _finish_scan(agent, _status(missing=None, hotfixes=None, history=[], errors=["Search failed"], reboot_pending=True))
    scan = await repo.get_scan(agent["id"])
    assert scan["reboot_pending"] and scan["errors"] == ["Search failed"] and scan["missing_count"] == 2
    assert len(await repo.list_missing(agent["id"])) == 2
    history = await repo.list_history(agent["id"])
    assert {row["source"] for row in history} == {"hotfix"}

    # One update was installed: the other keeps the date it was first seen missing.
    await _finish_scan(agent, _status(missing=[_status()["missing"][1]]))
    [remaining] = await repo.list_missing(agent["id"])
    assert remaining["update_id"] == EDGE and str(remaining["first_seen_at"]).startswith("2026-10-01")


@pytest.mark.anyio
async def test_only_builtin_runs_report_status_and_bad_status_is_explained(updates_db):
    _, agent = await _enrol(updates_db)
    script = {"id": None, "name": "Custom", "path": "rmm/custom.ps1", "language": "powershell", "content_sha256": "x"}
    run_id = await rmm_repo.create_run(
        script=script, content="Write-Output hi", agent_id=agent["id"], company_id=1, asset_id=10,
        requested_by_user_id=9, inputs=[], payload_encrypted=rmm_scripts.encrypt_secret("{}"),
        timeout_seconds=60, expires_at=datetime.utcnow() + timedelta(minutes=5),
    )
    await rmm_scripts.claim_jobs(agent)
    stdout = _output(_status())
    await rmm_scripts.record_result(agent, run_id, {"exit_code": 0, "stdout": stdout})
    assert updates.MARKER in (await rmm_repo.get_run(run_id))["stdout"]
    assert await repo.get_scan(agent["id"]) is None

    scan_id = await updates.queue_scan(agent)
    await rmm_scripts.claim_jobs(agent)
    await rmm_scripts.record_output(agent, scan_id, {"stdout": "Scanning\n" + updates.MARKER + "partial"})
    assert updates.MARKER not in (await rmm_repo.get_run(scan_id))["stdout"]
    await rmm_scripts.record_result(agent, scan_id, {"exit_code": 0, "stdout": "x\n" + updates.MARKER + "%%%\n"})
    run = await rmm_repo.get_run(scan_id)
    assert "MyPortal could not read the Windows Update status: the status is not valid base64." in run["stdout"]
    assert updates.MARKER not in run["stdout"] and await repo.get_scan(agent["id"]) is not None
    assert (await repo.get_scan(agent["id"]))["received_at"] is None


@pytest.mark.anyio
async def test_scans_are_windows_only_and_not_duplicated(updates_db):
    _, agent = await _enrol(updates_db)
    _, linux = await _enrol(updates_db, asset_id=11, uid="linux-agent", tray_id=6, os_name="linux")
    with pytest.raises(RunRequestError):
        await updates.queue_scan(linux)
    run_id = await updates.queue_scan(agent)
    assert run_id and await updates.queue_scan(agent) is None
    [job] = await rmm_scripts.claim_jobs(agent)
    assert job["language"] == "powershell" and job["timeout_seconds"] == updates.SCAN_TIMEOUT_SECONDS
    assert "Send-MyPortalWindowsUpdateState" in job["script"] and "IsInstalled=0" in job["script"]
    assert "Install(" not in job["script"]  # a scan never installs
    assert await updates.asset_context(linux, can_run=True) is None


@pytest.mark.anyio
async def test_install_validates_choices_and_queues_a_patch_job(updates_db):
    _, agent = await _enrol(updates_db)
    await _finish_scan(agent, _status())
    cases = [
        ([], "never", 15, "updates"),
        ([OLD], "never", 15, "updates"),
        ([EDGE], "never", 15, "updates"),  # needs someone at the device
        ([CU], "now", 15, "reboot_mode"),
        ([CU], "always", 1, "reboot_delay_minutes"),
        ([f"{index:08x}-0000-4000-8000-000000000000" for index in range(101)], "never", 15, "updates"),
    ]
    for chosen, mode, delay, key in cases:
        with pytest.raises(RunRequestError) as exc:
            await updates.queue_install(agent, chosen, reboot_mode=mode, reboot_delay_minutes=delay, user_id=9)
        assert key in exc.value.errors
    run_id = await updates.queue_install(agent, [CU.upper(), CU], reboot_mode="no_users", reboot_delay_minutes="30", user_id=9)
    with pytest.raises(RunRequestError, match="already waiting"):
        await updates.queue_install(agent, [CU], reboot_mode="never", reboot_delay_minutes=15, user_id=9)
    [job] = await rmm_scripts.claim_jobs(agent)
    assert job["id"] == run_id and job["timeout_seconds"] == 7200
    plan = json.loads(job["env"]["MYPORTAL_PATCH_PLAN"])
    assert plan["manual_windows"] == {"update_ids": [CU]} and plan["windows"] is None and plan["apps"] == []
    assert plan["reboot"] == {"reboot_mode": "no_users", "reboot_delay_minutes": 30, "immediate": True,
                              "active_start": 0, "active_end": 0}
    assert "Send-MyPortalWindowsUpdateState" in job["script"] and "manual_windows" in job["script"]
    run = await rmm_repo.get_run(run_id)
    assert run["script_name"] == "Install Windows updates" and run["run_source"] == "patch"
    assert {"key": "updates", "label": "Updates", "value": "KB5031356"} in run["inputs"]
    assert await rmm_patching.job_is_current(agent, job)
    assert not await rmm_patching.job_is_current({**agent, "os": "linux"}, job)
    tampered = {"env": {"MYPORTAL_PATCH_PLAN": json.dumps({**plan, "manual_windows": {"update_ids": ["; shutdown"]}})}}
    assert not await rmm_patching.job_is_current(agent, tampered)
    context = await updates.asset_context(agent, can_run=True)
    assert context["install_pending"]


@pytest.mark.anyio
async def test_tick_scans_connected_windows_devices_daily(updates_db):
    _, agent = await _enrol(updates_db)
    _, linux = await _enrol(updates_db, asset_id=11, uid="linux-agent", tray_id=6, os_name="linux")
    now = updates.utcnow()
    await updates_db.execute("UPDATE rmm_agents SET last_seen_utc = %s", (now - timedelta(minutes=1),))
    await updates.tick()
    runs = await rmm_repo.next_jobs_for_agent(agent["id"])
    assert len(runs) == 1 and await rmm_repo.next_jobs_for_agent(linux["id"]) == []
    await updates.tick()
    assert len(await rmm_repo.next_jobs_for_agent(agent["id"])) == 1
    # A scan that never reported back is retried hours later, not on every tick.
    await rmm_repo.cancel_run(runs[0]["id"])
    await updates.tick()
    assert await rmm_repo.next_jobs_for_agent(agent["id"]) == []
    await updates_db.execute("UPDATE rmm_windows_update_scans SET scan_requested_at = %s", (now - timedelta(hours=7),))
    await updates.tick()
    [retry] = await rmm_repo.next_jobs_for_agent(agent["id"])
    await rmm_repo.cancel_run(retry["id"])
    # A fresh scan, or a device that is not connected, is left alone.
    await updates_db.execute("UPDATE rmm_windows_update_scans SET scan_requested_at = NULL, received_at = %s",
                             (now - timedelta(hours=2),))
    await updates.tick()
    assert await rmm_repo.next_jobs_for_agent(agent["id"]) == []
    await updates_db.execute("UPDATE rmm_windows_update_scans SET received_at = %s", (now - timedelta(days=2),))
    await updates_db.execute("UPDATE rmm_agents SET last_seen_utc = %s", (now - timedelta(hours=1),))
    await updates.tick()
    assert await rmm_repo.next_jobs_for_agent(agent["id"]) == []


def test_installed_updates_follow_history_and_add_unlisted_hotfixes():
    state = updates.normalise(_status())
    history = [{**row, "source": "history", "occurred_at": row["occurred_at"].isoformat()} for row in state["history"]]
    history += [{**row, "source": "hotfix", "occurred_at": row["occurred_at"].isoformat()} for row in state["hotfixes"]]
    installed = updates.installed_updates(history)
    # The September update was uninstalled, the October one failed, Defender's KB is in history.
    assert [row["kb"] for row in installed] == ["KB2267602", "KB5029999"]
    assert installed[1]["source"] == "hotfix"


@pytest.mark.anyio
async def test_asset_section_lists_missing_installed_and_actions(updates_db):
    from app.features.rmm import windows_update_routes as routes

    _, agent = await _enrol(updates_db)
    assert (await routes.asset_windows_updates_context(1, 11, can_run=True)) is None  # no agent on that asset
    assert (await routes.asset_windows_updates_context(2, 10, can_run=True)) is None  # another company's device
    empty = await routes.asset_windows_updates_context(1, 10, can_run=True)
    assert empty["scan"] is None and empty["missing"] == []
    env = _environment()
    html = env.get_template("rmm/_asset_windows_updates.html").render(rmm={"windows_updates": empty}, asset={"id": 10}, csrf_token="t")
    assert "has not reported its Windows updates yet" in html and 'action="/rmm/assets/10/windows-updates/scan"' in html

    await _finish_scan(agent, _status(reboot_pending=True))
    context = await routes.asset_windows_updates_context(1, 10, can_run=True)
    assert context["counts"] == {"missing": 2, "important": 1, "installed": 2, "failed_recently": 1}
    cumulative = next(item for item in context["missing"] if item["update_id"] == CU)
    assert cumulative["last_failure"]["hresult"] == "0x80240022" and cumulative["size_label"] == "700 MB"
    html = env.get_template("rmm/_asset_windows_updates.html").render(rmm={"windows_updates": context}, asset={"id": 10}, csrf_token="t")
    assert 'id="windows-updates"' in html and 'action="/rmm/assets/10/windows-updates/install"' in html
    assert f'name="update_ids" value="{CU}"' in html and f'value="{EDGE}"' not in html
    assert "Needs someone at the device" in html and "&lt;b&gt;Edge&lt;/b&gt;" in html and "<b>Edge" not in html
    assert "0x80240022" in html and "Install selected updates" in html and 'name="reboot_mode"' in html
    assert "Security Update (KB5029999)" in html and "2026-08-01" in html and "Windows automatic updates" in html
    assert "Restart pending" in html and "WSUS (http://wsus.example.com:8530)" in html
    read_only = env.get_template("rmm/_asset_windows_updates.html").render(
        rmm={"windows_updates": {**context, "can_run": False}}, asset={"id": 10}, csrf_token="t")
    assert "update_ids" not in read_only and "Scan now" not in read_only and "KB5031356" in read_only

    detail = (ROOT / "app/templates/assets/detail.html").read_text()
    env.parse(detail)
    assert '{% include "rmm/_asset_windows_updates.html" %}' in detail and "rmm_windows_updates.js" in detail


def _form_request(path: str, data: list[tuple[str, str]]) -> Request:
    body = urlencode(data).encode()

    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    return Request({"type": "http", "method": "POST", "path": path, "headers": [
        (b"content-type", b"application/x-www-form-urlencoded")]}, receive)


@pytest.mark.anyio
async def test_routes_queue_scan_and_install_with_audit(updates_db, monkeypatch):
    from fastapi import HTTPException
    from app.features.rmm import windows_update_routes as routes

    _, agent = await _enrol(updates_db)
    audits = []

    async def context(request, **kwargs):
        return {"id": 9}, {}, 1, True

    async def audit(**kwargs):
        audits.append(kwargs)

    monkeypatch.setattr(routes, "_context", context)
    monkeypatch.setattr(routes.audit_service, "record", audit)
    response = await routes.scan_windows_updates(10, _form_request("/scan", []))
    assert response.headers["location"] == "/assets/10#windows-updates" and audits[-1]["action"] == "rmm.windows_updates.scan"
    await routes.scan_windows_updates(10, _form_request("/scan", []))
    assert len(audits) == 1  # already waiting
    [job] = await rmm_scripts.claim_jobs(agent)
    await rmm_scripts.record_result(agent, job["id"], {"exit_code": 0, "stdout": _output(_status())})
    await routes.install_windows_updates(10, _form_request("/install", [("update_ids", OLD), ("reboot_mode", "never")]))
    assert len(audits) == 1  # refused: not missing on this device
    await routes.install_windows_updates(10, _form_request("/install", [("update_ids", CU), ("reboot_mode", "always"),
                                                                      ("reboot_delay_minutes", "10")]))
    assert audits[-1]["action"] == "rmm.windows_updates.install" and audits[-1]["metadata"]["update_ids"] == [CU]
    api = await routes.windows_updates_api(10, _form_request("/api", []))
    assert api["supported"] and api["install_pending"] and len(api["missing"]) == 2
    with pytest.raises(HTTPException) as exc:
        await routes.install_windows_updates(20, _form_request("/install", [("update_ids", CU)]))
    assert exc.value.status_code == 404


# --------------------------------------------------------------------------- #
# The PowerShell scripts, with Windows Update's COM objects mocked
# --------------------------------------------------------------------------- #

COM_MOCKS = r'''
$global:Installed = New-Object System.Collections.Generic.List[string]
function New-FakeUpdate($id, $title, $interactive) {
    $update = [pscustomobject]@{
        Identity = [pscustomobject]@{ UpdateID = $id; RevisionNumber = 1 }; Title = $title; KBArticleIDs = @('5031356')
        Categories = @([pscustomobject]@{ CategoryID = '0FA1201D-4330-4FA8-8AE9-B877473B6441'; Name = 'Sicherheit'; Type = 'UpdateClassification' })
        MsrcSeverity = 'Critical'; MaxDownloadSize = [decimal]1048576; LastDeploymentChangeTime = [datetime]'2026-10-08'
        IsDownloaded = $false; EulaAccepted = $false; BrowseOnly = $false
        InstallationBehavior = [pscustomobject]@{ RebootBehavior = 1; CanRequestUserInput = $interactive }
    }
    $update | Add-Member ScriptMethod AcceptEula { $this.EulaAccepted = $true }
    return $update
}
function New-Object {
    param([string]$TypeName, [object[]]$ArgumentList, [string]$ComObject)
    switch ($ComObject) {
        'Microsoft.Update.Session' {
            $searcher = [pscustomobject]@{}
            $searcher | Add-Member ScriptMethod Search { param($query)
                if ($env:TEST_SEARCH_FAILS) { throw 'Search failed {0}' }
                [pscustomobject]@{ Updates = @(
                    (New-FakeUpdate $env:TEST_CU '2026-10 Cumulative Update (KB5031356)' $false),
                    (New-FakeUpdate $env:TEST_OTHER 'Other update (KB5031357)' $false),
                    (New-FakeUpdate $env:TEST_INTERACTIVE 'Interactive update (KB5031358)' $true)) } }
            $searcher | Add-Member ScriptMethod GetTotalHistoryCount { 1 }
            $searcher | Add-Member ScriptMethod QueryHistory { param($start, $count) @(
                [pscustomobject]@{ Title = 'Earlier (KB5030000)'; UpdateIdentity = [pscustomobject]@{ UpdateID = $env:TEST_OTHER }
                    Operation = 1; ResultCode = 4; HResult = -2145124318; Date = [datetime]'2026-09-11T03:04:05'
                    ClientApplicationID = 'UpdateOrchestrator'; Categories = @() }) }
            $session = [pscustomobject]@{ ClientApplicationID = '' }
            $session | Add-Member ScriptMethod CreateUpdateSearcher { $searcher }.GetNewClosure()
            $session | Add-Member ScriptMethod CreateUpdateDownloader {
                $downloader = [pscustomobject]@{ Updates = $null }
                $downloader | Add-Member ScriptMethod Download { [pscustomobject]@{ ResultCode = 2 } }
                $downloader }
            $session | Add-Member ScriptMethod CreateUpdateInstaller {
                $installer = [pscustomobject]@{ Updates = $null; AllowSourcePrompts = $true }
                $installer | Add-Member ScriptMethod Install {
                    foreach ($item in $this.Updates.Items) { $global:Installed.Add($item.Identity.UpdateID) }
                    $result = [pscustomobject]@{ ResultCode = 2; RebootRequired = $true }
                    $result | Add-Member ScriptMethod GetUpdateResult { param($i) [pscustomobject]@{ ResultCode = 2; HResult = 0 } }
                    $result }
                $installer }
            return $session
        }
        'Microsoft.Update.UpdateColl' {
            $collection = [pscustomobject]@{ Items = (Microsoft.PowerShell.Utility\New-Object System.Collections.ArrayList) }
            $collection | Add-Member ScriptProperty Count { $this.Items.Count }
            $collection | Add-Member ScriptMethod Add { param($u) $this.Items.Add($u) }
            $collection | Add-Member ScriptMethod Item { param($i) $this.Items[$i] }
            return $collection
        }
        'Microsoft.Update.SystemInfo' { return [pscustomobject]@{ RebootRequired = $false } }
        'Microsoft.Update.ServiceManager' { return [pscustomobject]@{ Services = @([pscustomobject]@{ Name = 'Microsoft Update'; IsDefaultAUService = $true }) } }
        default { return Microsoft.PowerShell.Utility\New-Object -TypeName $TypeName -ArgumentList $ArgumentList }
    }
}
function Get-HotFix { @([pscustomobject]@{ HotFixID = 'KB5029999'; Description = 'Security Update'; InstalledOn = [datetime]'2026-08-01'; InstalledBy = 'SYSTEM' }) }
function Test-Path { $false }
function Get-ItemProperty { param($Path, $Name, $ErrorAction) $null }
function shutdown.exe { $global:LASTEXITCODE = 0; ($args -join ' ') | Set-Content $env:TEST_SHUTDOWN_LOG }
'''


def _run_powershell(tmp_path, script: str, env: dict[str, str]):
    harness = tmp_path / "harness.ps1"
    harness.write_text(COM_MOCKS + script, encoding="utf-8")
    result = subprocess.run([POWERSHELL, "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-File", str(harness)],
                            env={**os.environ, **env}, capture_output=True, text=True, timeout=60)
    return result


@pytest.mark.skipif(not POWERSHELL, reason="Requires PowerShell; Windows Update is mocked")
@pytest.mark.parametrize("search_fails", [False, True])
def test_scan_script_reports_status_the_portal_can_read(tmp_path, search_fails):
    script = tmp_path / "scan.ps1"
    script.write_text(updates.scan_script_content(), encoding="utf-8")
    result = _run_powershell(tmp_path, f"& '{script}'\nexit $LASTEXITCODE\n", {
        "TEST_CU": CU, "TEST_OTHER": OLD, "TEST_INTERACTIVE": EDGE, "TEST_SEARCH_FAILS": "1" if search_fails else ""})
    assert result.returncode == (1 if search_fails else 0), result.stdout + result.stderr
    stdout, encoded = updates.take_marker(result.stdout)
    state = updates.normalise(updates.decode(encoded))
    assert state["source"] == "Microsoft Update" and state["hotfixes"][0]["kb"] == "KB5029999"
    assert state["history"][0]["hresult"] == "0x80240022" and state["history"][0]["result"] == "failed"
    if search_fails:
        assert state["missing"] is None and "Search failed {0}" in state["errors"][0] and "Search failed" in stdout
    else:
        assert [item["update_id"] for item in state["missing"]] == [CU, OLD, EDGE]
        assert state["missing"][0]["classification"] == "Security Updates" and state["missing"][2]["is_interactive"]
        assert state["missing"][0]["kb"] == "KB5031356" and state["missing"][0]["size_bytes"] == 1048576


@pytest.mark.skipif(not POWERSHELL, reason="Requires PowerShell; Windows Update is mocked")
@pytest.mark.parametrize("reboot_mode", ["never", "always"])
def test_patch_script_installs_only_the_chosen_updates(tmp_path, reboot_mode):
    script = tmp_path / "patch.ps1"
    script.write_text(rmm_patching.script_content(), encoding="utf-8")
    plan = {"manual_windows": {"update_ids": [CU.upper(), EDGE]}, "windows": None, "apps": [], "policies": [],
            "reboot": {"reboot_mode": reboot_mode, "reboot_delay_minutes": 10, "immediate": True,
                       "active_start": 0, "active_end": 0}}
    log = tmp_path / "shutdown.txt"
    result = _run_powershell(tmp_path, f"& '{script}'\n'INSTALLED=' + ($global:Installed -join ',')\n", {
        "TEST_CU": CU, "TEST_OTHER": OLD, "TEST_INTERACTIVE": EDGE, "TEST_SEARCH_FAILS": "",
        "TEST_SHUTDOWN_LOG": str(log), "MYPORTAL_PATCH_PLAN": json.dumps(plan)})
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"INSTALLED={CU}" in result.stdout
    assert "Skipped, needs someone at the device: Interactive update" in result.stdout
    assert "Reboot pending: True" in result.stdout
    _, encoded = updates.take_marker(result.stdout)
    assert encoded and updates.normalise(updates.decode(encoded))["missing"]
    if reboot_mode == "never":
        assert not log.exists()
    else:
        # Active hours are not checked for a technician's install.
        assert log.read_text().startswith("/r /t 600") and "Restart scheduled in 10 minutes." in result.stdout


@pytest.mark.skipif(not POWERSHELL, reason="Requires PowerShell; Windows Update is mocked")
def test_patch_policy_still_installs_by_category_and_reports_status(tmp_path):
    script = tmp_path / "patch.ps1"
    script.write_text(rmm_patching.script_content(), encoding="utf-8")
    plan = {"windows": {"categories": ["Security Updates"], "deferral_days": 0}, "apps": [], "policies": [],
            "reboot": {"reboot_mode": "never"}}
    result = _run_powershell(tmp_path, f"& '{script}'\n'INSTALLED=' + ($global:Installed -join ',')\n", {
        "TEST_CU": CU, "TEST_OTHER": OLD, "TEST_INTERACTIVE": EDGE, "TEST_SEARCH_FAILS": "",
        "TEST_SHUTDOWN_LOG": str(tmp_path / "shutdown.txt"), "MYPORTAL_PATCH_PLAN": json.dumps(plan)})
    assert result.returncode == 0, result.stdout + result.stderr
    # Interactive updates are never installed by a policy.
    assert f"INSTALLED={CU},{OLD}" in result.stdout and "Reboot pending: True" in result.stdout
    assert updates.take_marker(result.stdout)[1]
