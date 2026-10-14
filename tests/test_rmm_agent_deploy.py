"""Pushing the RMM agent through the tray app, against the real migrations (SQLite)."""
from __future__ import annotations

import hashlib
import json

import httpx
import pytest

from app.services import rmm_agent_deploy as deploy
from tests.test_rmm_scripts import _enrol, _environment, anyio_backend, sqlite_db  # noqa: F401 - fixtures

BINARY = b"MZ fake agent"


@pytest.fixture
def storage(tmp_path, monkeypatch):
    monkeypatch.setattr(deploy, "_STORAGE_DIR", tmp_path)
    return tmp_path


def _cache(storage, os_name="windows", arch="amd64", body=BINARY):
    name = deploy.file_name(os_name, arch)
    (storage / name).write_bytes(body)
    (storage / f"{name}.json").write_text(json.dumps({"sha256": hashlib.sha256(body).hexdigest(), "version": "1.2.3"}))


@pytest.fixture
def sent(monkeypatch):
    messages = []
    live = {"on": False}

    async def send_to_device(device_uid, payload):
        if live["on"]:
            messages.append((device_uid, payload))
        return live["on"]

    monkeypatch.setattr(deploy.tray_service, "send_to_device", send_to_device)
    return messages, live


async def _device(db, *, device_id=5, company_id=1, asset_id=11, os_name="windows", status="active", version=None):
    await db.execute(
        "INSERT INTO tray_devices (id, company_id, asset_id, device_uid, os, status, agent_version) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s)",
        (device_id, company_id, asset_id, f"uid-{device_id}", os_name, status, version),
    )
    return await db.fetch_one("SELECT * FROM tray_devices WHERE id = %s", (device_id,))


async def _commands(db):
    return await db.fetch_all("SELECT * FROM tray_command_log ORDER BY id")


def test_cached_package_needs_the_file_and_its_digest(storage):
    assert deploy.cached_package("windows", "amd64") is None
    _cache(storage)
    package = deploy.cached_package("windows", "amd64")
    assert package["file_name"] == "myportal-rmm-windows-amd64.exe" and package["version"] == "1.2.3"
    assert package["sha256"] == hashlib.sha256(BINARY).hexdigest() and package["size"] == len(BINARY)
    assert deploy.cached_package("../etc", "amd64") is None
    assert deploy.has_any_package("windows") and not deploy.has_any_package("darwin")


@pytest.mark.anyio
async def test_push_queues_once_and_delivers_when_the_tray_is_connected(sqlite_db, storage, sent):
    messages, live = sent
    _cache(storage)
    await _device(sqlite_db)

    first = await deploy.push_to_asset(11, company_id=1, user_id=9)
    assert first["status"] == "queued"
    # Pushing again while the first is still waiting reuses it.
    again = await deploy.push_to_asset(11, company_id=1, user_id=9)
    assert again["command_id"] == first["command_id"]
    live["on"] = True
    delivered = await deploy.push_to_asset(11, company_id=1, user_id=9)
    assert delivered == {"status": "sent", "command_id": first["command_id"]}
    assert messages == [("uid-5", {"type": "install_rmm_agent", "command_id": first["command_id"]})]
    rows = await _commands(sqlite_db)
    assert len(rows) == 1 and rows[0]["status"] == "delivered" and rows[0]["initiated_by_user_id"] == 9


@pytest.mark.anyio
async def test_push_refuses_devices_it_cannot_reach(sqlite_db, storage, sent):
    _cache(storage)
    with pytest.raises(deploy.AgentDeployError, match="tray app"):
        await deploy.push_to_asset(11, company_id=1, user_id=9)
    await _device(sqlite_db, device_id=6, company_id=2, asset_id=20)
    with pytest.raises(deploy.AgentDeployError):
        await deploy.push_to_asset(20, company_id=1, user_id=9)
    await _device(sqlite_db, device_id=7, asset_id=11, os_name="linux")
    with pytest.raises(deploy.AgentDeployError, match="Windows and macOS"):
        await deploy.push_to_asset(11, company_id=1, user_id=9)
    assert await _commands(sqlite_db) == []


