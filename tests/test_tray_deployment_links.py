from __future__ import annotations

import zipfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from starlette.requests import Request
from starlette.responses import HTMLResponse

from app import main
from app.api.routes import tray_deployment as deploy_routes
from app.repositories import tray as tray_repo
from app.security.encryption import decrypt_secret, encrypt_secret
from app.services import tray as tray_service
from app.services import tray_deployment
from app.services import tray_deployment_builds as builds

PORTAL = "https://portal.example.com"
TOKEN = "AbCdEfGhIjKlMnOpQrStUvWxYz_0123456789-abcde"
SLUG = "SlugSlugSlugSlugSlugSlugSlugSlugSlugSlug_-1"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


async def _receive() -> dict[str, Any]:
    return {"type": "http.request", "body": b"", "more_body": False}


def _request(path: str = "/") -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": path,
            "query_string": b"",
            "headers": [],
            "scheme": "http",
            "server": ("testserver", 80),
        },
        _receive,
    )


@pytest.fixture
def installers(tmp_path, monkeypatch) -> Path:
    (tmp_path / "myportal-tray.msi").write_bytes(b"MSI-BYTES")
    (tmp_path / "myportal-tray.pkg").write_bytes(b"PKG-BYTES")
    monkeypatch.setattr(tray_deployment.tray_installer, "_TRAY_STATIC_DIR", tmp_path)
    return tmp_path


def _link(**overrides: Any) -> dict[str, Any]:
    row = {
        "id": 7,
        "company_id": 3,
        "company_name": "Acme Pty Ltd",
        "label": "Acme",
        "slug_encrypted": encrypt_secret(SLUG),
        "install_token_id": 11,
        "install_token_encrypted": encrypt_secret(TOKEN),
        "revoked_at": None,
        "created_at": datetime(2026, 10, 1),
        "download_count": 0,
        "last_downloaded_at": None,
    }
    row.update(overrides)
    return row


@pytest.mark.parametrize(
    "bad_url",
    ["https://portal.example.com/\"&calc", "https://x.example.com/%TEMP%", "ftp://x", "https://a b"],
)
def test_scripts_reject_unsafe_portal_urls(bad_url):
    with pytest.raises(ValueError):
        tray_deployment.windows_script(bad_url, TOKEN)
    with pytest.raises(ValueError):
        tray_deployment.macos_script(bad_url, TOKEN)


def test_scripts_reject_unexpected_token():
    with pytest.raises(ValueError):
        tray_deployment.windows_script(PORTAL, "tok'en; rm -rf /")


def test_macos_bundle_contents(installers):
    path = tray_deployment.build_macos_bundle(PORTAL, TOKEN)
    try:
        with zipfile.ZipFile(path) as archive:
            env = archive.read("myportal-tray.env").decode()
            assert f"MYPORTAL_URL={PORTAL}\n" in env
            assert f"ENROL_TOKEN={TOKEN}\n" in env
            launcher = archive.getinfo("Install MyPortal Tray.command")
            assert (launcher.external_attr >> 16) & 0o111
            assert "myportal-tray.pkg" in archive.namelist()
    finally:
        path.unlink()


def test_bundle_requires_cached_installer(tmp_path, monkeypatch):
    monkeypatch.setattr(tray_deployment.tray_installer, "_TRAY_STATIC_DIR", tmp_path)
    with pytest.raises(tray_deployment.InstallerNotAvailable):
        tray_deployment.build_macos_bundle(PORTAL, TOKEN)


def test_download_filename_is_sanitised():
    assert (
        tray_deployment.download_filename('Acme "Pty" Ltd/../', "macos")
        == "MyPortal-Tray-Acme-Pty-Ltd-macOS.zip"
    )
    assert tray_deployment.download_filename("Acme", "exe") == "MyPortal-Tray-Acme-Setup.exe"


