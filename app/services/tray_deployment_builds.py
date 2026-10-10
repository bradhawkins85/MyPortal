"""Company-specific Windows tray installers built by a Windows build agent.

WiX cannot build MSI or Burn bundles on Linux, so MyPortal queues builds and a
Windows machine running ``tray/build-agent/MyPortalBuildAgent.ps1`` does the
work.  The agent authenticates with a MyPortal API key and:

1. claims the next job, receiving the release tag, portal URL and the
   deployment link's install token;
2. builds and signs ``myportal-tray.msi`` with those values as property
   defaults, then wraps it in a signed ``setup.exe`` Burn bundle;
3. uploads both files and marks the job complete (or reports a failure).

A build is queued when a deployment link is created, when an admin asks for a
rebuild, and automatically when the server caches a newer tray release than
the one a link's installers were built from.  Finished files live under
``private_uploads/tray-builds/<build id>/`` and are only served through the
link's ``/deploy`` URL.
"""

from __future__ import annotations

import hashlib
import re
import shutil
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, AsyncIterator

from app.core.logging import log_info, log_warning
from app.repositories import tray as tray_repo
from app.security.encryption import decrypt_secret
from app.services import tray_deployment
from app.services import tray_installer

ARTIFACT_KINDS = ("msi", "exe")
MAX_ARTIFACT_BYTES = 1024 * 1024 * 1024
BUILD_LEASE = timedelta(hours=2)

_STORAGE_DIR = Path(__file__).resolve().parents[2] / "private_uploads" / "tray-builds"
_RELEASE_TAG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/+-]{0,127}$")


class BuildNotFound(Exception):
    pass


class BuildStateError(Exception):
    pass


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _stale_before() -> datetime:
    return _now() - BUILD_LEASE


def build_dir(build_id: int) -> Path:
    return _STORAGE_DIR / str(int(build_id))


def artifact_path(build_id: int, kind: str) -> Path:
    if kind not in ARTIFACT_KINDS:
        raise ValueError("Unknown artifact kind")
    return build_dir(build_id) / ("myportal-tray." + kind)


def product_version(release_tag: str) -> str:
    """Return the three-part MSI version for a release tag, as the CI build does."""

    cleaned = re.sub(r"^v", "", release_tag or "")
    cleaned = re.sub(r"[^0-9.].*$", "", cleaned)
    parts = [int(p) if p.isdigit() else 0 for p in cleaned.split(".") if p != ""] if cleaned else []
    while len(parts) < 3:
        parts.append(0)
    return ".".join(str(p) for p in parts[:3])


def current_release_tag() -> str | None:
    tag = tray_installer.get_cached_latest_release_info().get("release_tag")
    tag = str(tag or "").strip()
    return tag if _RELEASE_TAG_RE.match(tag) else None


async def queue_build(link_id: int) -> int:
    """Queue a build for ``link_id`` unless one is already waiting or running."""

    for build in await tray_repo.list_deployment_builds(int(link_id)):
        if build.get("status") in ("queued", "building"):
            return int(build["id"])
    return await tray_repo.create_deployment_build(int(link_id))


async def queue_outdated_builds(release_tag: str) -> int:
    """Queue builds for active links whose installers predate ``release_tag``."""

    builds = await tray_repo.list_deployment_builds()
    newest: dict[int, dict[str, Any]] = {}
    latest_ready: dict[int, dict[str, Any]] = {}
    pending: set[int] = set()
    for build in builds:
        link_id = int(build["deployment_link_id"])
        newest.setdefault(link_id, build)
        if build.get("status") in ("queued", "building"):
            pending.add(link_id)
        if build.get("status") == "ready":
            latest_ready.setdefault(link_id, build)
    queued = 0
    for link in await tray_repo.list_active_deployment_links():
        link_id = int(link["id"])
        if link_id in pending:
            continue
        last = newest.get(link_id)
        if last and last.get("status") == "failed" and last.get("release_tag") == release_tag:
            # Retrying the same release would fail the same way; an admin
            # can still queue a rebuild from the Deployment URLs page.
            continue
        ready = latest_ready.get(link_id)
        if ready is None or ready.get("release_tag") != release_tag:
            await tray_repo.create_deployment_build(link_id)
            queued += 1
    return queued


async def claim_next_build(portal_url: str) -> dict[str, Any] | None:
    """Claim the next build for the agent and return its job description."""

    release_tag = current_release_tag()
    if release_tag is None:
        return None
    if not tray_deployment._PORTAL_URL_RE.match(portal_url or ""):
        raise ValueError("Portal URL contains characters that cannot be used in an installer")
    if await tray_repo.get_next_claimable_deployment_build(_stale_before()) is None:
        if await queue_outdated_builds(release_tag):
            log_info("Queued tray deployment builds for new release", release_tag=release_tag)

    while True:
        build = await tray_repo.get_next_claimable_deployment_build(_stale_before())
        if build is None:
            return None
        build_id = int(build["id"])
        if not await tray_repo.claim_deployment_build(
            build_id, release_tag=release_tag, stale_before=_stale_before()
        ):
            continue
        link = await tray_repo.get_deployment_link(int(build["deployment_link_id"]))
        token = await _usable_link_token(link)
        if link is None or token is None:
            await tray_repo.fail_deployment_build(
                build_id, error="The deployment URL or its install token was revoked."
            )
            continue
        company = await tray_deployment.companies_repo.get_company_by_id(int(link["company_id"]))
        return {
            "id": build_id,
            "deployment_link_id": int(link["id"]),
            "company_name": (company or {}).get("name") or "",
            "release_tag": release_tag,
            "product_version": product_version(release_tag),
            "portal_url": portal_url,
            "enrol_token": token,
        }


