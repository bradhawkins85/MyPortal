"""Remote control through RustDesk and MeshCentral, against the real migrations (SQLite)."""
from __future__ import annotations

import base64
import json
from urllib.parse import parse_qs, urlsplit

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.repositories import rmm as rmm_repo
from app.repositories import rmm_remote_control as remote_repo
from app.services import rmm_remote_control as remote
from app.services import rmm_scripts
from tests.test_rmm_scripts import _enrol, _environment, _sync, anyio_backend, sqlite_db  # noqa: F401 - fixtures

KEY = "ab" * 48
ACTIVATE = (
    "param([string]$RelayKey, [securestring]$Password)\n"
    "if (-not (Get-Service RustDesk -ErrorAction SilentlyContinue)) { Install-RustDesk $RelayKey }\n"
    "Write-Output '##myportal[session.id]=123 456 789'\n"
)


def _decode_mesh_token(token: str) -> dict:
    raw = base64.b64decode(token, altchars=b"@$")
    iv, tag, ciphertext = raw[:12], raw[12:28], raw[28:]
    return json.loads(AESGCM(bytes.fromhex(KEY)[:32]).decrypt(iv, ciphertext + tag, None))


def test_rustdesk_links_strip_spaces_and_quote_the_password():
    assert remote.rustdesk_url("123 456 789") == "rustdesk://connection/new/123456789"
    assert remote.rustdesk_url("123456789", "p&ss word") == "rustdesk://connection/new/123456789?password=p%26ss%20word"
    with pytest.raises(remote.RemoteControlError):
        remote.rustdesk_url("123/../evil")


def test_meshcentral_link_carries_a_token_meshcentral_can_decrypt():
    url = remote.meshcentral_url("https://mesh.example.com/", "Tactical", KEY, "node//abc$def@", now=1_700_000_000)
    parts = urlsplit(url)
    assert f"{parts.scheme}://{parts.netloc}{parts.path}" == "https://mesh.example.com/"
    query = parse_qs(parts.query)
    assert query["gotonode"] == ["node//abc$def@"] and query["viewmode"] == ["11"] and query["hide"] == ["31"]
    assert _decode_mesh_token(query["login"][0]) == {"a": 3, "u": "user//tactical", "time": 1_700_000_000}
    signed_in_only = parse_qs(urlsplit(remote.meshcentral_url("https://mesh.example.com", "user/acme/Bob", KEY)).query)
    assert "gotonode" not in signed_in_only and _decode_mesh_token(signed_in_only["login"][0])["u"] == "user/acme/bob"
    with pytest.raises(remote.RemoteControlError):
        remote.meshcentral_url("https://mesh.example.com", "t", KEY, "node//x&evil=1")


def test_session_markers_are_taken_out_of_the_output():
    stdout, values = remote.take_session_markers(
        "Installing\r\n##myportal[session.id]=123 456\r\n  ##myportal[session.Password]=s3cret  \nDone"
    )
    assert values == {"id": "123 456", "password": "s3cret"}
    assert "s3cret" not in stdout and "123 456" not in stdout
    assert stdout.startswith("Installing\r\n##myportal[session.id]=••••••\r\n") and stdout.endswith("Done")
    assert remote.take_session_markers("plain") == ("plain", {})


async def _setup(sqlite_db, monkeypatch, **form):
    await _sync(monkeypatch, {"rmm/Common/Remote/Activate RustDesk.ps1": ACTIVATE})
    script = (await rmm_repo.list_scripts())[0]
    await remote.save_settings("rustdesk", {"is_enabled": "1", "activation_script_id": str(script["id"]), **form}, user_id=9)
    return script