@pytest.mark.anyio
async def test_push_without_a_cached_build_fetches_it_first(sqlite_db, storage, sent, monkeypatch):
    await _device(sqlite_db)
    fetched = []

    async def fetch_latest_agent(**kwargs):
        fetched.append(kwargs)
        return {}

    monkeypatch.setattr(deploy, "fetch_latest_agent", fetch_latest_agent)
    with pytest.raises(deploy.AgentDeployError, match="does not have the RMM agent"):
        await deploy.push_to_asset(11, company_id=1, user_id=9)
    assert fetched and await _commands(sqlite_db) == []


@pytest.mark.anyio
async def test_devices_with_an_agent_need_nothing(sqlite_db, storage, sent):
    _cache(storage)
    await _device(sqlite_db, asset_id=10)
    await _enrol(sqlite_db, tray_id=5, asset_id=10)
    assert await deploy.push_to_asset(10, company_id=1, user_id=9) == {"status": "installed", "command_id": None}
    assert await deploy.asset_deploy_state(10, 1) is not None
    assert await _commands(sqlite_db) == []


@pytest.mark.anyio
async def test_company_auto_deploy_queues_once_a_day(sqlite_db, storage, sent):
    _cache(storage)
    device = await _device(sqlite_db)
    assert await deploy.auto_deploy_device(device) is False

    await sqlite_db.execute("UPDATE companies SET rmm_agent_auto_deploy = 1 WHERE id = 1")
    assert await deploy.auto_deploy_device(device) is True
    assert await deploy.auto_deploy_device(device) is False  # still queued
    rows = await _commands(sqlite_db)
    assert len(rows) == 1 and rows[0]["initiated_by_user_id"] is None

    await deploy.record_result(device, int(rows[0]["id"]), ok=False, message="Access denied")
    assert await deploy.auto_deploy_device(device) is False  # failed today, retried tomorrow
    await sqlite_db.execute("UPDATE tray_command_log SET created_at = '2000-01-01 00:00:00'")
    assert await deploy.auto_deploy_device(device) is True


@pytest.mark.anyio
async def test_switching_auto_deploy_on_pushes_to_every_device_without_the_agent(sqlite_db, storage, sent):
    _cache(storage)
    _cache(storage, "darwin", "arm64")
    await _device(sqlite_db, device_id=5, asset_id=10)
    await _enrol(sqlite_db, tray_id=5, asset_id=10)
    await _device(sqlite_db, device_id=6, asset_id=11, os_name="darwin")
    await _device(sqlite_db, device_id=7, asset_id=None, os_name="linux")
    await _device(sqlite_db, device_id=8, asset_id=None, status="revoked")
    await _device(sqlite_db, device_id=9, company_id=2, asset_id=20)

    counts = await deploy.auto_deploy_company(1, user_id=9)
    assert counts == {"sent": 0, "queued": 1, "skipped": 1}
    assert [row["device_id"] for row in await _commands(sqlite_db)] == [6]


@pytest.mark.anyio
async def test_company_push_runs_in_the_background_once_at_a_time(sqlite_db, storage, sent, monkeypatch):
    import asyncio

    from app.services import background

    _cache(storage)
    messages, live = sent
    live["on"] = True
    monkeypatch.setattr(deploy, "COMPANY_PUSH_STAGGER_SECONDS", 0.01)
    await _device(sqlite_db, device_id=5, asset_id=10)
    await _device(sqlite_db, device_id=6, asset_id=11)

    assert deploy.start_company_deploy(1, user_id=9) is True
    assert deploy.start_company_deploy(1, user_id=9) is False  # already running
    assert messages == []  # nothing is pushed before the request returns
    await asyncio.gather(*list(background._BACKGROUND_TASKS.values()))
    assert sorted(uid for uid, _ in messages) == ["uid-5", "uid-6"]
    assert 1 not in deploy._COMPANY_PUSHES


