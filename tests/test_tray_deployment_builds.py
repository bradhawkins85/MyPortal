from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import aiosqlite
import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.api.routes import tray_deployment as deploy_routes
from app.core.database import db
from app.repositories import tray as tray_repo
from app.security.encryption import encrypt_secret
from app.services import message_templates, tray_deployment, value_templates
from app.services import tray_deployment_builds as builds

PORTAL = "https://portal.example.com"
TOKEN = "AbCdEfGhIjKlMnOpQrStUvWxYz_0123456789-abcde"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture
async def sqlite_db(tmp_path, monkeypatch):
    conn = await aiosqlite.connect(str(tmp_path / "builds.db"))
    conn.row_factory = aiosqlite.Row
    await conn.executescript(
        """
        CREATE TABLE tray_install_tokens (
          id INTEGER PRIMARY KEY AUTOINCREMENT, company_id INT, label TEXT,
          token_hash TEXT, token_prefix TEXT, created_by_user_id INT,
          created_at DATETIME DEFAULT CURRENT_TIMESTAMP, expires_at DATETIME,
          revoked_at DATETIME, last_used_at DATETIME, use_count INT DEFAULT 0);
        CREATE TABLE tray_deployment_links (
          id INTEGER PRIMARY KEY AUTOINCREMENT, company_id INT NOT NULL,
          label TEXT NOT NULL, slug_hash TEXT NOT NULL, slug_prefix TEXT NOT NULL,
          slug_encrypted TEXT NOT NULL, install_token_id INT,
          install_token_encrypted TEXT NOT NULL, created_by_user_id INT,
          created_at DATETIME DEFAULT CURRENT_TIMESTAMP, expires_at DATETIME,
          revoked_at DATETIME, download_count INT DEFAULT 0, last_downloaded_at DATETIME);
        CREATE TABLE tray_deployment_builds (
          id INTEGER PRIMARY KEY AUTOINCREMENT, deployment_link_id INT NOT NULL,
          status VARCHAR(16) NOT NULL DEFAULT 'queued', release_tag TEXT,
          error TEXT, msi_sha256 TEXT, exe_sha256 TEXT,
          requested_at DATETIME DEFAULT CURRENT_TIMESTAMP,
          claimed_at DATETIME, completed_at DATETIME);
        INSERT INTO tray_install_tokens (id, company_id, label, token_hash, token_prefix)
          VALUES (11, 3, 'Deployment URL: Acme', 'h', 'p');
        """
    )
    await conn.execute(
        "INSERT INTO tray_deployment_links (id, company_id, label, slug_hash, slug_prefix, "
        "slug_encrypted, install_token_id, install_token_encrypted) VALUES (7, 3, 'Acme', 's', 's', ?, 11, ?)",
        (encrypt_secret("slug"), encrypt_secret(TOKEN)),
    )
    await conn.commit()
    monkeypatch.setattr(db, "_use_sqlite", True)
    monkeypatch.setattr(db, "_sqlite_conn", conn)
    monkeypatch.setattr(builds, "_STORAGE_DIR", tmp_path / "tray-builds")
    monkeypatch.setattr(
        tray_deployment.companies_repo,
        "get_company_by_id",
        AsyncMock(return_value={"id": 3, "name": "Acme"}),
    )
    monkeypatch.setattr(
        builds.tray_installer,
        "get_cached_latest_release_info",
        lambda: {"release_tag": "v1.4.2"},
    )
    yield conn
    await conn.close()


async def _chunks(*parts: bytes):
    for part in parts:
        yield part


async def _status(build_id: int) -> str:
    return (await tray_repo.get_deployment_build(build_id))["status"]


@pytest.mark.parametrize(
    "tag, expected",
    [("v1.4.2", "1.4.2"), ("1.5", "1.5.0"), ("v2.0.1-beta.3", "2.0.1"), ("nightly", "0.0.0")],
)
def test_product_version_matches_ci(tag, expected):
    assert builds.product_version(tag) == expected


@pytest.mark.anyio
async def test_claim_returns_job_once(sqlite_db):
    build_id = await builds.queue_build(7)
    assert await builds.queue_build(7) == build_id

    job = await builds.claim_next_build(PORTAL)

    assert job == {
        "id": build_id,
        "deployment_link_id": 7,
        "company_name": "Acme",
        "release_tag": "v1.4.2",
        "product_version": "1.4.2",
        "portal_url": PORTAL,
        "enrol_token": TOKEN,
    }
    assert await _status(build_id) == "building"
    assert await builds.claim_next_build(PORTAL) is None


