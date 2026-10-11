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


async def _device(db, *, device_id=5, company_id=1, asset_id=11, os_name="windows", status="active"):
    await db.execute(
        "INSERT INTO tray_devices (id, company_id, asset_id, device_uid, os, status) VALUES (%s, %s, %s, %s, %s, %s)",
        (device_id, company_id, asset_id, f"uid-{device_id}", os_name, status),
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
    assert deploy.cached_package("darwin", "arm64") is None
    assert not list(storage.glob("*.tmp"))
    # Unchanged release assets are not downloaded again.
    assert not any((await deploy.fetch_latest_agent(repo="o/r", github_token="")).values())


def test_asset_page_shows_why_the_agent_is_missing():
    env = _environment()
    macros = env.get_template("rmm/_macros.html").module
    assert "tray app" in str(macros.agent_deploy_status(None))
    assert "queued" in str(macros.agent_deploy_status({"status": "queued", "supported": True}))
    failed = str(macros.agent_deploy_status({"status": "error", "error": "<b>denied</b>", "supported": True}))
    assert "&lt;b&gt;denied" in failed