@pytest.mark.anyio
async def test_results_only_land_on_the_reporting_devices_command(sqlite_db, storage, sent):
    _cache(storage)
    device = await _device(sqlite_db)
    other = await _device(sqlite_db, device_id=6, asset_id=None)
    command_id = (await deploy.push_to_asset(11, company_id=1, user_id=9))["command_id"]

    assert await deploy.record_result(other, command_id, ok=True, message=None) is False
    assert await deploy.record_result(device, command_id, ok=False, message="x" * 5000) is True
    row = (await _commands(sqlite_db))[0]
    assert row["status"] == "error" and len(row["error"]) == deploy.MAX_ERROR_LENGTH
    state = await deploy.asset_deploy_state(11, 1)
    assert state["status"] == "error" and state["supported"]
    assert await deploy.record_result(device, command_id, ok=True, message="installed") is True
    assert (await _commands(sqlite_db))[0]["status"] == "completed"



@pytest.fixture
def no_release(monkeypatch):
    async def fetch_latest_agent(**kwargs):
        return {}

    monkeypatch.setattr(deploy, "fetch_latest_agent", fetch_latest_agent)


async def _attempts(db):
    return [dict(row) for row in await db.fetch_all("SELECT * FROM rmm_agent_install_attempts ORDER BY id")]


@pytest.mark.anyio
async def test_every_push_is_kept_in_the_asset_history_with_its_outcome(sqlite_db, storage, sent, no_release):
    from datetime import datetime, timedelta, timezone

    # No tray app: refused, and the reason is kept for the asset.
    with pytest.raises(deploy.AgentDeployError):
        await deploy.push_to_asset(11, company_id=1, user_id=9)
    history = await deploy.asset_install_history(11)
    assert [(item["state"], item["label"]) for item in history] == [("rejected", "Not sent")]
    assert "no MyPortal tray app" in history[0]["reason"] and history[0]["requested_by_email"] == "tech@example.com"

    # No agent build yet: refused too.
    device = await _device(sqlite_db)
    with pytest.raises(deploy.AgentDeployError):
        await deploy.push_to_asset(11, company_id=1, user_id=9)

    _cache(storage)
    command_id = (await deploy.push_to_asset(11, company_id=1, user_id=9))["command_id"]
    history = await deploy.asset_install_history(11)
    assert [item["state"] for item in history] == ["queued", "rejected", "rejected"]
    assert "agent builds" in history[1]["reason"]

    await sqlite_db.execute("UPDATE tray_command_log SET status = 'delivered', delivered_at = %s", (
        (datetime.now(timezone.utc) - timedelta(hours=1)).replace(tzinfo=None),))
    assert (await deploy.asset_install_history(11))[0]["state"] == "installing"
    later = datetime.now(timezone.utc) + timedelta(hours=deploy.NO_REPORT_HOURS)
    stale = deploy.describe_attempt((await sqlite_db.fetch_all(
        "SELECT a.*, c.status AS command_status, c.error AS command_error, c.delivered_at AS command_delivered_at "
        "FROM rmm_agent_install_attempts a JOIN tray_command_log c ON c.id = a.command_id"))[0], now=later)
    assert stale["state"] == "no_report"

    await deploy.record_result(device, command_id, ok=False, message="Access is denied")
    latest = (await deploy.asset_install_history(11))[0]
    assert (latest["state"], latest["reason"]) == ("failed", "Access is denied")
    assert (await deploy.company_latest_attempts(1))[11]["state"] == "failed"
    await deploy.record_result(device, command_id, ok=True, message=None)
    assert (await deploy.asset_install_history(11))[0]["label"] == "Installed"


@pytest.mark.anyio
async def test_automatic_installs_keep_one_row_for_a_reason_that_repeats(sqlite_db, storage, sent, no_release):
    device = await _device(sqlite_db)
    await sqlite_db.execute("UPDATE companies SET rmm_agent_auto_deploy = 1 WHERE id = 1")
    # No agent build: every connection is refused, but the history keeps one row.
    assert await deploy.auto_deploy_device(device) is False
    assert await deploy.auto_deploy_device(device) is False
    rows = await _attempts(sqlite_db)
    assert [(row["source"], row["outcome"]) for row in rows] == [("auto", "rejected")]

    _cache(storage)
    assert await deploy.auto_deploy_device(device) is True
    rows = await _attempts(sqlite_db)
    assert [(row["source"], row["outcome"]) for row in rows] == [("auto", "rejected"), ("auto", "queued")]
    assert rows[-1]["command_id"] == (await _commands(sqlite_db))[0]["id"]


