"""Remote control through RustDesk and MeshCentral, against the real migrations (SQLite)."""
from __future__ import annotations

import base64
import json
from urllib.parse import parse_qs, urlsplit

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.repositories import rmm as rmm_repo
from app.repositories import rmm_remote_control as remote_repo
from app.core.config import get_settings
from app.services import rmm_remote_control as remote
from app.services import rmm_scripts, rmm_script_files
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


def _meshcentral_env(monkeypatch) -> None:
    """Point MeshCentral at a known site, user and token key (via the .env)."""

    settings = get_settings()
    monkeypatch.setattr(settings, "meshcentral_server_url", "https://mesh.example.com/")
    monkeypatch.setattr(settings, "meshcentral_username", "tactical")
    monkeypatch.setattr(settings, "meshcentral_login_token_key", KEY)


def test_rustdesk_links_strip_spaces_and_quote_the_password():
    assert remote.rustdesk_url("123 456 789") == "rustdesk://connection/new/123456789"
    assert remote.rustdesk_url("123456789", "p&ss word") == "rustdesk://connection/new/123456789?password=p%26ss%20word"
    with pytest.raises(remote.RemoteControlError):
        remote.rustdesk_url("123/../evil")
    assert remote.rustdesk_url("42", "pw", "file_transfer") == "rustdesk://file-transfer/42?password=pw"
    assert remote.rustdesk_url("42", None, "terminal") == "rustdesk://terminal/42"
    assert remote.rustdesk_url("123 456", "stored-secret", "remote_prompt") == "rustdesk://connection/new/123456"


def test_meshcentral_modes_pick_the_device_view():
    views = {mode: parse_qs(urlsplit(remote.meshcentral_url("https://m.example.com", "t", KEY, "n", mode=mode)).query)["viewmode"]
             for mode in ("remote", "file_transfer", "terminal")}
    assert views == {"remote": ["11"], "file_transfer": ["13"], "terminal": ["12"]}


def test_meshcentral_link_carries_a_token_meshcentral_can_decrypt():
    url = remote.meshcentral_url("https://mesh.example.com/", "Tactical", KEY, "node//abc$def@", now=1_700_000_000)
    parts = urlsplit(url)
    assert f"{parts.scheme}://{parts.netloc}{parts.path}" == "https://mesh.example.com/"
    query = parse_qs(parts.query)
    assert query["gotonode"] == ["abc$def@"] and query["viewmode"] == ["11"] and query["hide"] == ["31"]
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
@pytest.mark.parametrize("mode", ["remote", "remote_prompt"])
async def test_activation_script_runs_and_session_returns_the_launch_link(sqlite_db, monkeypatch, mode):
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
    session = await remote.start(provider="rustdesk", company_id=1, asset_id=10, user_id=9, mode=mode)
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
    assert ready["launch_url"] == "rustdesk://connection/new/123456789" + ("?password=one-time" if mode == "remote" else "")
    assert ready["mode"] == mode
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
    _meshcentral_env(monkeypatch)
    await remote.save_settings("meshcentral", {"is_enabled": "on", "id_field": "MeshCentral Node"}, user_id=9)
    session = await remote.start(provider="meshcentral", company_id=1, asset_id=10, user_id=9)
    assert session["status"] == "ready" and session["run_id"] is None
    query = parse_qs(urlsplit(session["launch_url"]).query)
    assert query["gotonode"] == ["abc"] and _decode_mesh_token(query["login"][0])["u"] == "user//tactical"
    # A device without a node ID opens MeshCentral signed in.
    other = await remote.start(provider="meshcentral", company_id=1, asset_id=11, user_id=9)
    assert other["status"] == "ready" and "gotonode" not in other["launch_url"]

    with pytest.raises(remote.RemoteControlError, match="turned off"):
        await remote.start(provider="rustdesk", company_id=1, asset_id=10, user_id=9)