@pytest.mark.anyio
async def test_activation_script_runs_and_session_returns_the_launch_link(sqlite_db, monkeypatch):
    script = await _setup(sqlite_db, monkeypatch)
    # Values for the script are read once the script's fields are on the page.
    await remote.save_settings("rustdesk", {
        "is_enabled": "1", "activation_script_id": str(script["id"]),
        "entry:param:RelayKey": "{{company.variables.Tenant}}", "entry:param:Password": "hunter2",
    }, user_id=9)
    settings = {item["provider"]: item for item in await remote.load_settings()}
    fields = {item["name"]: item for item in settings["rustdesk"]["fields"]}
    assert fields["RelayKey"]["value"] == "{{company.variables.Tenant}}"
    assert fields["Password"]["value"] == "" and fields["Password"]["value_set"]
    # A blank secret keeps the saved one.
    await remote.save_settings("rustdesk", {
        "is_enabled": "1", "activation_script_id": str(script["id"]),
        "entry:param:RelayKey": "{{company.variables.Tenant}}", "entry:param:Password": "",
    }, user_id=9)
    assert await remote.enabled_providers() == [{"provider": "rustdesk", "label": "RustDesk"}]

    _r, agent = await _enrol(sqlite_db)
    session = await remote.start(provider="rustdesk", company_id=1, asset_id=10, user_id=9)
    assert session["status"] == "activating" and session["launch_url"] is None
    run = await rmm_repo.get_run(session["run_id"])
    assert run["run_source"] == "remote_control" and run["timeout_seconds"] <= remote.MAX_ACTIVATION_TIMEOUT_SECONDS

    jobs = await rmm_scripts.wait_for_jobs(agent, wait_seconds=0)
    assert jobs[0]["parameters"] == {"RelayKey": "contoso", "Password": "hunter2"}
    outcome = await rmm_scripts.record_result(agent, session["run_id"], {
        "exit_code": 0,
        "stdout": "Reset password\n##myportal[session.id]=123 456 789\n",
        "custom_values": [{"scope": "session", "name": "password", "value": "one-time"}],
    })
    assert outcome["status"] == "completed"
    run = await rmm_repo.get_run(session["run_id"])
    assert "123 456 789" not in run["stdout"] and "one-time" not in json.dumps(run["custom_values"])
    assert {item["name"]: item["message"] for item in run["custom_values"]} == {
        "id": "Used for remote control", "password": "Used for remote control"}

    ready = await remote.session_status(session["id"], company_id=1, user_id=9, is_super_admin=False)
    assert ready["status"] == "ready"
    assert ready["launch_url"] == "rustdesk://connection/new/123456789?password=one-time"
    # Only the technician who started it (or a super admin) sees the link.
    assert await remote.session_status(session["id"], company_id=1, user_id=5, is_super_admin=False) is None
    assert await remote.session_status(session["id"], company_id=2, user_id=9, is_super_admin=True) is None
    assert (await remote.session_status(session["id"], company_id=1, user_id=5, is_super_admin=True))["launch_url"]

    await sqlite_db.execute("UPDATE rmm_remote_sessions SET expires_at = '2000-01-01 00:00:00'")
    expired = await remote.session_status(session["id"], company_id=1, user_id=9, is_super_admin=False)
    assert expired["status"] == "expired" and expired["launch_url"] is None
    assert (await remote_repo.get_session(session["id"]))["values_encrypted"] is None


@pytest.mark.anyio
async def test_a_failed_script_or_missing_id_fails_the_session(sqlite_db, monkeypatch):
    await _setup(sqlite_db, monkeypatch)
    _r, agent = await _enrol(sqlite_db)
    first = await remote.start(provider="rustdesk", company_id=1, asset_id=10, user_id=9)
    await rmm_scripts.wait_for_jobs(agent, wait_seconds=0)
    await rmm_scripts.record_result(agent, first["run_id"], {"exit_code": 3, "stdout": ""})
    failed = await remote.session_status(first["id"], company_id=1, user_id=9, is_super_admin=False)
    assert failed["status"] == "failed" and "exit code 3" in failed["error"]

    second = await remote.start(provider="rustdesk", company_id=1, asset_id=10, user_id=9)
    await rmm_scripts.wait_for_jobs(agent, wait_seconds=0)
    await rmm_scripts.record_result(agent, second["run_id"], {"exit_code": 0, "stdout": "nothing"})
    missing = await remote.session_status(second["id"], company_id=1, user_id=9, is_super_admin=False)
    assert missing["status"] == "failed" and "No RustDesk ID" in missing["error"]

    # Without the agent the script cannot run.
    with pytest.raises(remote.RemoteControlError, match="RMM agent"):
        await remote.start(provider="rustdesk", company_id=1, asset_id=11, user_id=9)