@pytest.mark.anyio
async def test_claim_needs_cached_release(sqlite_db, monkeypatch):
    await builds.queue_build(7)
    monkeypatch.setattr(builds.tray_installer, "get_cached_latest_release_info", lambda: {})
    assert await builds.claim_next_build(PORTAL) is None


@pytest.mark.anyio
async def test_upload_and_complete_supersedes_old_build(sqlite_db):
    first = await builds.queue_build(7)
    await builds.claim_next_build(PORTAL)
    for kind in builds.ARTIFACT_KINDS:
        await builds.save_artifact(first, kind, _chunks(b"old-", kind.encode()))
    await builds.complete_build(first)
    assert await _status(first) == "ready"
    assert (await builds.ready_artifact(7, "msi")).read_bytes() == b"old-msi"

    second = await builds.queue_build(7)
    await builds.claim_next_build(PORTAL)
    sha = await builds.save_artifact(second, "exe", _chunks(b"new-", b"exe"))
    await builds.save_artifact(second, "msi", _chunks(b"new-msi"))
    await builds.complete_build(second)

    assert len(sha) == 64
    assert await _status(first) == "superseded"
    assert not builds.build_dir(first).exists()
    assert (await builds.ready_artifact(7, "exe")).read_bytes() == b"new-exe"


@pytest.mark.anyio
async def test_complete_requires_both_artifacts(sqlite_db):
    build_id = await builds.queue_build(7)
    await builds.claim_next_build(PORTAL)
    await builds.save_artifact(build_id, "msi", _chunks(b"msi"))
    with pytest.raises(builds.BuildStateError):
        await builds.complete_build(build_id)


@pytest.mark.anyio
async def test_upload_rejected_unless_building(sqlite_db):
    build_id = await builds.queue_build(7)
    with pytest.raises(builds.BuildStateError):
        await builds.save_artifact(build_id, "msi", _chunks(b"msi"))
    with pytest.raises(builds.BuildNotFound):
        await builds.save_artifact(999, "msi", _chunks(b"msi"))


@pytest.mark.anyio
async def test_oversized_upload_is_rejected(sqlite_db, monkeypatch):
    monkeypatch.setattr(builds, "MAX_ARTIFACT_BYTES", 4)
    build_id = await builds.queue_build(7)
    await builds.claim_next_build(PORTAL)
    with pytest.raises(builds.BuildStateError):
        await builds.save_artifact(build_id, "exe", _chunks(b"abc", b"def"))
    assert not builds.artifact_path(build_id, "exe").exists()


@pytest.mark.anyio
async def test_new_release_queues_rebuild_but_failures_are_not_retried(sqlite_db, monkeypatch):
    build_id = await builds.queue_build(7)
    await builds.claim_next_build(PORTAL)
    await builds.fail_build(build_id, "signing failed")
    assert (await tray_repo.get_deployment_build(build_id))["error"] == "signing failed"

    # Same release: the failure is left for an admin to retry.
    assert await builds.claim_next_build(PORTAL) is None

    monkeypatch.setattr(
        builds.tray_installer,
        "get_cached_latest_release_info",
        lambda: {"release_tag": "v1.5.0"},
    )
    job = await builds.claim_next_build(PORTAL)
    assert job is not None
    assert job["release_tag"] == "v1.5.0"
    assert job["id"] != build_id


@pytest.mark.anyio
async def test_revoked_token_fails_build(sqlite_db):
    build_id = await builds.queue_build(7)
    await tray_repo.revoke_install_token(11)
    assert await builds.claim_next_build(PORTAL) is None
    assert await _status(build_id) == "failed"


@pytest.mark.anyio
async def test_expired_link_is_not_rebuilt(sqlite_db, monkeypatch):
    build_id = await builds.queue_build(7)
    await builds.claim_next_build(PORTAL)
    for kind in builds.ARTIFACT_KINDS:
        await builds.save_artifact(build_id, kind, _chunks(kind.encode()))
    await builds.complete_build(build_id)
    await sqlite_db.execute(
        "UPDATE tray_deployment_links SET expires_at = ? WHERE id = 7",
        (datetime.utcnow() - timedelta(minutes=1),),
    )
    await sqlite_db.commit()
    monkeypatch.setattr(
        builds.tray_installer,
        "get_cached_latest_release_info",
        lambda: {"release_tag": "v1.5.0"},
    )
    assert await builds.claim_next_build(PORTAL) is None
    assert await tray_repo.list_deployment_builds(7) == [
        await tray_repo.get_deployment_build(build_id)
    ]