def test_built_in_meshcentral_script_picks_the_os_file():
    assert remote.builtin_meshcentral_script({"os": "windows"})["path"] == "builtin/meshcentral_activate.ps1"
    assert remote.builtin_meshcentral_script({"os": "windows"})["language"] == "powershell"
    assert remote.builtin_meshcentral_script({"os": ""})["path"] == "builtin/meshcentral_activate.ps1"
    assert remote.builtin_meshcentral_script({"os": "darwin"})["path"] == "builtin/meshcentral_activate_macos.sh"
    assert remote.builtin_meshcentral_script({"os": "linux"})["path"] == "builtin/meshcentral_activate.sh"
    assert remote.builtin_meshcentral_script({"os": "linux"})["language"] == "bash"


@pytest.mark.anyio
async def test_built_in_meshcentral_installs_into_the_companies_group(sqlite_db, monkeypatch):
    # The built-in MeshCentral needs the .env settings, but no script.
    _meshcentral_env(monkeypatch)
    await remote.save_settings("meshcentral", {"is_enabled": "on"}, user_id=9)
    # Group is resolved (created if missing) up front; the node is found afterwards.
    group_names = []

    async def fake_ensure_group(server_url, login_token, name):
        group_names.append(name)
        return "grp123"

    async def fake_find_node(server_url, login_token, group_id, names, **kwargs):
        assert group_id == "grp123"
        return "node//abc$def@"

    async def fake_download(server_url, login_token, group_id, arch_id):
        assert server_url == "https://mesh.example.com" and group_id == "grp123" and arch_id == 4
        assert _decode_mesh_token(login_token)["u"] == "user//tactical"
        return b"MZ\x00\xffinstaller"

    monkeypatch.setattr(remote.meshcentral, "download_installer", fake_download)
    monkeypatch.setattr(remote.meshcentral, "ensure_group", fake_ensure_group)
    monkeypatch.setattr(remote.meshcentral, "find_node", fake_find_node)

    _r, agent = await _enrol(sqlite_db)  # hostname "PC", company 1 (Contoso)
    await rmm_repo.record_agent_checkin(agent["id"], {"script_files_supported": True})
    await sqlite_db.execute("UPDATE rmm_agents SET arch = 'amd64' WHERE id = ?", (agent["id"],))
    agent = await rmm_scripts.authenticate_agent(_r["auth_token"])
    session = await remote.start(provider="meshcentral", company_id=1, asset_id=10, user_id=9)
    assert session["status"] == "activating" and session["launch_url"] is None
    # The group is named after the device's company, not one shared group.
    assert group_names == ["Contoso"]

    jobs = await rmm_scripts.wait_for_jobs(agent, wait_seconds=0)
    assert len(jobs) == 1 and jobs[0]["path"] == "builtin/meshcentral_activate.ps1"
    # Only the local path and group reach the endpoint; credentials stay on MyPortal.
    assert set(jobs[0]["env"]) == {"MESH_INSTALLER_PATH", "MESH_GROUP_ID"}
    descriptor = jobs[0]["files"][0]
    assert jobs[0]["env"]["MESH_INSTALLER_PATH"] == descriptor["destination"]
    assert "content_base64" not in descriptor
    run_id = session["run_id"]
    assert await rmm_script_files.download_file({"id": agent["id"] + 1}, run_id, 0) is None
    assert await rmm_script_files.download_file(agent, run_id, 1) is None
    assert (await rmm_script_files.download_file(agent, run_id, 0))[0] == b"MZ\x00\xffinstaller"
    assert jobs[0]["env"]["MESH_GROUP_ID"] == "grp123"

    await rmm_scripts.record_result(agent, session["run_id"], {"exit_code": 0, "stdout": "installed"})
    assert await rmm_script_files.download_file(agent, run_id, 0) is None
    ready = await remote.session_status(session["id"], company_id=1, user_id=9, is_super_admin=False)
    assert ready["status"] == "ready"
    query = parse_qs(urlsplit(ready["launch_url"]).query)
    assert query["gotonode"] == ["abc$def@"] and _decode_mesh_token(query["login"][0])["u"] == "user//tactical"

    # If the device has not checked in yet, fall back to a signed-in link.
    async def no_node(server_url, login_token, group_id, names, **kwargs):
        return None

    monkeypatch.setattr(remote.meshcentral, "find_node", no_node)
    fallback = await remote.session_status(session["id"], company_id=1, user_id=9, is_super_admin=False)
    assert fallback["status"] == "ready" and "gotonode" not in fallback["launch_url"]


