"""RMM scripting: parameter/env detection, Gitea sync, runs, results and pages."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import aiosqlite
import pytest
from jinja2 import ChoiceLoader, DictLoader, Environment, FileSystemLoader

from app.repositories import asset_custom_fields as asset_fields_repo
from app.repositories import assets as assets_repo
from app.repositories import company_variables as company_variables_repo
from app.repositories import rmm as rmm_repo
from app.repositories import rmm_automation as automation_repo
from app.repositories import tags as tags_repo
from app.security.encryption import decrypt_secret
from app.security.menu_permissions import normalize_menu_permissions
from app.services import gitea
from app.services import rmm_script_parser as parser
from app.services import rmm_scripts

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def anyio_backend():
    return "asyncio"


# ---------------------------------------------------------------------------
# Parameter and environment variable detection
# ---------------------------------------------------------------------------

POWERSHELL = r'''<#
.SYNOPSIS
  Checks free disk space.
.PARAMETER Drive
  Drive letter to check.
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$Drive,
    # Warn below this many GB
    [int]$MinFreeGB = 10,
    [ValidateSet('Low', 'High')] [string]$Level = 'Low',
    [switch]$Force,
    [Parameter(Mandatory=$false)][securestring]$AdminPassword,
    [string[]]$Paths = @('C:\Temp', 'D:\Logs'),
    [bool]$Notify = $true  # Send an alert
)
$env:SCRATCH = "set by the script"
Write-Output $env:TENANT_ID $env:Path ${env:API_TOKEN} $env:SCRATCH $env:MYPORTAL_RESULT_FILE
[Environment]::GetEnvironmentVariable('SITE_CODE')
# $env:COMMENTED_OUT is ignored
'''

BASH = r'''#!/bin/bash
# Restarts a service and reports its status.
#
# param(
#   [Parameter(Mandatory)] [string] $Service,  # Service to restart
#   [int] $Wait = 5,
#   [ValidateSet("start","restart")] $Action = "restart"
# )
set -euo pipefail
NAME="local"
for ITEM in a b; do echo "$ITEM"; done
read -r ANSWER
echo "$NAME ${SITE_URL:-https://example.com} $API_KEY $HOME $ANSWER"
echo '$IN_SINGLE_QUOTES'
echo "${Service}"
'''


def test_powershell_param_block_is_parsed():
    parsed = parser.parse_script(POWERSHELL, "powershell")
    assert parsed.description == "Checks free disk space."
    params = {item.name: item for item in parsed.parameters}
    assert list(params) == ["Drive", "MinFreeGB", "Level", "Force", "AdminPassword", "Paths", "Notify"]
    assert params["Drive"].mandatory and params["Drive"].type == "string"
    assert params["Drive"].help == "Drive letter to check."
    assert (params["MinFreeGB"].type, params["MinFreeGB"].default, params["MinFreeGB"].help) == (
        "integer", "10", "Warn below this many GB")
    assert params["Level"].type == "choice" and params["Level"].choices == ["Low", "High"]
    assert params["Force"].type == "switch" and not params["Force"].mandatory
    assert params["AdminPassword"].type == "secret" and params["AdminPassword"].sensitive
    assert not params["AdminPassword"].mandatory
    assert params["Paths"].type == "list" and params["Paths"].default == "C:\\Temp, D:\\Logs"
    assert params["Notify"].type == "boolean" and params["Notify"].default == "true"
    assert params["Notify"].help == "Send an alert"


def test_powershell_env_vars_exclude_assigned_system_reserved_and_comments():
    parsed = parser.parse_script(POWERSHELL, "powershell")
    env = {item.name: item for item in parsed.env_vars}
    assert list(env) == ["TENANT_ID", "API_TOKEN", "SITE_CODE"]
    assert env["API_TOKEN"].sensitive and not env["TENANT_ID"].sensitive


def test_powershell_without_param_block_has_no_parameters():
    parsed = parser.parse_script("Write-Output 'hi'\nfunction F { param($x) }\n", "powershell")
    assert parsed.parameters == []


def test_shell_comment_param_block_and_env_vars():
    parsed = parser.parse_script(BASH, "bash")
    assert parsed.description == "Restarts a service and reports its status."
    params = {item.name: item for item in parsed.parameters}
    assert list(params) == ["Service", "Wait", "Action"]
    assert params["Service"].mandatory and params["Service"].help == "Service to restart"
    assert params["Wait"].type == "integer" and params["Wait"].default == "5"
    assert params["Action"].choices == ["start", "restart"]
    env = {item.name: item for item in parsed.env_vars}
    assert list(env) == ["SITE_URL", "API_KEY"]
    assert env["SITE_URL"].default == "https://example.com"
    assert env["API_KEY"].sensitive


def test_zsh_without_header_still_finds_env_vars():
    parsed = parser.parse_script("echo $CUSTOMER_ID\nexport CUSTOMER_ID2=1\necho $CUSTOMER_ID2\n", "zsh")
    assert parsed.parameters == []
    assert [item.name for item in parsed.env_vars] == ["CUSTOMER_ID"]


@pytest.mark.parametrize(
    ("path", "language"),
    [("a/b.ps1", "powershell"), ("x.SH", "bash"), ("y.zsh", "zsh"), ("z.bash", "bash"), ("readme.md", None)],
)
def test_language_for_path(path, language):
    assert parser.language_for_path(path) == language


def test_unbalanced_param_block_does_not_raise():
    parsed = parser.parse_script("param(\n [string]$A = 'x'\n", "powershell")
    assert parsed.parameters == []


# ---------------------------------------------------------------------------
# Values techs enter
# ---------------------------------------------------------------------------

SCRIPT = {
    "id": 1,
    "parameters": [
        {"name": "Drive", "type": "string", "mandatory": True, "default": None, "choices": [], "help": "", "sensitive": False},
        {"name": "MinFreeGB", "type": "integer", "mandatory": False, "default": "10", "choices": [], "help": "", "sensitive": False},
        {"name": "Level", "type": "choice", "mandatory": False, "default": None, "choices": ["Low", "High"], "help": "", "sensitive": False},
        {"name": "Force", "type": "switch", "mandatory": False, "default": None, "choices": [], "help": "", "sensitive": False},
        {"name": "Paths", "type": "list", "mandatory": False, "default": None, "choices": [], "help": "", "sensitive": False},
        {"name": "Password", "type": "secret", "mandatory": False, "default": None, "choices": [], "help": "", "sensitive": True},
    ],
    "env_vars": [{"name": "TENANT_ID", "default": None, "help": "", "sensitive": False}],
}


def test_required_fields_are_enforced():
    with pytest.raises(rmm_scripts.RunRequestError) as exc:
        rmm_scripts.validate_entries(SCRIPT, {"param:MinFreeGB": "5"})
    assert set(exc.value.errors) == {"param:Drive"}


@pytest.mark.anyio
async def test_values_are_resolved_and_typed():
    entries = rmm_scripts.validate_entries(SCRIPT, {
        "param:Drive": "{{asset.custom.SystemDrive}}",
        "param:MinFreeGB": " 20 ",
        "param:Level": "high",
        "param:Force": "true",
        "param:Paths": "C:\\a\nC:\\b, C:\\c",
        "env:TENANT_ID": "{{company.variables.Tenant}}",
    })
    context = {"company": {"variables": {"Tenant": "contoso"}}, "asset": {"custom": {"SystemDrive": "C"}}}
    payload = await rmm_scripts.resolve_entries(SCRIPT, entries, context)
    assert payload["parameters"] == {
        "Drive": "C", "MinFreeGB": 20, "Level": "High", "Force": True, "Paths": ["C:\\a", "C:\\b", "C:\\c"],
    }
    assert payload["parameter_order"] == ["Drive", "MinFreeGB", "Level", "Force", "Paths"]
    assert payload["env"] == {"TENANT_ID": "contoso"}


@pytest.mark.anyio
async def test_bad_values_and_empty_variables_are_reported():
    entries = rmm_scripts.validate_entries(SCRIPT, {
        "param:Drive": "{{asset.custom.Missing}}", "param:MinFreeGB": "ten", "param:Level": "Medium",
    })
    with pytest.raises(rmm_scripts.RunRequestError) as exc:
        await rmm_scripts.resolve_entries(SCRIPT, entries, {"asset": {"custom": {}}})
    assert set(exc.value.errors) == {"param:Drive", "param:MinFreeGB", "param:Level"}


def test_sensitive_literals_are_masked_but_variables_are_kept():
    fields = {field["key"]: field for field in rmm_scripts.script_fields(SCRIPT)}
    assert rmm_scripts._stored_input(fields["param:Password"], "hunter2")["value"] == rmm_scripts.SENSITIVE_MASK
    stored = rmm_scripts._stored_input(fields["param:Password"], "{{company.variables.AdminPw}}")
    assert stored["value"] == "{{company.variables.AdminPw}}" and stored["is_variable"]


def test_custom_values_accept_both_shapes_and_are_bounded():
    assert rmm_scripts.normalise_custom_values({"asset": {"A": True}, "company": {"B": 2}}) == [
        {"scope": "asset", "name": "A", "value": "true"}, {"scope": "company", "name": "B", "value": "2"},
    ]
    many = [{"scope": "asset", "name": f"F{i}", "value": "x"} for i in range(500)]
    assert len(rmm_scripts.normalise_custom_values(many)) == rmm_scripts.MAX_CUSTOM_VALUES
    assert rmm_scripts.normalise_custom_values([{"scope": "ticket", "name": "x"}, {"scope": "asset", "name": ""}]) == []


# ---------------------------------------------------------------------------
# End to end against the real migrations (SQLite)
# ---------------------------------------------------------------------------


class _SqliteDb:
    def __init__(self, conn):
        self.conn = conn

    @staticmethod
    def _sql(sql):
        return sql.replace("%s", "?")

    async def fetch_one(self, sql, params=None):
        cursor = await self.conn.execute(self._sql(sql), params or ())
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def fetch_all(self, sql, params=None):
        cursor = await self.conn.execute(self._sql(sql), params or ())
        return [dict(row) for row in await cursor.fetchall()]

    async def execute(self, sql, params=None):
        await self.conn.execute(self._sql(sql), params or ())

    async def execute_rowcount(self, sql, params=None):
        return (await self.conn.execute(self._sql(sql), params or ())).rowcount

    async def execute_returning_lastrowid(self, sql, params=None):
        return (await self.conn.execute(self._sql(sql), params or ())).lastrowid

    @staticmethod
    def is_sqlite():
        return True


@pytest.fixture
async def sqlite_db(monkeypatch):
    from app.core.database import Database

    conn = await aiosqlite.connect(":memory:")
    conn.row_factory = aiosqlite.Row
    await conn.executescript("""
        CREATE TABLE companies (id INTEGER PRIMARY KEY, name TEXT, archived INT DEFAULT 0);
        CREATE TABLE users (id INTEGER PRIMARY KEY, email TEXT);
        CREATE TABLE assets (id INTEGER PRIMARY KEY, company_id INT, name TEXT, serial_number TEXT, archived_at TEXT);
        INSERT INTO companies (id, name) VALUES (1, 'Contoso'), (2, 'Other');
        INSERT INTO users VALUES (9, 'tech@example.com');
        INSERT INTO assets (id, company_id, name, serial_number) VALUES (10, 1, 'PC-01', 'SN1'), (11, 1, 'PC-02', 'SN2'), (20, 2, 'OTHER-PC', 'SN3');
        -- Mirrors migrations 099/160, whose MySQL-only syntax SQLite cannot read.
        CREATE TABLE asset_custom_field_definitions (
          id INTEGER PRIMARY KEY, name TEXT UNIQUE, display_name TEXT, field_type TEXT,
          display_order INT DEFAULT 0, created_at TEXT, updated_at TEXT);
        CREATE TABLE asset_custom_field_values (
          id INTEGER PRIMARY KEY, asset_id INT, field_definition_id INT, value_text TEXT, value_date TEXT,
          value_boolean INT, created_at TEXT, updated_at TEXT, UNIQUE (asset_id, field_definition_id));
    """)
    adapter = Database()
    for name in ("332_company_variables.sql", "465_rmm_scripting.sql", "466_rmm_script_company.sql",
                 "466_asset_company_tags.sql", "467_rmm_automation.sql"):
        await conn.executescript(adapter._adapt_sql_for_sqlite((ROOT / "migrations" / name).read_text()))
    await conn.executescript("""
        INSERT INTO asset_custom_field_definitions (id, name, field_type) VALUES
          (1, 'BitLocker', 'text'), (2, 'Encrypted', 'checkbox'), (3, 'Checked', 'date'), (4, 'Photo', 'image');
        INSERT INTO company_variable_definitions (id, name) VALUES (1, 'Tenant');
        INSERT INTO company_variable_values (company_id, variable_id, value) VALUES (1, 1, 'contoso');
    """)
    fake = _SqliteDb(conn)
    for module in (rmm_repo, automation_repo, tags_repo, asset_fields_repo, company_variables_repo, assets_repo):
        monkeypatch.setattr(module, "db", fake)

    async def get_company(company_id):
        return await fake.fetch_one("SELECT * FROM companies WHERE id = %s", (company_id,))

    async def list_companies(include_archived=False):
        rows = await fake.fetch_all("SELECT * FROM companies ORDER BY id")
        return [dict(row) for row in rows]

    monkeypatch.setattr(rmm_scripts.companies_repo, "get_company_by_id", get_company)
    monkeypatch.setattr(rmm_scripts.companies_repo, "list_companies", list_companies)
    yield fake
    await conn.close()


async def _sync(monkeypatch, files: dict[str, str], shas: dict[str, str] | None = None, *, created=None, refuse=False):
    settings = gitea.GiteaSettings("https://git.example.com", "https://git.example.com", "t", "msp", "scripts", "main", "rmm", True)

    async def load_settings():
        return settings

    async def list_files(_settings):
        return [gitea.GiteaFile(path=path, sha=(shas or {}).get(path, "sha-" + path), size=len(body))
                for path, body in files.items()]

    fetched = []

    async def fetch_file(_settings, path):
        fetched.append(path)
        return files[path].encode("utf-8")

    monkeypatch.setattr(gitea, "load_settings", load_settings)
    monkeypatch.setattr(gitea, "list_files", list_files)
    async def create_files(_settings, new_files, message):
        if refuse:
            raise gitea.GiteaError("Gitea refused to create folders.")
        if created is not None:
            created.update(new_files)

    monkeypatch.setattr(gitea, "fetch_file", fetch_file)
    monkeypatch.setattr(gitea, "create_files", create_files)
    summary = await rmm_scripts.sync_from_gitea()
    return summary, fetched


async def _enrol(fake, *, tray_id=5, asset_id=10, uid="agent-uid-0001", os_name="windows"):
    tray_device = {"id": tray_id, "company_id": 1, "asset_id": asset_id}
    result = await rmm_scripts.enrol_agent(
        tray_device=tray_device,
        payload={"agent_uid": uid, "hostname": "PC", "os": os_name, "shells": ["powershell", "bash"]},
        client_ip="203.0.113.5",
    )
    return result, await rmm_scripts.authenticate_agent(result["auth_token"])


@pytest.mark.anyio
async def test_sync_adds_updates_skips_and_removes(sqlite_db, monkeypatch):
    files = {"rmm/Common/disk/Check-Disk.ps1": POWERSHELL, "rmm/Common/linux/restart.sh": BASH, "rmm/README.md": "# docs"}
    summary, fetched = await _sync(monkeypatch, files)
    assert (summary.added, summary.updated, summary.unchanged) == (2, 0, 0)
    scripts = {script["name"]: script for script in await rmm_repo.list_scripts()}
    assert set(scripts) == {"Check-Disk", "restart"}
    assert scripts["Check-Disk"]["folder"] == "Common/disk" and scripts["Check-Disk"]["language"] == "powershell"
    assert len(scripts["Check-Disk"]["parameters"]) == 7
    assert [env["name"] for env in scripts["restart"]["env_vars"]] == ["SITE_URL", "API_KEY"]
    assert "rmm/README.md" not in fetched

    summary, fetched = await _sync(
        monkeypatch, {"rmm/Common/disk/Check-Disk.ps1": "param([string]$Only)\n"}, {"rmm/Common/disk/Check-Disk.ps1": "sha-new"}
    )
    assert (summary.added, summary.updated, summary.removed) == (0, 1, 1)
    assert [script["name"] for script in await rmm_repo.list_scripts()] == ["Check-Disk"]
    assert [s["name"] for s in (await rmm_repo.list_scripts())[0]["parameters"]] == ["Only"]

    summary, fetched = await _sync(monkeypatch, {"rmm/Common/disk/Check-Disk.ps1": "x"}, {"rmm/Common/disk/Check-Disk.ps1": "sha-new"})
    assert summary.unchanged == 1 and fetched == []


@pytest.mark.anyio
async def test_enrolment_links_tray_device_and_rejects_takeover(sqlite_db):
    result, agent = await _enrol(sqlite_db)
    assert agent["company_id"] == 1 and agent["asset_id"] == 10 and agent["tray_device_id"] == 5
    assert agent["auth_token_hash"] != result["auth_token"]
    again, agent_again = await _enrol(sqlite_db)
    assert agent_again["id"] == agent["id"]
    assert await rmm_scripts.authenticate_agent(result["auth_token"]) is None
    with pytest.raises(PermissionError):
        await _enrol(sqlite_db, tray_id=6)
    with pytest.raises(PermissionError):
        await rmm_scripts.enrol_agent(tray_device={"id": 7, "company_id": None}, payload={"agent_uid": "x" * 10}, client_ip=None)
    with pytest.raises(ValueError):
        await rmm_scripts.enrol_agent(tray_device={"id": 7, "company_id": 1}, payload={"agent_uid": "bad uid!"}, client_ip=None)


@pytest.mark.anyio
async def test_run_round_trip_updates_custom_fields_and_variables(sqlite_db, monkeypatch):
    await _sync(monkeypatch, {"rmm/Common/Check-Disk.ps1": POWERSHELL})
    script = (await rmm_repo.list_scripts())[0]
    _result, agent = await _enrol(sqlite_db)
    await asset_fields_repo.set_asset_field_value(10, 1, value_text="Off")

    queued = await rmm_scripts.queue_runs(
        script_id=script["id"], company_id=1, asset_ids=[10, 11, 20],
        submitted={"param:Drive": "{{asset.custom.BitLocker}}", "param:AdminPassword": "s3cret",
                   "env:TENANT_ID": "{{company.variables.Tenant}}", "env:SITE_CODE": "{{asset.serial_number}}"},
        timeout_seconds=5, requested_by_user_id=9,
    )
    assert [item["asset_id"] for item in queued["queued"]] == [10]
    problems = {item["asset_id"]: item["message"] for item in queued["problems"]}
    assert problems == {11: "No RMM agent is installed.", 20: "Device not found."}
    run_id = queued["queued"][0]["run_id"]

    stored = await sqlite_db.fetch_one("SELECT * FROM rmm_script_runs WHERE id = %s", (run_id,))
    assert "s3cret" not in stored["inputs_json"] and "s3cret" not in stored["payload_encrypted"]
    assert stored["timeout_seconds"] == rmm_scripts.MIN_TIMEOUT_SECONDS
    secret_payload = json.loads(decrypt_secret(stored["payload_encrypted"], allow_plaintext=False))
    assert secret_payload["parameters"]["AdminPassword"] == "s3cret"

    jobs = await rmm_scripts.wait_for_jobs(agent, wait_seconds=0)
    assert len(jobs) == 1
    job = jobs[0]
    assert job["parameters"] == {"Drive": "Off", "AdminPassword": "s3cret"}
    assert job["env"] == {"TENANT_ID": "contoso", "SITE_CODE": "SN1"}
    assert job["sha256"] == script["content_sha256"] and job["script"] == POWERSHELL
    assert await rmm_scripts.wait_for_jobs(agent, wait_seconds=0) == []
    assert not await rmm_repo.cancel_run(run_id)

    assert await rmm_scripts.mark_started(agent, run_id)
    outcome = await rmm_scripts.record_result(agent, run_id, {
        "exit_code": 0, "stdout": "done", "stderr": "",
        "custom_values": [
            {"scope": "asset", "name": "bitlocker", "value": "On"},
            {"scope": "asset", "name": "Encrypted", "value": "yes"},
            {"scope": "asset", "name": "Checked", "value": "2026-10-01T10:00:00Z"},
            {"scope": "asset", "name": "Photo", "value": "x"},
            {"scope": "asset", "name": "Unknown", "value": "x"},
            {"scope": "company", "name": "TENANT", "value": "fabrikam"},
        ],
    })
    assert outcome["status"] == "completed"
    applied = {item["name"]: item["applied"] for item in outcome["custom_values"]}
    assert applied == {"bitlocker": True, "Encrypted": True, "Checked": True, "Photo": False, "Unknown": False, "TENANT": True}

    values = {row["field_name"]: row for row in await asset_fields_repo.get_asset_field_values(10)}
    assert values["BitLocker"]["value_text"] == "On"
    assert bool(values["Encrypted"]["value_boolean"]) is True
    assert str(values["Checked"]["value_date"]) == "2026-10-01"
    assert (await company_variables_repo.value_map(1))["Tenant"] == "fabrikam"

    run = await rmm_repo.get_run(run_id)
    assert run["status"] == "completed" and run["exit_code"] == 0 and run["stdout"] == "done"
    assert run["requested_by_email"] == "tech@example.com" and run["asset_name"] == "PC-01"
    assert (await sqlite_db.fetch_one("SELECT payload_encrypted FROM rmm_script_runs WHERE id = %s", (run_id,)))["payload_encrypted"] is None
    # A late duplicate report does not overwrite the result.
    assert (await rmm_scripts.record_result(agent, run_id, {"exit_code": 1}))["status"] == "completed"


@pytest.mark.anyio
async def test_failures_timeouts_and_other_agents(sqlite_db, monkeypatch):
    await _sync(monkeypatch, {"rmm/Common/a.sh": "echo hi\n"})
    script = (await rmm_repo.list_scripts())[0]
    _r, windows_agent = await _enrol(sqlite_db)
    queued = await rmm_scripts.queue_runs(script_id=script["id"], company_id=1, asset_ids=[10], submitted={},
                                          timeout_seconds=None, requested_by_user_id=None)
    assert queued["queued"] == [] and "Windows" in queued["problems"][0]["message"]

    _r, mac_agent = await _enrol(sqlite_db, tray_id=8, asset_id=11, uid="agent-uid-0002", os_name="darwin")
    first = await rmm_scripts.queue_runs(script_id=script["id"], company_id=1, asset_ids=[11], submitted={},
                                         timeout_seconds=99999, requested_by_user_id=None)
    run_id = first["queued"][0]["run_id"]
    assert (await rmm_repo.get_run(run_id))["timeout_seconds"] == rmm_scripts.MAX_TIMEOUT_SECONDS
    assert await rmm_scripts.record_result(windows_agent, run_id, {"exit_code": 0}) is None
    assert await rmm_scripts.wait_for_jobs(windows_agent, wait_seconds=0) == []
    await rmm_scripts.wait_for_jobs(mac_agent, wait_seconds=0)
    assert (await rmm_scripts.record_result(mac_agent, run_id, {"exit_code": 2}))["status"] == "failed"

    second = await rmm_scripts.queue_runs(script_id=script["id"], company_id=1, asset_ids=[11], submitted={},
                                          timeout_seconds=60, requested_by_user_id=None)
    timed = second["queued"][0]["run_id"]
    assert (await rmm_scripts.record_result(mac_agent, timed, {"timed_out": True}))["status"] == "timed_out"

    third = await rmm_scripts.queue_runs(script_id=script["id"], company_id=1, asset_ids=[11], submitted={},
                                         timeout_seconds=60, requested_by_user_id=None)
    cancelled = third["queued"][0]["run_id"]
    assert await rmm_repo.cancel_run(cancelled)
    assert await rmm_scripts.wait_for_jobs(mac_agent, wait_seconds=0) == []

    fourth = await rmm_scripts.queue_runs(script_id=script["id"], company_id=1, asset_ids=[11], submitted={},
                                          timeout_seconds=60, requested_by_user_id=None)
    stale = fourth["queued"][0]["run_id"]
    await sqlite_db.execute("UPDATE rmm_script_runs SET expires_at = '2000-01-01 00:00:00' WHERE id = %s", (stale,))
    assert await rmm_repo.expire_stale_runs() == 1
    assert (await rmm_repo.get_run(stale))["status"] == "expired"
    statuses = [run["status"] for run in await rmm_repo.list_runs(company_id=1)]
    assert sorted(statuses) == ["cancelled", "expired", "failed", "timed_out"]
    assert await rmm_repo.list_runs(company_id=2) == []


@pytest.mark.anyio
async def test_long_poll_wakes_when_a_run_is_queued(sqlite_db, monkeypatch):
    import asyncio

    await _sync(monkeypatch, {"rmm/Common/a.ps1": "Write-Output 1\n"})
    script = (await rmm_repo.list_scripts())[0]
    _r, agent = await _enrol(sqlite_db)
    waiter = asyncio.create_task(rmm_scripts.wait_for_jobs(agent, wait_seconds=10))
    await asyncio.sleep(0.05)
    await rmm_scripts.queue_runs(script_id=script["id"], company_id=1, asset_ids=[10], submitted={},
                                 timeout_seconds=60, requested_by_user_id=None)
    jobs = await asyncio.wait_for(waiter, timeout=2)
    assert len(jobs) == 1


@pytest.mark.anyio
async def test_available_variables_list_asset_fields_and_company_variables(sqlite_db):
    tokens = [item["token"] for item in await rmm_scripts.available_variables()]
    assert "{{asset.custom.BitLocker}}" in tokens and "{{company.variables.Tenant}}" in tokens
    assert "{{asset.custom.Photo}}" not in tokens


# ---------------------------------------------------------------------------
# Gitea settings and client
# ---------------------------------------------------------------------------


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("settings", "message"),
    [
        ({"base_url": "", "repository": "a/b"}, "GITEA_BASE_URL"),
        ({"base_url": "ftp://git", "repository": "a/b"}, "https://"),
        ({"base_url": "https://git", "repository": "nope"}, "owner/name"),
        ({"base_url": "https://git", "public_url": "//evil.example", "repository": "a/b"}, "GITEA_PUBLIC_URL"),
        ({"base_url": "https://git", "public_url": "javascript:x", "repository": "a/b"}, "GITEA_PUBLIC_URL"),
    ],
)
async def test_gitea_settings_are_validated(monkeypatch, settings, message):
    async def get_module(slug, redact=True):
        return {"enabled": True, "settings": settings}

    monkeypatch.setattr(gitea.modules_service, "get_module", get_module)
    with pytest.raises(gitea.GiteaError, match=message):
        await gitea.load_settings()


@pytest.mark.anyio
async def test_gitea_lists_only_the_configured_folder(monkeypatch):
    import httpx

    def handler(request):
        assert request.headers["Authorization"] == "token secret"
        assert request.url.path == "/api/v1/repos/msp/scripts/git/trees/main"
        return httpx.Response(200, json={"tree": [
            {"path": "rmm/a.ps1", "type": "blob", "sha": "1", "size": 3},
            {"path": "rmm", "type": "tree", "sha": "2"},
            {"path": "rmm-other/b.sh", "type": "blob", "sha": "3", "size": 3},
        ], "truncated": False})

    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(gitea, "_client", lambda s: httpx.AsyncClient(transport=transport, headers=gitea._headers(s)))
    settings = gitea.GiteaSettings("https://git.example.com", "https://git.example.com", "secret", "msp", "scripts", "main", "rmm", True)
    files = await gitea.list_files(settings)
    assert [item.path for item in files] == ["rmm/a.ps1"]
    assert gitea.web_url(settings, "rmm/a b.ps1").endswith("/msp/scripts/src/branch/main/rmm/a%20b.ps1")


@pytest.mark.anyio
async def test_gitea_links_use_the_public_address(monkeypatch):
    async def get_module(slug, redact=True):
        return {"enabled": True, "settings": {
            "base_url": "http://gitea:3000", "public_url": "/gitea/", "repository": "myportal/rmm-scripts",
        }}

    monkeypatch.setattr(gitea.modules_service, "get_module", get_module)
    settings = await gitea.load_settings()
    assert settings.base_url == "http://gitea:3000"
    assert gitea.web_url(settings, "a.ps1") == "/gitea/myportal/rmm-scripts/src/branch/main/a.ps1"


def test_gitea_module_is_on_by_default_and_reads_public_url(monkeypatch):
    from app.services import modules

    monkeypatch.setenv("GITEA_PUBLIC_URL", "/gitea/")
    assert next(m for m in modules.DEFAULT_MODULES if m["slug"] == "gitea")["enabled"] is True
    assert modules._coerce_settings("gitea", {}, None)["public_url"] == "/gitea"


def test_gitea_module_redacts_token_and_reads_env(monkeypatch):
    from app.services import modules

    monkeypatch.setenv("GITEA_SCRIPTS_REPOSITORY", "msp/rmm-scripts/")
    monkeypatch.setenv("GITEA_API_TOKEN", "tok")
    settings = modules._coerce_settings("gitea", {}, None)
    assert settings["repository"] == "msp/rmm-scripts" and settings["api_token"] == "tok"
    redacted = modules._redact_module_settings({"slug": "gitea", "settings": settings})
    assert redacted["settings"].get("api_token") != "tok"


# ---------------------------------------------------------------------------
# Permissions, menu and templates
# ---------------------------------------------------------------------------


def test_permission_defaults_to_no_access():
    assert normalize_menu_permissions(None)["menu.rmm_scripts"] == "none"
    assert normalize_menu_permissions({"menu.rmm_scripts": "write"})["menu.rmm_scripts"] == "write"


def test_menu_links_to_scripts():
    base = (ROOT / "app/templates/base.html").read_text()
    assert 'href="/rmm/scripts"' in base and "menu.rmm_scripts" in base and "feature_pack_available('rmm')" in base


def test_rmm_is_its_own_feature_pack():
    from app.core.features import discover_builtin_feature_pack_slugs
    from app.features.rmm import PACK

    assert PACK.slug == "rmm"
    assert "rmm" in discover_builtin_feature_pack_slugs()


def _environment():
    return Environment(
        loader=ChoiceLoader([
            DictLoader({
                "base.html": "{% block header_actions %}{% endblock %}{% block content %}{% endblock %}{% block scripts %}{% endblock %}",
                "macros/header.html": "{% macro page_header_actions(actions, menu_id='') %}{% for a in actions %}[{{ a.label }}]{% endfor %}{% endmacro %}",
            }),
            FileSystemLoader(ROOT / "app" / "templates"),
        ]),
        autoescape=True,
    )


def test_scripts_page_renders_library_runs_and_escapes():
    env = _environment()
    env.globals["static_url"] = lambda path: path
    html = env.get_template("rmm/scripts.html").render(
        script_groups=[{"folder": "disk", "scripts": [{
            "id": 1, "name": "<b>Check</b>", "path": "rmm/disk/Check.ps1", "language": "powershell",
            "language_label": "PowerShell", "description": "Checks", "parameter_count": 2, "env_count": 1,
        }]}],
        runs=[{"id": 4, "script_name": "Check", "asset_id": 10, "asset_name": "PC-01", "status": "failed",
               "exit_code": 2, "requested_by_email": "t@example.com", "queued_at": "2026-10-10T00:00:00"}],
        agents=[], summary={"scripts": 1, "agents": 0, "running": 0, "failed": 1},
        can_run=True, can_sync=True, gitea_ready=True, gitea_message="", company={"name": "Contoso"},
    )
    assert "&lt;b&gt;Check&lt;/b&gt;" in html and "<b>Check</b>" not in html
    assert "[Run a script][Sync from Gitea]" in html
    assert 'class="status status--error"' in html and "Failed" in html
    assert 'id="rmm-run-modal"' in html and 'data-rmm-view-run="4"' in html
    assert '"can_run": true' in html


def test_scripts_page_empty_state_for_read_only_users():
    env = _environment()
    env.globals["static_url"] = lambda path: path
    html = env.get_template("rmm/scripts.html").render(
        script_groups=[], runs=[], agents=[], summary={"scripts": 0, "agents": 0, "running": 0, "failed": 0},
        can_run=False, can_sync=False, gitea_ready=False, gitea_message="x", company=None,
    )
    assert "No scripts yet" in html and "Ask a super admin" in html
    assert "[Run a script]" not in html and "Sync from Gitea]" not in html


@pytest.mark.anyio
async def test_asset_card_context_hides_agent_secrets(sqlite_db):
    from app.features.rmm import routes

    await _enrol(sqlite_db)
    context = await routes.asset_rmm_context(1, 10, can_run=True)
    assert context["can_run"] and context["agent"]["hostname"] == "PC"
    assert "auth_token_hash" not in context["agent"]
    assert (await routes.asset_rmm_context(1, 11, can_run=True))["can_run"] is False


@pytest.mark.anyio
async def test_variable_names_with_spaces_resolve_case_insensitively():
    context = {"company": {"name": "Contoso", "variables": {"Tenant ID": "contoso"}},
               "asset": {"name": "PC-01", "custom": {"BitLocker Status": "On"}}}
    assert await rmm_scripts.render_value("{{company.variables.tenant id}}/{{ asset.custom.BitLocker Status }}", context) == "contoso/On"
    assert await rmm_scripts.render_value("{{asset.name}} at {{company.name}}", context) == "PC-01 at Contoso"
    assert await rmm_scripts.render_value("{{asset.custom.Missing}}", context) == ""


def test_shell_read_targets_and_no_catastrophic_backtracking():
    import time

    assert parser._read_targets(" -r -p 'Name: ' -t 5 FIRST LAST") == ["FIRST", "LAST"]
    assert parser._read_targets(" -a ITEMS") == ["ITEMS"]
    assert parser._read_targets(" -r 'unbalanced") == []
    script = 'read -r ANSWER\necho "$ANSWER $SITE_URL"\n'
    assert [env.name for env in parser.parse_script(script, "bash").env_vars] == ["SITE_URL"]
    started = time.monotonic()
    parser.parse_script("read " + "A " * 5000 + "-\n" + "read -" * 5000, "bash")
    assert time.monotonic() - started < 2


def _companies(*rows):
    return [{"id": company_id, "name": name, "archived": archived} for company_id, name, archived in rows]


def _files(*paths):
    return [gitea.GiteaFile(path=path, sha="s", size=1) for path in paths]


def test_company_folder_names_are_safe_and_unique():
    names = rmm_scripts.company_folder_names(_companies(
        (3, "Contoso", 0), (1, "A/B: Ltd.", 0), (2, "contoso", 0), (4, "", 0), (5, "...", 0)))
    assert names == {1: "A-B- Ltd", 2: "contoso", 3: "Contoso (3)", 4: "Company 4", 5: "Company 5"}


@pytest.mark.anyio
async def test_layout_links_markers_and_names_and_lists_missing_folders(monkeypatch):
    settings = gitea.GiteaSettings("https://g", "https://g", "t", "msp", "scripts", "main", "rmm", True)
    markers = {"rmm/Companies/Renamed/.myportal-company": b'{"company_id": 1}',
               "rmm/Companies/Broken/.myportal-company": b"not json"}

    async def fetch_file(_settings, path):
        return markers[path]

    monkeypatch.setattr(gitea, "fetch_file", fetch_file)
    files = _files(*markers, "rmm/Companies/fabrikam/x.ps1", "rmm/Companies/Broken/y.ps1", "rmm/Common/a.ps1", "other/z.ps1")
    companies = _companies((1, "Contoso", 0), (2, "Fabrikam", 0), (3, "Northwind", 0), (4, "Gone", 1))
    layout = await rmm_scripts.read_layout(settings, files, companies)
    assert layout.company_folders == {"Renamed": 1, "fabrikam": 2, "Northwind": 3}
    assert set(layout.missing) == {"Companies/fabrikam/.myportal-company", "Companies/Northwind/.myportal-company"}
    assert json.loads(layout.missing["Companies/Northwind/.myportal-company"])["company_id"] == 3

    assert rmm_scripts.script_scope("Common/disk/a.ps1", layout) == (True, None, "")
    assert rmm_scripts.script_scope("Companies/Renamed/sub/b.ps1", layout) == (True, 1, "")
    assert rmm_scripts.script_scope("Companies/Broken/y.ps1", layout)[:2] == (False, None)
    assert rmm_scripts.script_scope("Companies/c.ps1", layout)[0] is False
    assert rmm_scripts.script_scope("loose.ps1", layout)[0] is False

    empty = await rmm_scripts.read_layout(settings, [], companies)
    assert set(empty.missing) == {"Common/README.md", "Companies/README.md", "Companies/Contoso/.myportal-company",
                                  "Companies/Fabrikam/.myportal-company", "Companies/Northwind/.myportal-company"}


@pytest.mark.anyio
async def test_company_scripts_run_only_at_their_company(sqlite_db, monkeypatch):
    created: dict[str, str] = {}
    files = {"rmm/Common/a.ps1": "Write-Output 1\n", "rmm/Companies/Contoso/only.ps1": "Write-Output 2\n",
             "rmm/stray.ps1": "Write-Output 3\n"}
    summary, _fetched = await _sync(monkeypatch, files, created=created)
    assert summary.added == 2 and summary.folders_created == len(created)
    assert set(created) == {"rmm/Companies/Contoso/.myportal-company", "rmm/Companies/Other/.myportal-company"}
    assert [item["path"] for item in summary.skipped] == ["rmm/stray.ps1"]

    assert {s["name"] for s in await rmm_repo.list_scripts(company_id=1)} == {"a", "only"}
    assert {s["name"] for s in await rmm_repo.list_scripts(company_id=2)} == {"a"}
    company_script = next(s for s in await rmm_repo.list_scripts() if s["name"] == "only")
    assert company_script["company_id"] == 1
    assert rmm_repo.script_available_to(company_script, 1) and not rmm_repo.script_available_to(company_script, 2)
    with pytest.raises(rmm_scripts.RunRequestError):
        await rmm_scripts.queue_runs(script_id=company_script["id"], company_id=2, asset_ids=[20], submitted={},
                                     timeout_seconds=60, requested_by_user_id=None)

    # Moving a script to Common changes who can run it even though the file is unchanged.
    moved = {"rmm/Common/a.ps1": files["rmm/Common/a.ps1"], "rmm/Companies/Contoso/only.ps1": files["rmm/Companies/Contoso/only.ps1"]}
    summary, _fetched = await _sync(monkeypatch, moved, refuse=True)
    assert summary.unchanged == 2 and "refused" in summary.message()


@pytest.mark.anyio
async def test_gitea_create_files_posts_a_batch_and_explains_a_read_only_token(monkeypatch):
    import base64

    import httpx

    seen = {}

    def handler(request):
        seen["path"] = request.url.path
        seen["body"] = json.loads(request.content)
        return httpx.Response(seen.get("status", 201), json={})

    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(gitea, "_client", lambda s: httpx.AsyncClient(transport=transport, headers=gitea._headers(s)))
    settings = gitea.GiteaSettings("https://g", "https://g", "t", "msp", "scripts", "main", "", True)
    await gitea.create_files(settings, {"Common/README.md": "hi"}, "Add folders")
    assert seen["path"] == "/api/v1/repos/msp/scripts/contents"
    assert seen["body"]["branch"] == "main"
    assert seen["body"]["files"] == [{"operation": "create", "path": "Common/README.md",
                                      "content": base64.b64encode(b"hi").decode()}]
    seen["status"] = 403
    with pytest.raises(gitea.GiteaError, match="write access"):
        await gitea.create_files(settings, {"Common/README.md": "hi"}, "Add folders")