@pytest.mark.anyio
async def test_company_pushes_are_recorded_as_such(sqlite_db, storage, sent):
    _cache(storage)
    await _device(sqlite_db, device_id=6, asset_id=11)
    await deploy.auto_deploy_company(1, user_id=9)
    history = await deploy.asset_install_history(11)
    assert [(item["source_label"], item["state"]) for item in history] == [("Company-wide push", "queued")]

def _github(release_assets, files):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/releases"):
            return httpx.Response(200, json=[
                {"id": 1, "tag_name": "v9.0.0", "draft": True, "assets": release_assets},
                {"id": 2, "tag_name": "v2.0.0", "assets": [{"name": "myportal-tray.msi"}]},
                {"id": 3, "tag_name": "v1.9.0", "assets": release_assets},
            ])
        return httpx.Response(200, content=files[request.url.path.rsplit("/", 1)[-1]])

    return httpx.MockTransport(handler)


def _asset(name, asset_id):
    return {"id": asset_id, "name": name, "updated_at": "2026-10-01", "browser_download_url": f"https://dl.example.com/{name}"}


@pytest.mark.anyio
async def test_fetch_caches_builds_from_the_newest_release_with_the_agent(storage, monkeypatch):
    good = b"windows agent"
    files = {
        "myportal-rmm-windows-amd64.exe": good,
        "myportal-rmm-windows-amd64.exe.sha256": f"{hashlib.sha256(good).hexdigest()}  myportal-rmm-windows-amd64.exe\n".encode(),
        "myportal-rmm-darwin-arm64": b"tampered",
        "myportal-rmm-darwin-arm64.sha256": b"0" * 64,
    }
    assets = [_asset(name, index) for index, name in enumerate(files)]
    transport = _github(assets, files)
    monkeypatch.setattr(deploy, "monitored_client", lambda cls, **kw: cls(transport=transport, **kw))

    results = await deploy.fetch_latest_agent(repo="o/r", github_token="")
    assert results["myportal-rmm-windows-amd64.exe"] is True
    assert results["myportal-rmm-darwin-arm64"] is False  # digest mismatch: not kept
    package = deploy.cached_package("windows", "amd64")
    assert package["version"] == "1.9.0" and package["path"].read_bytes() == good
    versioned = deploy.cached_package("windows", "amd64", "1.9.0")
    assert versioned["path"].read_bytes() == good
    assert deploy.cached_package("darwin", "arm64") is None
    assert deploy.cached_package("darwin", "arm64", "1.9.0") is None
    assert not list(storage.glob("*.tmp"))
    # Unchanged release assets are not downloaded again.
    assert not any((await deploy.fetch_latest_agent(repo="o/r", github_token="")).values())