def test_settings_page_renders_providers_and_script_values():
    env = _environment()
    env.globals["static_url"] = lambda path: path
    html = env.get_template("rmm/remote_control.html").render(
        providers=[
            {"provider": "rustdesk", "label": "RustDesk", "is_enabled": True, "activation_script_id": 1,
             "activation_script_active": True, "id_field": "",
             "fields": [{"key": "param:Password", "name": "Password", "mandatory": False, "sensitive": True,
                         "value": "", "value_set": True, "default": None, "help": ""}]},
            {"provider": "meshcentral", "label": "MeshCentral", "is_enabled": False, "activation_script_id": None,
             "id_field": "", "builtin": True,
             "fields": []},
        ],
        scripts=[{"id": 1, "name": "Activate", "path": "rmm/Common/Activate.ps1"}],
        rustdesk_env={"cfg_set": True, "installer_set": False, "macos_installer_set": True},
        mesh_env={"server_set": True, "username_set": False, "token_key_set": True},
        variables=[{"group": "Company variables", "label": "Key", "token": "{{company.variables.Key}}"}],
    )
    assert 'action="/rmm/remote-control/rustdesk"' in html and 'action="/rmm/remote-control/meshcentral"' in html
    assert 'name="entry:param:Password"' in html and "Saved: leave blank to keep it" in html
    assert "MESHCENTRAL_USERNAME" in html and "not set" in html and "{{company.variables.RustDeskKey}}" in html
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


def test_asset_menu_has_a_rustdesk_menu_and_a_meshcentral_menu():
    env = _environment()
    template = env.from_string(
        '{% import "rmm/_macros.html" as rmm_ui %}{{ rmm_ui.asset_menu(7, "<PC>", providers, run_script=run) }}'
    )
    providers = [{"provider": "rustdesk", "label": "RustDesk"}, {"provider": "meshcentral", "label": "MeshCentral"}]
    html = template.render(providers=providers, run=True)
    # The RustDesk icon opens a menu of every session rather than starting one.
    assert 'src="/static/images/rustdesk.svg"' in html and 'aria-label="RustDesk actions for &lt;PC&gt;"' in html
    assert "button--icon" not in html and "Start a RustDesk remote session" not in html
    assert html.count('data-rmm-remote="rustdesk"') == 4
    for mode in ("remote", "remote_prompt", "file_transfer", "terminal"):
        assert f'role="menuitem" data-rmm-remote="rustdesk" data-rmm-remote-mode="{mode}"' in html
    # The RMM menu is shown as the MeshCentral icon rather than the "RMM" text.
    assert 'src="/static/images/meshcentral.svg"' in html and ">RMM <" not in html
    for mode in ("remote", "file_transfer", "terminal"):
        assert f'data-rmm-remote="meshcentral" data-rmm-remote-mode="{mode}"' in html
    assert html.count('role="menuitem"') == 8 and html.count("data-rmm-menu") == 2
    assert 'data-rmm-remote-name="&lt;PC&gt;"' in html and "<PC>" not in html
    assert "data-rmm-run-open" in html
    assert 'data-rmm-remote="meshcentral" data-rmm-remote-mode="remote_prompt"' not in html
    rustdesk_only = template.render(providers=providers[:1], run=False)
    assert "rmm-rustdesk__icon" in rustdesk_only and "Remote desktop (Prompt)" in rustdesk_only
    assert rustdesk_only.count("data-rmm-menu") == 1 and "meshcentral" not in rustdesk_only
    # Without MeshCentral the menu keeps its "RMM" label.
    assert ">RMM <" in template.render(providers=[], run=True)
    in_menu = env.from_string(
        '{% import "rmm/_macros.html" as rmm_ui %}{{ rmm_ui.asset_menu(7, "PC", providers, rustdesk_in_menu=True) }}'
    ).render(providers=providers[:1])
    assert "rmm-rustdesk__icon" not in in_menu and in_menu.count('data-rmm-remote="rustdesk"') == 4