@pytest.mark.anyio
async def test_session_values_from_other_scripts_are_masked_and_ignored(sqlite_db, monkeypatch):
    script = await _setup(sqlite_db, monkeypatch)
    _r, agent = await _enrol(sqlite_db)
    queued = await rmm_scripts.queue_runs(script_id=script["id"], company_id=1, asset_ids=[10], submitted={},
                                          timeout_seconds=None, requested_by_user_id=9)
    run_id = queued["queued"][0]["run_id"]
    await rmm_scripts.wait_for_jobs(agent, wait_seconds=0)
    await rmm_scripts.record_result(agent, run_id, {"exit_code": 0, "stdout": "##myportal[session.id]=42\n"})
    run = await rmm_repo.get_run(run_id)
    assert "42" not in run["stdout"]
    assert run["custom_values"][0]["applied"] is False and run["custom_values"][0]["value"] == "••••••"


@pytest.mark.anyio
async def test_without_a_script_the_id_comes_from_a_custom_field(sqlite_db, monkeypatch):

    async def custom_values(asset_id):
        return {"MeshCentral Node": "node//abc"} if asset_id == 10 else {}

    monkeypatch.setattr(rmm_scripts, "_asset_custom_values", custom_values)
    with pytest.raises(remote.RemoteControlError, match="address, a user name"):
        await remote.save_settings("meshcentral", {"is_enabled": "1", "server_url": "https://mesh.example.com"}, user_id=9)
    with pytest.raises(remote.RemoteControlError, match="logintokenkey"):
        await remote.save_settings("meshcentral", {"is_enabled": "1", "secret": "not-hex"}, user_id=9)
    with pytest.raises(remote.RemoteControlError, match="MeshCentral address"):
        await remote.save_settings("meshcentral", {"server_url": "javascript:alert(1)"}, user_id=9)
    await remote.save_settings("meshcentral", {
        "is_enabled": "on", "server_url": "https://mesh.example.com/", "username": "tactical",
        "secret": KEY, "id_field": "meshcentral node",
    }, user_id=9)
    settings = {item["provider"]: item for item in await remote.load_settings()}
    assert settings["meshcentral"]["secret_set"] and "secret_encrypted" not in settings["meshcentral"]
    # Saving again with a blank key keeps it.
    await remote.save_settings("meshcentral", {
        "is_enabled": "on", "server_url": "https://mesh.example.com", "username": "tactical", "id_field": "MeshCentral Node",
    }, user_id=9)

    session = await remote.start(provider="meshcentral", company_id=1, asset_id=10, user_id=9)
    assert session["status"] == "ready" and session["run_id"] is None
    query = parse_qs(urlsplit(session["launch_url"]).query)
    assert query["gotonode"] == ["node//abc"] and _decode_mesh_token(query["login"][0])["u"] == "user//tactical"
    # A device without a node ID opens MeshCentral signed in.
    other = await remote.start(provider="meshcentral", company_id=1, asset_id=11, user_id=9)
    assert other["status"] == "ready" and "gotonode" not in other["launch_url"]

    with pytest.raises(remote.RemoteControlError, match="turned off"):
        await remote.start(provider="rustdesk", company_id=1, asset_id=10, user_id=9)


