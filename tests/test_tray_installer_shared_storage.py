"""Tray installers cached outside an immutable blue/green release."""

from __future__ import annotations

import contextlib

import httpx
import pytest
from starlette.applications import Starlette
from starlette.routing import Mount
from starlette.staticfiles import StaticFiles
from starlette.testclient import TestClient

from app.services import tray_installer

RELEASE = {
    "id": 1,
    "tag_name": "v0.8.1",
    "assets": [
        {
            "id": 7,
            "name": "myportal-tray.msi",
            "updated_at": "2026-10-07T10:56:34Z",
            "size": 3,
            "browser_download_url": "https://github.example/myportal-tray.msi",
        }
    ],
}


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _patch_client(monkeypatch, handler) -> None:
    @contextlib.asynccontextmanager
    async def client(_cls, **kwargs):
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler), **kwargs) as c:
            yield c

    monkeypatch.setattr(tray_installer, "monitored_client", client)


@pytest.mark.anyio
async def test_failed_download_raises_instead_of_reporting_success(monkeypatch, tmp_path):
    monkeypatch.setattr(tray_installer, "_TRAY_STATIC_DIR", tmp_path)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/releases/latest"):
            return httpx.Response(200, json=RELEASE)
        return httpx.Response(500)

    _patch_client(monkeypatch, handler)
    with pytest.raises(tray_installer.TrayInstallerDownloadError, match="myportal-tray.msi: HTTP 500"):
        await tray_installer.fetch_latest_tray_installers(repo="owner/repo")
    assert not (tmp_path / "myportal-tray.msi").exists()


@pytest.mark.anyio
async def test_successful_download_is_cached(monkeypatch, tmp_path):
    monkeypatch.setattr(tray_installer, "_TRAY_STATIC_DIR", tmp_path)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/releases/latest"):
            return httpx.Response(200, json=RELEASE)
        return httpx.Response(200, content=b"msi")

    _patch_client(monkeypatch, handler)
    results = await tray_installer.fetch_latest_tray_installers(repo="owner/repo")

    assert results["myportal-tray.msi"] is True
    assert tray_installer.get_cached_latest_release_info()["release_tag"] == "v0.8.1"


def test_tray_mount_serves_installers_linked_outside_the_release(tmp_path):
    shared = tmp_path / "shared" / "tray-installers"
    shared.mkdir(parents=True)
    (shared / "myportal-tray.msi").write_bytes(b"msi")
    static = tmp_path / "release" / "app" / "static"
    static.mkdir(parents=True)
    (static / "tray").symlink_to(shared)

    generic = Starlette(routes=[Mount("/static", StaticFiles(directory=str(static)))])
    with TestClient(generic) as client:
        # The reason the dedicated mount exists.
        assert client.get("/static/tray/myportal-tray.msi").status_code == 404

    app = Starlette(
        routes=[
            Mount("/static/tray", StaticFiles(directory=str(static / "tray"), check_dir=False)),
            Mount("/static", StaticFiles(directory=str(static))),
        ]
    )
    with TestClient(app) as client:
        response = client.get("/static/tray/myportal-tray.msi")
    assert response.status_code == 200
    assert response.content == b"msi"


def test_app_mounts_tray_before_generic_static():
    from app.main import app

    paths = [getattr(route, "path", None) for route in app.routes]
    assert paths.index("/static/tray") < paths.index("/static")
