import asyncio
import json

from starlette.requests import Request

from app.api.routes import tray

DEVICE = {"id": 7, "device_uid": "uid-7", "agent_version": "1.0.0"}
PUBLISHED = {
    "version": "1.2.0",
    "download_url": "https://example.com/1.2.0.msi",
    "required": 0,
    "rollout_percent": 100,
    "sha256": None,
}
CACHED = {
    "name": "myportal-tray.msi",
    "platform": "windows",
    "version": "1.5.0",
    "download_url": "https://portal.example.com/static/tray/myportal-tray.msi",
    "sha256": "ab" * 32,
}


def _request():
    return Request(
        {
            "type": "http",
            "method": "GET",
            "scheme": "https",
            "server": ("portal.example.com", 443),
            "path": "/api/tray/version",
            "headers": [
                (b"authorization", b"Bearer device-token"),
                (b"x-tray-os", b"windows"),
            ],
        }
    )


def _patch(monkeypatch, *, published, pushed, cached=CACHED):
    completed = []

    async def latest(platform):
        return published

    async def device_by_hash(token_hash):
        return DEVICE

    async def recent_command(device_id, command, *, since):
        assert (device_id, command) == (7, "update")
        return pushed

    async def mark_completed(command_id, *, error=None):
        completed.append(command_id)

    monkeypatch.setattr(tray.tray_repo, "get_latest_tray_version", latest)
    monkeypatch.setattr(tray.tray_repo, "get_device_by_auth_hash", device_by_hash)
    monkeypatch.setattr(tray.tray_repo, "get_recent_delivered_command", recent_command)
    monkeypatch.setattr(tray.tray_repo, "mark_command_completed", mark_completed)
    monkeypatch.setattr(
        tray.tray_installer, "cached_server_installers", lambda portal_url: [cached] if cached else []
    )
    return completed


def _version(monkeypatch, **kwargs):
    completed = _patch(monkeypatch, **kwargs)
    response = asyncio.run(tray.get_tray_version(_request()))
    return json.loads(response.body), completed


def test_pushed_update_offers_cached_release_newer_than_published(monkeypatch):
    body, completed = _version(monkeypatch, published=PUBLISHED, pushed={"id": 42})

    assert body == {
        "version": "1.5.0",
        "download_url": CACHED["download_url"],
        "required": False,
        "sha256": CACHED["sha256"],
    }
    assert completed == [42]


def test_pushed_update_offers_cached_release_when_nothing_published(monkeypatch):
    body, _ = _version(monkeypatch, published=None, pushed={"id": 42})

    assert body["version"] == "1.5.0"


def test_scheduled_check_ignores_cached_release(monkeypatch):
    body, completed = _version(monkeypatch, published=PUBLISHED, pushed=None)

    assert body["version"] == "1.2.0"
    assert completed == []


def test_pushed_update_keeps_newer_published_version(monkeypatch):
    published = {**PUBLISHED, "version": "2.0.0"}
    body, completed = _version(monkeypatch, published=published, pushed={"id": 42})

    assert body["version"] == "2.0.0"
    assert completed == [42]


def test_pushed_update_without_cached_installer_serves_published(monkeypatch):
    body, _ = _version(monkeypatch, published=PUBLISHED, pushed={"id": 42}, cached=None)

    assert body["version"] == "1.2.0"


def test_tray_version_comparison():
    assert tray._is_newer_tray_version("v1.10.0", "1.9.9")
    assert tray._is_newer_tray_version("1.2.1", "1.2")
    assert not tray._is_newer_tray_version("1.2", "1.2.0")
    assert not tray._is_newer_tray_version("1.2.0-rc1", "1.2.0")
    assert tray._is_newer_tray_version("1.0.0", None)
    assert not tray._is_newer_tray_version("garbage", "1.0.0")