async def idle_reason() -> str:
    """Explain why :func:`claim_next_build` has nothing for the agent.

    Sent to the build agent with an empty claim so its log says what to fix
    rather than staying silent.
    """

    release_tag = current_release_tag()
    if release_tag is None:
        return (
            "No tray release is cached on MyPortal. Fetch the latest tray release "
            "from Admin > Tray > Devices."
        )
    links = await tray_repo.list_active_deployment_links()
    if not links:
        return "There are no active deployment URLs. Create one under Admin > Tray > Deployment URLs."
    active_ids = {int(link["id"]) for link in links}
    newest: dict[int, dict[str, Any]] = {}
    for build in await tray_repo.list_deployment_builds():
        link_id = int(build["deployment_link_id"])
        if link_id in active_ids and build.get("status") != "superseded":
            newest.setdefault(link_id, build)
    building = [b for b in newest.values() if b.get("status") == "building"]
    if building:
        return (
            f"{len(building)} build(s) are already in progress. A build that does not "
            f"report back is handed out again after {int(BUILD_LEASE.total_seconds() // 3600)} hours."
        )
    failed = [
        b
        for b in newest.values()
        if b.get("status") == "failed" and b.get("release_tag") == release_tag
    ]
    if failed:
        return (
            f"{len(failed)} deployment URL(s) failed to build {release_tag} and are not retried "
            "automatically. Fix the error shown on Admin > Tray > Deployment URLs, then press Rebuild."
        )
    return f"All active deployment URLs have installers for {release_tag}."


async def _usable_link_token(link: dict[str, Any] | None) -> str | None:
    if not link or link.get("revoked_at") or not link.get("install_token_id"):
        return None
    token = await tray_repo.get_install_token_by_id(int(link["install_token_id"]))
    if not tray_deployment._token_is_usable(token):
        return None
    try:
        return decrypt_secret(str(link.get("install_token_encrypted") or ""), allow_plaintext=False)
    except Exception:
        return None


async def _building(build_id: int) -> dict[str, Any]:
    build = await tray_repo.get_deployment_build(int(build_id))
    if build is None:
        raise BuildNotFound()
    if build.get("status") != "building":
        raise BuildStateError("This build is not in progress.")
    return build


async def save_artifact(build_id: int, kind: str, chunks: AsyncIterator[bytes]) -> str:
    """Stream an uploaded artifact to disk and return its SHA-256."""

    await _building(build_id)
    target = artifact_path(build_id, kind)
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(target.suffix + ".part")
    digest = hashlib.sha256()
    size = 0
    try:
        with partial.open("wb") as handle:
            async for chunk in chunks:
                size += len(chunk)
                if size > MAX_ARTIFACT_BYTES:
                    raise BuildStateError("The uploaded file is too large.")
                digest.update(chunk)
                handle.write(chunk)
        if size == 0:
            raise BuildStateError("The uploaded file is empty.")
        partial.replace(target)
    finally:
        partial.unlink(missing_ok=True)
    return digest.hexdigest()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


async def complete_build(build_id: int) -> None:
    build = await _building(build_id)
    missing = [kind for kind in ARTIFACT_KINDS if not artifact_path(build_id, kind).is_file()]
    if missing:
        raise BuildStateError("Upload the " + " and ".join(missing) + " before completing.")
    await tray_repo.complete_deployment_build(
        int(build_id),
        msi_sha256=_sha256(artifact_path(build_id, "msi")),
        exe_sha256=_sha256(artifact_path(build_id, "exe")),
    )
    old_ids = await tray_repo.supersede_deployment_builds(
        int(build["deployment_link_id"]), keep_build_id=int(build_id)
    )
    for old_id in old_ids:
        shutil.rmtree(build_dir(old_id), ignore_errors=True)
    log_info(
        "Tray deployment build completed",
        build_id=build_id,
        deployment_link_id=build.get("deployment_link_id"),
        release_tag=build.get("release_tag"),
    )


async def fail_build(build_id: int, error: str) -> None:
    await _building(build_id)
    message = (error or "").strip()[:2000] or "The build agent reported a failure."
    await tray_repo.fail_deployment_build(int(build_id), error=message)
    shutil.rmtree(build_dir(build_id), ignore_errors=True)
    log_warning("Tray deployment build failed", build_id=build_id, error=message)


async def ready_artifact(link_id: int, kind: str) -> Path | None:
    build = await tray_repo.get_latest_ready_deployment_build(int(link_id))
    if build is None:
        return None
    path = artifact_path(int(build["id"]), kind)
    return path if path.is_file() else None


async def latest_build_by_link() -> dict[int, dict[str, Any]]:
    """Return each link's most useful build: pending first, else the newest."""

    summary: dict[int, dict[str, Any]] = {}
    for build in await tray_repo.list_deployment_builds():
        link_id = int(build["deployment_link_id"])
        if build.get("status") == "superseded":
            continue
        if link_id not in summary:
            summary[link_id] = build
    return summary


async def purge_link_artifacts(link_id: int) -> None:
    """Delete stored installers for a revoked deployment link."""

    for build in await tray_repo.list_deployment_builds(int(link_id)):
        shutil.rmtree(build_dir(int(build["id"])), ignore_errors=True)