@pytest.mark.anyio
async def test_latest_refresh_retains_all_platforms_by_version_for_offline_fallback(storage, monkeypatch):
    version = "1.2.3"
    requests = []

    def handler(request):
        requests.append(request.url.path)
        if request.url.path.endswith("/releases"):
            assets = []
            for index, (os_name, arch) in enumerate(deploy.PLATFORMS):
                name = deploy.file_name(os_name, arch)
                assets.extend([_asset(name, index * 2), _asset(name + ".sha256", index * 2 + 1)])
            return httpx.Response(200, json=[{"id": version, "tag_name": "v" + version, "assets": assets}])
        name = request.url.path.rsplit("/", 1)[-1]
        binary_name = name.removesuffix(".sha256")
        body = f"{version}:{binary_name}".encode()
        if name.endswith(".sha256"):
            return httpx.Response(200, text=hashlib.sha256(body).hexdigest())
        return httpx.Response(200, content=body)

    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(deploy, "monitored_client", lambda cls, **kw: cls(transport=transport, **kw))
    assert all((await deploy.fetch_latest_agent(repo="o/r", github_token="")).values())

    # An unchanged latest cache from older servers also warms the version cache
    # without downloading the binaries again.
    for path in (storage / version).iterdir():
        path.unlink()
    requests.clear()
    assert not any((await deploy.fetch_latest_agent(repo="o/r", github_token="")).values())
    assert requests == ["/repos/o/r/releases"]

    version = "1.2.4"
    assert all((await deploy.fetch_latest_agent(repo="o/r", github_token="")).values())

    async def github_unavailable(**kwargs):
        pytest.fail("Fallback attempted to fetch from GitHub instead of using the scheduled cache")

    monkeypatch.setattr(deploy, "fetch_latest_agent", github_unavailable)
    for os_name, arch in deploy.PLATFORMS:
        name = deploy.file_name(os_name, arch)
        for release in ("1.2.3", "1.2.4"):
            package = await deploy.ensure_package(os_name, arch, version="v" + release)
            assert package["path"].read_bytes() == f"{release}:{name}".encode()
            assert package["version"] == release
        assert deploy.cached_package(os_name, arch)["version"] == "1.2.4"
    assert not list(storage.rglob("*.tmp"))


def test_asset_page_shows_why_the_agent_is_missing():
    env = _environment()
    macros = env.get_template("rmm/_macros.html").module
    assert "tray app" in str(macros.agent_deploy_status(None))
    assert "queued" in str(macros.agent_deploy_status({"status": "queued", "supported": True}))
    failed = str(macros.agent_deploy_status({"status": "error", "error": "<b>denied</b>", "supported": True}))
    assert "&lt;b&gt;denied" in failed


def test_tray_versions_that_handle_the_install():
    assert deploy.parse_version("v0.8.4") == (0, 8, 4) and deploy.parse_version("0.9-2-gabc") == (0, 9, 0)
    assert deploy.parse_version("nightly") is None
    assert deploy.tray_can_install({"agent_version": "v0.8.4"}) and deploy.tray_can_install({"agent_version": "1.0.0"})
    assert not deploy.tray_can_install({"agent_version": "v0.8.3"})
    assert not deploy.tray_can_install({"agent_version": "0.0.0-dev"})
    assert deploy.tray_can_install({"agent_version": None})  # not reported yet


@pytest.mark.anyio
async def test_old_tray_without_a_newer_published_release_is_refused(sqlite_db, storage, sent):
    _cache(storage)
    await _device(sqlite_db, version="v0.8.3")
    with pytest.raises(deploy.AgentDeployError, match="too old"):
        await deploy.push_to_asset(11, company_id=1, user_id=9)
    await sqlite_db.execute("INSERT INTO tray_versions (version, download_url) VALUES ('0.8.3', 'https://x/a.msi')")
    with pytest.raises(deploy.AgentDeployError, match="Admin > Tray > Versions"):
        await deploy.push_to_asset(11, company_id=1, user_id=9)
    assert await _commands(sqlite_db) == []
    state = await deploy.asset_deploy_state(11, 1)
    assert state["tray_outdated"] and state["tray_version"] == "v0.8.3"


@pytest.mark.anyio
async def test_old_tray_updates_first_and_gets_the_install_once_it_reports_the_new_version(sqlite_db, storage, sent):
    from app.services import tray as tray_service

    messages, live = sent
    live["on"] = True
    _cache(storage)
    device = await _device(sqlite_db, version="v0.8.3")
    await sqlite_db.execute("INSERT INTO tray_versions (version, download_url) VALUES ('0.8.4', 'https://x/a.msi')")

    result = await deploy.push_to_asset(11, company_id=1, user_id=9)
    assert result["status"] == "updating_tray"
    assert [payload["type"] for _uid, payload in messages] == ["update"]
    rows = {row["command"]: row for row in await _commands(sqlite_db)}
    assert rows["install_rmm_agent"]["status"] == "queued" and rows["update"]["status"] == "delivered"
    # Pushing again nudges the tray app again but keeps the one install.
    await deploy.push_to_asset(11, company_id=1, user_id=9)
    assert [row["command"] for row in await _commands(sqlite_db)].count("install_rmm_agent") == 1

    # Reconnecting on the old version: the install is held back.
    messages.clear()
    await tray_service.deliver_queued_commands(device)
    assert messages == []
    # The updated tray app reports its version: the install goes out.
    await sqlite_db.execute("UPDATE tray_devices SET agent_version = 'v0.8.4' WHERE id = 5")
    await tray_service.deliver_queued_commands(device)
    assert messages == [("uid-5", {"type": "install_rmm_agent", "command_id": result["command_id"]})]