def test_settings_page_renders_providers_and_script_values():
    env = _environment()
    env.globals["static_url"] = lambda path: path
    html = env.get_template("rmm/remote_control.html").render(
        providers=[
            {"provider": "rustdesk", "label": "RustDesk", "is_enabled": True, "activation_script_id": 1,
             "activation_script_active": True, "server_url": "", "username": "", "secret_set": False, "id_field": "",
             "fields": [{"key": "param:Password", "name": "Password", "mandatory": False, "sensitive": True,
                         "value": "", "value_set": True, "default": None, "help": ""}]},
            {"provider": "meshcentral", "label": "MeshCentral", "is_enabled": False, "activation_script_id": None,
             "server_url": "https://mesh.example.com", "username": "<b>t</b>", "secret_set": True, "id_field": "",
             "fields": []},
        ],
        scripts=[{"id": 1, "name": "Activate", "path": "rmm/Common/Activate.ps1"}],
        variables=[{"group": "Company variables", "label": "Key", "token": "{{company.variables.Key}}"}],
    )
    assert 'action="/rmm/remote-control/rustdesk"' in html and 'action="/rmm/remote-control/meshcentral"' in html
    assert 'name="entry:param:Password"' in html and "Saved: leave blank to keep it" in html
    assert "&lt;b&gt;t&lt;/b&gt;" in html and "{{company.variables.RustDeskKey}}" in html
    assert '<option value="1" selected>' in html


class _FakeMain:
    def __init__(self, memberships, pack=True):
        self.memberships = memberships
        self.pack = pack

    def _feature_pack_available(self, slug):
        return self.pack

    async def _get_effective_company_membership(self, request, user_id, company_id):
        return self.memberships.get(company_id)

    def _membership_menu_can(self, user, membership, key, *, write=False):
        return bool(user.get("is_super_admin")) or bool(membership and membership.get(key) == ("write" if write else "read"))


@pytest.mark.anyio
async def test_ticket_buttons_follow_the_tickets_company_permission(sqlite_db, monkeypatch):
    from types import SimpleNamespace

    from fastapi import HTTPException

    from app.features.rmm import remote_control_routes as routes

    main = _FakeMain({1: {"menu.rmm_scripts": "write"}, 2: {"menu.rmm_scripts": "read"}})
    monkeypatch.setattr(routes, "_assets_routes", lambda: SimpleNamespace(_main=lambda: main))
    await remote.save_settings("rustdesk", {"is_enabled": "1", "id_field": "RustDesk ID"}, user_id=9)
    tech = {"id": 9, "is_super_admin": False}
    assert await routes.providers_for_company(None, tech, 1) == [{"provider": "rustdesk", "label": "RustDesk"}]
    # Read-only access, no company, or the feature pack turned off: no buttons.
    assert await routes.providers_for_company(None, tech, 2) == []
    assert await routes.providers_for_company(None, tech, None) == []
    main.pack = False
    assert await routes.providers_for_company(None, tech, 1) == []

    async def signed_in(request):
        return tech, None

    main._require_authenticated_user = signed_in
    assert await routes._company_user(None, 1) == tech
    with pytest.raises(HTTPException) as denied:
        await routes._company_user(None, 2)
    assert denied.value.status_code == 403


def test_asset_menu_groups_rmm_actions():
    env = _environment()
    template = env.from_string(
        '{% import "rmm/_macros.html" as rmm_ui %}{{ rmm_ui.asset_menu(7, "<PC>", providers, run_script=run) }}'
    )
    providers = [{"provider": "rustdesk", "label": "RustDesk"}, {"provider": "meshcentral", "label": "MeshCentral"}]
    html = template.render(providers=providers, run=True)
    assert html.count('role="menuitem"') == 3 and "data-rmm-menu" in html
    assert 'data-rmm-remote="meshcentral" data-rmm-remote-asset="7" data-rmm-remote-name="&lt;PC&gt;"' in html
    assert "data-rmm-run-open" in html and "<PC>" not in html
    assert "data-rmm-run-open" not in template.render(providers=providers, run=False)