@pytest.mark.anyio
async def test_create_link_stores_hashes_and_encrypted_values(monkeypatch):
    monkeypatch.setattr(
        tray_deployment.companies_repo,
        "get_company_by_id",
        AsyncMock(return_value={"id": 3, "name": "Acme"}),
    )
    create_token = AsyncMock(return_value={"id": 11})
    monkeypatch.setattr(tray_repo, "create_install_token", create_token)
    create_link = AsyncMock(side_effect=lambda **kwargs: {"id": 7, **kwargs})
    monkeypatch.setattr(tray_repo, "create_deployment_link", create_link)
    queue_build = AsyncMock(return_value=1)
    monkeypatch.setattr(tray_repo, "create_deployment_build", queue_build)

    record, slug = await tray_deployment.create_deployment_link(
        company_id=3, label="", created_by_user_id=1, expires_in_days=30
    )

    token_kwargs = create_token.await_args.kwargs
    assert token_kwargs["company_id"] == 3
    link_kwargs = create_link.await_args.kwargs
    assert link_kwargs["slug_hash"] == tray_service.hash_token(slug)
    assert link_kwargs["install_token_id"] == 11
    assert link_kwargs["label"] == "Acme deployment"
    raw_token = decrypt_secret(link_kwargs["install_token_encrypted"], allow_plaintext=False)
    assert token_kwargs["token_hash"] == tray_service.hash_token(raw_token)
    assert decrypt_secret(link_kwargs["slug_encrypted"], allow_plaintext=False) == slug
    assert slug not in str(record.get("slug_hash"))
    queue_build.assert_awaited_once_with(7)
    expires_at = link_kwargs["expires_at"]
    assert token_kwargs["expires_at"] == expires_at
    assert timedelta(days=29) < expires_at - datetime.utcnow() <= timedelta(days=30)


@pytest.mark.anyio
async def test_create_link_rejects_unknown_expiry(monkeypatch):
    monkeypatch.setattr(tray_repo, "create_install_token", AsyncMock())
    with pytest.raises(ValueError):
        await tray_deployment.create_deployment_link(
            company_id=3, label=None, created_by_user_id=1, expires_in_days=3650
        )


@pytest.mark.anyio
async def test_expired_link_is_unavailable(monkeypatch):
    expired = _link(expires_at=(datetime.utcnow() - timedelta(minutes=1)).isoformat())
    monkeypatch.setattr(
        tray_repo, "get_deployment_link_by_slug_hash", AsyncMock(return_value=expired)
    )
    monkeypatch.setattr(tray_repo, "get_install_token_by_id", AsyncMock(return_value={"id": 11}))
    with pytest.raises(tray_deployment.DeploymentLinkUnavailable, match="expired"):
        await tray_deployment.resolve_active_link(SLUG)
    assert await tray_deployment.link_status(expired) == "expired"


@pytest.mark.anyio
async def test_resolve_active_link_returns_token(monkeypatch):
    monkeypatch.setattr(
        tray_repo, "get_deployment_link_by_slug_hash", AsyncMock(return_value=_link())
    )
    monkeypatch.setattr(
        tray_repo, "get_install_token_by_id", AsyncMock(return_value={"id": 11})
    )
    link, token = await tray_deployment.resolve_active_link(SLUG)
    assert link["id"] == 7
    assert token == TOKEN


@pytest.mark.anyio
@pytest.mark.parametrize(
    "link_row, token_row",
    [
        (None, {"id": 11}),
        (_link(revoked_at=datetime(2026, 10, 2)), {"id": 11}),
        (_link(), {"id": 11, "revoked_at": datetime(2026, 10, 2)}),
        (_link(), {"id": 11, "expires_at": datetime.utcnow() - timedelta(days=1)}),
        (_link(), None),
    ],
)
async def test_resolve_rejects_unusable_links(monkeypatch, link_row, token_row):
    monkeypatch.setattr(
        tray_repo, "get_deployment_link_by_slug_hash", AsyncMock(return_value=link_row)
    )
    monkeypatch.setattr(tray_repo, "get_install_token_by_id", AsyncMock(return_value=token_row))
    with pytest.raises(tray_deployment.DeploymentLinkUnavailable):
        await tray_deployment.resolve_active_link(SLUG)