@pytest.mark.anyio
async def test_auto_deploy_queues_the_update_for_an_old_tray(sqlite_db, storage, sent):
    _cache(storage)
    device = await _device(sqlite_db, version="v0.8.3")
    await sqlite_db.execute("UPDATE companies SET rmm_agent_auto_deploy = 1 WHERE id = 1")
    await sqlite_db.execute("INSERT INTO tray_versions (version, download_url) VALUES ('v0.9.0', 'https://x/a.msi')")
    assert await deploy.auto_deploy_device(device) is True
    assert sorted(row["command"] for row in await _commands(sqlite_db)) == ["install_rmm_agent", "update"]
    assert all(row["status"] == "queued" for row in await _commands(sqlite_db))


def test_asset_page_says_the_tray_app_must_update():
    env = _environment()
    macros = env.get_template("rmm/_macros.html").module
    text = str(macros.agent_deploy_status(
        {"supported": True, "tray_outdated": True, "tray_version": "v0.8.3", "min_tray_version": "0.8.4", "status": "queued"}
    ))
    assert "v0.8.3" in text and "0.8.4" in text and "queued" in text


@pytest.mark.anyio
async def test_versioned_package_uses_requested_release_and_separate_cache(storage, monkeypatch):
    name = deploy.file_name("windows", "amd64")
    requests = []
    assets = [_asset(name, 1), _asset(name + ".sha256", 2)]

    def handler(request):
        requests.append(request.url.path)
        if request.url.path.endswith("/releases/tags/v1.2.3"):
            return httpx.Response(200, json={"id": 1, "tag_name": "v1.2.3", "assets": assets})
        if request.url.path.endswith("/releases/tags/v1.2.4") or request.url.path.endswith("/releases/tags/1.2.4"):
            return httpx.Response(404)
        if request.url.path.endswith(".sha256"):
            return httpx.Response(200, text=hashlib.sha256(BINARY).hexdigest())
        if request.url.path.endswith(name):
            return httpx.Response(200, content=BINARY)
        raise AssertionError(f"Unexpected request: {request.url}")

    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(deploy, "monitored_client", lambda cls, **kw: cls(transport=transport, **kw))
    # A different latest package must not be returned for the selected release.
    _cache(storage, body=b"latest")
    package = await deploy.ensure_package("windows", "amd64", version="v1.2.3")
    assert package["version"] == "1.2.3" and package["path"].read_bytes() == BINARY
    assert package["path"].parent == storage / "1.2.3"
    assert deploy.cached_package("windows", "amd64")["path"].read_bytes() == b"latest"
    before = len(requests)
    assert await deploy.ensure_package("windows", "amd64", version="1.2.3") == package
    assert len(requests) == before
    assert await deploy.ensure_package("windows", "amd64", version="1.2.4") is None


@pytest.mark.parametrize("version", ["../secret", "", "1.2.3/../../secret", "dev", "1.2"])
def test_release_selector_rejects_invalid_paths(version):
    with pytest.raises(ValueError):
        deploy.release_version(version)