@pytest.mark.anyio
async def test_stale_build_is_handed_out_again(sqlite_db):
    build_id = await builds.queue_build(7)
    await builds.claim_next_build(PORTAL)
    old = datetime.utcnow() - builds.BUILD_LEASE - timedelta(minutes=5)
    await sqlite_db.execute(
        "UPDATE tray_deployment_builds SET claimed_at = ? WHERE id = ?", (old, build_id)
    )
    await sqlite_db.commit()
    job = await builds.claim_next_build(PORTAL)
    assert job is not None and job["id"] == build_id


def _request() -> Request:
    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": b"", "more_body": False}

    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/tray/build-agent/jobs/claim",
            "query_string": b"",
            "headers": [],
            "scheme": "https",
            "server": ("portal.example.com", 443),
        },
        receive,
    )


@pytest.mark.anyio
async def test_claim_route_returns_204_when_idle(monkeypatch):
    monkeypatch.setattr(builds, "claim_next_build", AsyncMock(return_value=None))
    response = await deploy_routes.build_agent_claim(_request(), _api_key={})
    assert response.status_code == 204


@pytest.mark.anyio
async def test_complete_route_maps_state_errors(monkeypatch):
    monkeypatch.setattr(
        builds, "complete_build", AsyncMock(side_effect=builds.BuildStateError("nope"))
    )
    with pytest.raises(HTTPException) as excinfo:
        await deploy_routes.build_agent_complete(5, _api_key={})
    assert excinfo.value.status_code == 409


@pytest.fixture
def portal_url(monkeypatch):
    settings = SimpleNamespace(portal_url=PORTAL + "/")
    monkeypatch.setattr(tray_deployment, "get_settings", lambda: settings)
    return settings


async def _add_link(conn, link_id: int, slug: str, expires_at: datetime | None) -> None:
    await conn.execute(
        "INSERT INTO tray_install_tokens (id, company_id, label, token_hash, token_prefix, expires_at) "
        "VALUES (?, 3, 'Deployment URL', 'h', 'p', ?)",
        (link_id + 100, expires_at),
    )
    await conn.execute(
        "INSERT INTO tray_deployment_links (id, company_id, label, slug_hash, slug_prefix, "
        "slug_encrypted, install_token_id, install_token_encrypted, expires_at) "
        "VALUES (?, 3, 'Acme', ?, 's', ?, ?, ?, ?)",
        (link_id, slug, encrypt_secret(slug), link_id + 100, encrypt_secret(TOKEN), expires_at),
    )
    await conn.commit()


@pytest.mark.anyio
async def test_company_deployment_url_prefers_link_that_lasts_longest(sqlite_db, portal_url):
    soon = datetime.utcnow() + timedelta(days=1)
    await _add_link(sqlite_db, 8, "short-lived", soon)
    await _add_link(sqlite_db, 9, "one-year", datetime.utcnow() + timedelta(days=365))

    # Link 7 never expires, so it wins over newer links that do.
    assert await tray_deployment.company_deployment_url(3) == PORTAL + "/deploy/slug"

    await tray_deployment.revoke_deployment_link(7)
    assert await tray_deployment.company_deployment_url(3) == PORTAL + "/deploy/one-year"

    await tray_repo.revoke_install_token(109)
    assert await tray_deployment.company_deployment_url(3) == PORTAL + "/deploy/short-lived"

    assert await tray_deployment.company_deployment_url(4) == ""
    assert await tray_deployment.company_deployment_url(None) == ""
    portal_url.portal_url = None
    assert await tray_deployment.company_deployment_url(3) == ""


@pytest.mark.anyio
async def test_deployment_url_variable_in_automations_and_templates(
    sqlite_db, portal_url, monkeypatch
):
    expected = PORTAL + "/deploy/slug"
    rendered = await value_templates.render_string_async(
        "Install: {{ tray.deploymentUrl }} {{TRAY_DEPLOYMENT_URL}}",
        {"ticket": {"company_id": 3}},
        include_templates=False,
    )
    assert rendered == f"Install: {expected} {expected}"

    monkeypatch.setattr(message_templates, "get_template_by_slug", AsyncMock(return_value=None))
    body, _ = await message_templates.render_template_content(
        "welcome",
        {"company": {"id": 3, "name": "Acme"}},
        default_content="Hi {{ company.name }}, install from {{ tray.deploymentUrl }}",
        default_content_type="text/plain",
    )
    assert body == f"Hi Acme, install from {expected}"

    context = {"ticket": {"company_id": 3}}
    assert await message_templates.with_tray_deployment_url("No variable", context) is context