@pytest.mark.anyio
@pytest.mark.parametrize("mode", ["file_transfer", "remote_prompt"])
async def test_builtin_rustdesk_applies_the_config_and_its_own_password(sqlite_db, monkeypatch, mode):
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "rustdesk_cfg", "=0nI9cfgString")
    monkeypatch.setattr(get_settings(), "rustdesk_installer_url", "")
    await remote.save_settings("rustdesk", {"is_enabled": "1"}, user_id=9)
    assert (await remote.load_settings())[0]["builtin"] is True
    _r, agent = await _enrol(sqlite_db)

    session = await remote.start(provider="rustdesk", company_id=1, asset_id=10, user_id=9, mode=mode)
    assert session["status"] == "activating" and session["mode"] == mode
    run = await rmm_repo.get_run(session["run_id"])
    assert run["script_name"] == "RustDesk (built-in)" and run["run_source"] == "remote_control"
    assert run["script_id"] is None and run["inputs"] == []
    job = (await rmm_scripts.wait_for_jobs(agent, wait_seconds=0))[0]
    assert job["language"] == "powershell" and "--get-id" in job["script"]
    password = job["env"]["MYPORTAL_RUSTDESK_PASSWORD"]
    assert job["env"]["RUSTDESK_CFG"] == "=0nI9cfgString" and "RUSTDESK_INSTALLER_URL" not in job["env"]
    assert len(password) >= 16

    await rmm_scripts.record_result(agent, session["run_id"], {"exit_code": 0, "stdout": "##myportal[session.id]=987 654 321\n"})
    ready = await remote.session_status(session["id"], company_id=1, user_id=9, is_super_admin=False)
    assert ready["status"] == "ready"
    assert ready["launch_url"] == (f"rustdesk://file-transfer/987654321?password={password}"
                                    if mode == "file_transfer" else "rustdesk://connection/new/987654321")
    # The password never reaches run history.
    stored = await rmm_repo.get_run(session["run_id"])
    assert password not in json.dumps({k: str(v) for k, v in stored.items()})

    # A device without RustDesk: the script's error is shown.
    second = await remote.start(provider="rustdesk", company_id=1, asset_id=10, user_id=9)
    await rmm_scripts.wait_for_jobs(agent, wait_seconds=0)
    await rmm_scripts.record_result(agent, second["run_id"], {
        "exit_code": 1, "stderr": "\nRustDesk is not installed on this device.\nAt line:12 char:9\n"})
    failed = await remote.session_status(second["id"], company_id=1, user_id=9, is_super_admin=False)
    assert failed["status"] == "failed" and "RustDesk is not installed on this device." in failed["error"]

    with pytest.raises(remote.RemoteControlError, match="RMM agent"):
        await remote.start(provider="rustdesk", company_id=1, asset_id=11, user_id=9)


def test_builtin_scripts_ship_for_windows_and_unix():
    windows = remote.builtin_rustdesk_script({"os": "windows"})
    mac = remote.builtin_rustdesk_script({"os": "darwin"})
    linux = remote.builtin_rustdesk_script({"os": "linux"})
    assert windows["language"] == "powershell" and windows["path"].endswith(".ps1")
    assert mac["language"] == "bash" and mac["content"].startswith("#!/bin/bash")
    assert mac["path"] == "builtin/rustdesk_activate_macos.sh" and "hdiutil attach" in mac["content"]
    assert linux["path"] == "builtin/rustdesk_activate.sh"
    for script in (windows, mac, linux):
        assert "RUSTDESK_CFG" in script["content"] and "MYPORTAL_RUSTDESK_PASSWORD" in script["content"]
        assert "##myportal[session.id]=" in script["content"]