@pytest.mark.anyio
async def test_package_routes_keep_version_on_download_url(storage):
    from app.features.rmm.agent_deploy_routes import agent_package, agent_package_download, _package
    from fastapi import HTTPException

    folder = storage / "1.2.3"
    folder.mkdir()
    _cache(folder)
    package = await agent_package(os="windows", arch="amd64", tray_device={}, version="v1.2.3")
    assert package["download_path"] == "/api/rmm/agent/package/download?os=windows&arch=amd64&version=1.2.3"
    response = await agent_package_download(os="windows", arch="amd64", tray_device={}, version="1.2.3")
    assert response.path == folder / deploy.file_name("windows", "amd64")
    with pytest.raises(HTTPException) as error:
        await _package("windows", "amd64", "../secret")
    assert error.value.status_code == 400


@pytest.mark.anyio
async def test_repair_repushes_an_existing_agent_and_keeps_offline_history(sqlite_db, storage, sent):
    messages, live = sent
    _cache(storage)
    await _device(sqlite_db, asset_id=10)
    await _enrol(sqlite_db, tray_id=5, asset_id=10)
    first = await deploy.push_to_asset(10, company_id=1, user_id=9, repair=True)
    assert first["status"] == "queued"
    second = await deploy.push_to_asset(10, company_id=1, user_id=9, repair=True)
    assert second["command_id"] == first["command_id"]
    live["on"] = True
    delivered = await deploy.push_to_asset(10, company_id=1, user_id=9, repair=True)
    assert delivered == {"status": "sent", "command_id": first["command_id"]}
    assert len(await _commands(sqlite_db)) == 1
    assert messages[0][1]["type"] == "install_rmm_agent"
    history = await deploy.asset_install_history(10)
    assert all(item["source_label"] == "Technician repair" for item in history)
    device = await deploy.deploy_repo.get_tray_device(5)
    await deploy.record_result(device, first["command_id"], ok=True, message="verified")
    assert (await deploy.asset_install_history(10))[0]["label"] == "Repaired"


@pytest.mark.anyio
async def test_repair_can_install_a_missing_agent_and_rejects_other_tenants(sqlite_db, storage, sent):
    _cache(storage)
    await _device(sqlite_db)
    assert (await deploy.push_to_asset(11, company_id=1, user_id=9, repair=True))["status"] == "queued"
    await _device(sqlite_db, device_id=6, company_id=2, asset_id=20)
    with pytest.raises(deploy.AgentDeployError):
        await deploy.push_to_asset(20, company_id=1, user_id=9, repair=True)
    assert len(await _commands(sqlite_db)) == 1


@pytest.mark.anyio
async def test_repair_action_available_for_existing_agents_only_with_write_access(sqlite_db):
    from app.features.rmm import routes
    await _device(sqlite_db, asset_id=10)
    await _enrol(sqlite_db, tray_id=5, asset_id=10)
    assert (await routes.asset_rmm_context(1, 10, can_run=True))["can_push_agent"] is True
    assert (await routes.asset_rmm_context(1, 10, can_run=False))["can_push_agent"] is False


@pytest.mark.anyio
async def test_repair_route_checks_tenant_write_access_and_audits(sqlite_db, storage, sent, monkeypatch):
    from fastapi import HTTPException
    from starlette.requests import Request
    from app.features.rmm import agent_deploy_routes as routes
    _cache(storage)
    await _device(sqlite_db, asset_id=10)
    await _enrol(sqlite_db, tray_id=5, asset_id=10)
    audits = []
    async def context(request, **kwargs):
        assert kwargs == {"write": True, "api": True}
        return {"id": 9}, {}, 1, True
    async def audit(**kwargs):
        audits.append(kwargs)
    monkeypatch.setattr(routes, "_context", context)
    monkeypatch.setattr(routes.audit_service, "record", audit)
    request = Request({"type": "http", "method": "POST", "path": "/api/rmm/assets/10/agent-repair"})
    result = await routes.repair_agent(10, request)
    assert result["status"] == "queued" and "Repair queued" in result["message"]
    assert audits[0]["action"] == "rmm.agent.repair" and audits[0]["entity_id"] == 10
    with pytest.raises(HTTPException) as exc:
        await routes.repair_agent(20, request)
    assert exc.value.status_code == 404 and len(audits) == 1