@pytest.mark.anyio
async def test_resolve_rejects_malformed_slug_without_lookup(monkeypatch):
    lookup = AsyncMock()
    monkeypatch.setattr(tray_repo, "get_deployment_link_by_slug_hash", lookup)
    with pytest.raises(tray_deployment.DeploymentLinkUnavailable):
        await tray_deployment.resolve_active_link("../../etc")
    lookup.assert_not_awaited()


@pytest.mark.anyio
async def test_revoke_link_also_revokes_install_token(monkeypatch):
    monkeypatch.setattr(tray_repo, "get_deployment_link", AsyncMock(return_value=_link()))
    revoke_link = AsyncMock()
    revoke_token = AsyncMock()
    monkeypatch.setattr(tray_repo, "revoke_deployment_link", revoke_link)
    monkeypatch.setattr(tray_repo, "revoke_install_token", revoke_token)
    assert await tray_deployment.revoke_deployment_link(7) is True
    revoke_link.assert_awaited_once_with(7)
    revoke_token.assert_awaited_once_with(11)


@pytest.mark.anyio
async def test_macos_download_serves_bundle_and_counts(monkeypatch, installers):
    monkeypatch.setattr(main.settings, "portal_url", PORTAL)
    monkeypatch.setattr(
        tray_deployment, "resolve_active_link", AsyncMock(return_value=(_link(), TOKEN))
    )
    mark = AsyncMock()
    monkeypatch.setattr(tray_repo, "mark_deployment_link_downloaded", mark)

    response = await deploy_routes.deployment_macos_bundle(SLUG, _request())

    assert response.media_type == "application/zip"
    assert response.headers["cache-control"] == "no-store"
    assert "MyPortal-Tray-Acme-Pty-Ltd-macOS.zip" in response.headers["content-disposition"]
    with zipfile.ZipFile(response.path) as archive:
        assert TOKEN in archive.read("myportal-tray.env").decode()
    mark.assert_awaited_once_with(7)
    Path(response.path).unlink()


@pytest.mark.anyio
async def test_windows_exe_served_from_ready_build(monkeypatch, tmp_path):
    monkeypatch.setattr(builds, "_STORAGE_DIR", tmp_path)
    path = builds.artifact_path(42, "exe")
    path.parent.mkdir(parents=True)
    path.write_bytes(b"EXE")
    monkeypatch.setattr(
        tray_deployment, "resolve_active_link", AsyncMock(return_value=(_link(), TOKEN))
    )
    monkeypatch.setattr(
        tray_repo,
        "get_latest_ready_deployment_build",
        AsyncMock(return_value={"id": 42, "status": "ready"}),
    )
    monkeypatch.setattr(tray_repo, "mark_deployment_link_downloaded", AsyncMock())

    response = await deploy_routes.deployment_windows_exe(SLUG)

    assert Path(response.path) == path
    assert "MyPortal-Tray-Acme-Pty-Ltd-Setup.exe" in response.headers["content-disposition"]


@pytest.mark.anyio
async def test_windows_msi_without_build_is_503(monkeypatch):
    monkeypatch.setattr(
        tray_deployment, "resolve_active_link", AsyncMock(return_value=(_link(), TOKEN))
    )
    monkeypatch.setattr(
        tray_repo, "get_latest_ready_deployment_build", AsyncMock(return_value=None)
    )
    with pytest.raises(HTTPException) as excinfo:
        await deploy_routes.deployment_windows_msi(SLUG)
    assert excinfo.value.status_code == 503