@pytest.mark.anyio
async def test_builtin_rustdesk_sends_each_platform_its_own_installer(sqlite_db, monkeypatch):
    from app.core.config import get_settings

    monkeypatch.setattr(get_settings(), "rustdesk_cfg", "")
    monkeypatch.setattr(get_settings(), "rustdesk_installer_url", "https://downloads.example.com/rustdesk.exe")
    monkeypatch.setattr(get_settings(), "rustdesk_macos_installer_url", "https://downloads.example.com/rustdesk.dmg")
    assert remote.rustdesk_environment() == {"cfg_set": False, "installer_set": True, "macos_installer_set": True}
    await remote.save_settings("rustdesk", {"is_enabled": "1"}, user_id=9)
    _r, agent = await _enrol(sqlite_db, os_name="darwin")

    session = await remote.start(provider="rustdesk", company_id=1, asset_id=10, user_id=9)
    job = (await rmm_scripts.wait_for_jobs(agent, wait_seconds=0))[0]
    assert job["language"] == "bash" and "hdiutil" in job["script"]
    assert job["env"]["RUSTDESK_INSTALLER_URL"] == "https://downloads.example.com/rustdesk.dmg"
    assert "RUSTDESK_CFG" not in job["env"]

    await rmm_scripts.record_result(agent, session["run_id"], {"exit_code": 0, "stdout": "##myportal[session.id]=123456789\n"})
    ready = await remote.session_status(session["id"], company_id=1, user_id=9, is_super_admin=False)
    assert ready["launch_url"].startswith("rustdesk://connection/new/123456789?password=")


@pytest.mark.anyio
async def test_activation_is_not_held_up_by_other_running_scripts(sqlite_db, monkeypatch):
    await remote.save_settings("rustdesk", {"is_enabled": "1"}, user_id=9)
    _r, agent = await _enrol(sqlite_db)
    content = "Start-Sleep 3600"
    busy = {"id": None, "name": "DISM Cleanup", "path": "Common/dism.ps1", "language": "powershell",
            "content": content, "content_sha256": "x"}
    await rmm_scripts.queue_for_agent(script=busy, agent=agent, entries={}, inputs=[], timeout_seconds=600,
                                      requested_by_user_id=None)
    assert [job["path"] for job in await rmm_scripts.claim_jobs(agent)] == ["Common/dism.ps1"]

    # An agent too old for Run Anyway keeps activation in the queue.
    waiting = await remote.start(provider="rustdesk", company_id=1, asset_id=10, user_id=9)
    assert await rmm_scripts.claim_jobs(agent) == []
    assert (await rmm_repo.get_run(waiting["run_id"]))["status"] == "queued"

    await sqlite_db.execute("UPDATE rmm_agents SET run_anyway_supported = 1")
    agent = await rmm_repo.get_agent(agent["id"])
    session = await remote.start(provider="rustdesk", company_id=1, asset_id=10, user_id=9)
    jobs = await rmm_scripts.claim_jobs(agent)
    assert [(job["id"], job["run_anyway"]) for job in jobs] == [(session["run_id"], True)]


def test_agent_state_follows_the_agents_last_poll():
    from datetime import datetime, timedelta

    now = datetime(2026, 1, 1, 12, 0, 0)
    assert rmm_scripts.agent_state(None) == "missing"
    assert rmm_scripts.agent_state({"last_seen_utc": now - timedelta(seconds=30)}, now=now) == "connected"
    assert rmm_scripts.agent_state({"last_seen_utc": "2026-01-01T11:59:30Z"}, now=now) == "connected"
    assert rmm_scripts.agent_state({"last_seen_utc": now - timedelta(minutes=10)}, now=now) == "offline"
    assert rmm_scripts.agent_state({"last_seen_utc": None}, now=now) == "offline"


@pytest.mark.anyio
async def test_remote_control_needs_the_devices_rmm_agent(sqlite_db, monkeypatch):
    from app.features.rmm import routes

    await remote.save_settings("rustdesk", {"is_enabled": "1", "id_field": "RustDesk ID"}, user_id=9)
    without = await routes.asset_rmm_context(1, 10, can_run=True)
    assert without["agent_state"] == "missing" and without["remote_control"] == []
    assert await routes.company_agent_states(1) == {}

    await _enrol(sqlite_db)
    with_agent = await routes.asset_rmm_context(1, 10, can_run=True)
    assert with_agent["agent_state"] == "connected"
    assert with_agent["remote_control"] == [{"provider": "rustdesk", "label": "RustDesk"}]
    assert await routes.company_agent_states(1) == {10: "connected"}
    details = await routes.company_agent_details(1)
    assert details[10]["state"] == "connected"
    assert details[10]["version"] == (await sqlite_db.fetch_one("SELECT agent_version FROM rmm_agents"))["agent_version"]
    # Installed but no longer polling.
    await sqlite_db.execute("UPDATE rmm_agents SET last_seen_utc = '2000-01-01 00:00:00'")
    assert (await routes.asset_rmm_context(1, 10, can_run=True))["agent_state"] == "offline"
    assert await routes.company_agent_states(1) == {10: "offline"}
    assert await routes.company_agent_states(2) == {}


def test_agent_indicator_shows_a_tick_an_o_or_a_cross():
    macros = _environment().get_template("rmm/_macros.html").module
    connected = str(macros.agent_indicator("connected"))
    assert "rmm-agent-indicator--connected" in connected and "✓" in connected and 'aria-label="RMM agent connected"' in connected
    assert ">O<" in str(macros.agent_indicator("offline"))
    assert "✗" in str(macros.agent_indicator("missing")) and "--missing" in str(macros.agent_indicator("bogus"))


@pytest.mark.anyio
async def test_prompt_mode_is_rustdesk_only():
    with pytest.raises(remote.RemoteControlError, match="only available for RustDesk"):
        await remote.start(provider="meshcentral", company_id=1, asset_id=10, user_id=9, mode="remote_prompt")


@pytest.mark.anyio
@pytest.mark.parametrize('os_name,arch,expected', [
    ('windows', 'amd64', 4), ('windows', 'ARM64', 43), ('windows', '386', 3),
    ('linux', 'x86_64', 6), ('linux', 'aarch64', 32), ('linux', 'i686', 5),
    ('darwin', 'arm64', 10005), ('macos', 'amd64', 10005),
])
async def test_mesh_installer_is_selected_and_snapshotted(monkeypatch, os_name, arch, expected):
    _meshcentral_env(monkeypatch)
    agent = {'id': 1, 'company_id': 2, 'os': os_name, 'arch': arch,
             'shells': ['powershell', 'bash'], 'script_files_supported': True}
    async def download(site, token, group, arch_id):
        assert arch_id == expected and group == 'company-group'
        return b'MZinstaller'
    async def settings(site, token, group):
        assert group == 'company-group'
        return b'MeshID=group\nServerID=server\nMeshServer=wss://mesh.test/agent.ashx\n'
    async def create_run(**kwargs):
        payload = json.loads(remote.decrypt_secret(kwargs['payload_encrypted']))
        item = payload['files'][0]
        assert base64.b64decode(item['content_base64']) == b'MZinstaller'
        assert item['destination'] == payload['env']['MESH_INSTALLER_PATH']
        assert item['destination'].startswith('C:\\' if os_name == 'windows' else '/')
        assert 'MESH_SITE' not in payload['env'] and 'token' not in payload['env']
        assert len(payload['files']) == (1 if os_name == 'windows' else 2)
        if os_name != 'windows':
            config = payload['files'][1]
            assert config['destination'] == item['destination'] + '.msh'
            assert base64.b64decode(config['content_base64']).startswith(b'MeshID=group')
        return 123
    monkeypatch.setattr(remote.meshcentral, 'download_installer', download)
    monkeypatch.setattr(remote.meshcentral, 'download_settings', settings)
    monkeypatch.setattr(remote.rmm_repo, 'create_run', create_run)
    assert await remote._queue_builtin_meshcentral(agent, 'company-group', 9) == 123


@pytest.mark.anyio
@pytest.mark.parametrize('supported,arch,message', [
    (False, 'amd64', 'Update the RMM agent'), (True, '', 'supported architecture'),
    (True, 'unknown', 'supported architecture'),
])
async def test_mesh_rejects_incompatible_agent_before_downloading(monkeypatch, supported, arch, message):
    agent = {'id': 1, 'company_id': 2, 'os': 'windows', 'arch': arch,
             'shells': ['powershell'], 'script_files_supported': supported}
    with pytest.raises(remote.RemoteControlError, match=message):
        await remote._queue_builtin_meshcentral(agent, 'group', 9)