@pytest.mark.anyio
async def test_download_for_unknown_slug_is_404(monkeypatch):
    monkeypatch.setattr(
        tray_deployment,
        "resolve_active_link",
        AsyncMock(side_effect=tray_deployment.DeploymentLinkUnavailable("nope")),
    )
    with pytest.raises(HTTPException) as excinfo:
        await deploy_routes.deployment_macos_script(SLUG, _request())
    assert excinfo.value.status_code == 404


@pytest.mark.anyio
async def test_download_without_installer_is_503(monkeypatch, tmp_path):
    monkeypatch.setattr(tray_deployment.tray_installer, "_TRAY_STATIC_DIR", tmp_path)
    monkeypatch.setattr(
        tray_deployment, "resolve_active_link", AsyncMock(return_value=(_link(), TOKEN))
    )
    with pytest.raises(HTTPException) as excinfo:
        await deploy_routes.deployment_macos_bundle(SLUG, _request())
    assert excinfo.value.status_code == 503


@pytest.mark.anyio
async def test_public_page_renders_for_active_link(monkeypatch, installers):
    monkeypatch.setattr(main.settings, "portal_url", PORTAL)
    monkeypatch.setattr(
        tray_deployment, "resolve_active_link", AsyncMock(return_value=(_link(), TOKEN))
    )
    monkeypatch.setattr(builds, "ready_artifact", AsyncMock(return_value=Path("setup.exe")))
    response = await deploy_routes.deployment_page(SLUG, _request(f"/deploy/{SLUG}"))
    body = response.body.decode()
    assert response.status_code == 200
    assert f"{PORTAL}/deploy/{SLUG}/windows.exe" in body
    assert f"{PORTAL}/deploy/{SLUG}/windows.msi" in body
    assert f"{PORTAL}/deploy/{SLUG}/macos.zip" in body
    assert "Acme Pty Ltd" in body
    assert TOKEN not in body
    assert response.headers["referrer-policy"] == "no-referrer"


@pytest.mark.anyio
async def test_public_page_for_revoked_link_is_404(monkeypatch):
    monkeypatch.setattr(
        tray_deployment,
        "resolve_active_link",
        AsyncMock(side_effect=tray_deployment.DeploymentLinkUnavailable("This deployment link has been revoked.")),
    )
    response = await deploy_routes.deployment_page(SLUG, _request(f"/deploy/{SLUG}"))
    assert response.status_code == 404
    assert "has been revoked" in response.body.decode()


@pytest.mark.anyio
async def test_admin_page_hides_revoked_links(monkeypatch):
    import app.repositories.companies as companies_repo

    monkeypatch.setattr(main.settings, "portal_url", PORTAL)
    monkeypatch.setattr(
        main, "_require_super_admin_page", AsyncMock(return_value=({"id": 1}, None))
    )
    monkeypatch.setattr(
        tray_repo,
        "list_deployment_links",
        AsyncMock(return_value=[_link(), _link(id=8, revoked_at=datetime(2026, 10, 2))]),
    )
    monkeypatch.setattr(tray_repo, "get_install_token_by_id", AsyncMock(return_value={"id": 11}))
    monkeypatch.setattr(companies_repo, "list_companies", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        tray_repo,
        "list_deployment_builds",
        AsyncMock(return_value=[{"id": 3, "deployment_link_id": 7, "status": "ready", "release_tag": "v1.2.3"}]),
    )
    captured: dict[str, Any] = {}

    async def fake_render(template_name, request, user, *, extra):
        captured["extra"] = extra
        return HTMLResponse("ok")

    monkeypatch.setattr(main, "_render_template", fake_render)

    await main.admin_tray_deployment_links_page(_request("/admin/tray/deployment-links"))

    links = captured["extra"]["links"]
    assert [link["id"] for link in links] == [7]
    assert links[0]["url"] == f"{PORTAL}/deploy/{SLUG}"
    assert captured["extra"]["hidden_revoked_count"] == 1
    assert links[0]["build"]["release_tag"] == "v1.2.3"
