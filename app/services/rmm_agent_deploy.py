"""Push the RMM agent to devices from MyPortal.

The tray app is the channel: it already runs as LocalSystem/root on every
managed Windows and macOS device and holds the device token the agent enrols
with. A push queues an ``install_rmm_agent`` tray command (see
:mod:`app.repositories.rmm_agent_deploy`); the tray app then

1. asks ``GET /api/rmm/agent/package`` for the agent build for its platform,
2. downloads it from ``/api/rmm/agent/package/download`` and checks its SHA-256,
3. runs ``myportal-rmm enrol`` with its own token and ``myportal-rmm install``,
4. reports the outcome to ``POST /api/rmm/agent/install-result``.

Technicians push to one device from the Assets page. With a company's
``rmm_agent_auto_deploy`` setting on, every tray device of that company
without an agent gets a push when the setting is switched on and whenever the
device connects, at most once every :data:`AUTO_RETRY_HOURS`.

Agent builds come from the GitHub release the ``Build RMM agent`` workflow
uploads them to (``GITHUB_TRAY_MSI_REPO``) and are kept in
``private_uploads/rmm-agent``, served only to enrolled tray devices.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx

from app.core.config import get_settings
from app.core.logging import log_error, log_info, log_warning
from app.repositories import companies as companies_repo
from app.repositories import rmm_agent_deploy as deploy_repo
from app.services import tray as tray_service
from app.services import tray_installer
from app.services.monitored_http import monitored_client

INSTALL_COMMAND = deploy_repo.INSTALL_COMMAND
# The tray app runs on Windows and macOS only.
TRAY_PLATFORMS = ("windows", "darwin")
PLATFORMS = (
    ("windows", "amd64"),
    ("windows", "arm64"),
    ("darwin", "amd64"),
    ("darwin", "arm64"),
    ("linux", "amd64"),
    ("linux", "arm64"),
)
AUTO_RETRY_HOURS = 24
MAX_ERROR_LENGTH = 2000

_STORAGE_DIR = Path(__file__).resolve().parents[2] / "private_uploads" / "rmm-agent"
_FETCH_LOCK = asyncio.Lock()
_CHUNK_SIZE = 65536


class AgentDeployError(Exception):
    """A push that cannot be made; the message is shown to the technician."""


def file_name(os_name: str, arch: str) -> str:
    return f"myportal-rmm-{os_name}-{arch}" + (".exe" if os_name == "windows" else "")


# Paths are built only from these names, never from what a device sent.
_FILE_NAMES = {(os_name, arch): file_name(os_name, arch) for os_name, arch in PLATFORMS}


def _valid_platform(os_name: str, arch: str) -> bool:
    return (os_name, arch) in _FILE_NAMES


def _metadata_path(name: str) -> Path:
    return _STORAGE_DIR / f"{name}.json"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(_CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def cached_package(os_name: str, arch: str) -> dict[str, Any] | None:
    """The cached agent build for a platform: ``path``, ``sha256``, ``version`` and ``size``."""

    name = _FILE_NAMES.get((os_name, arch))
    if name is None:
        return None
    path = _STORAGE_DIR / name
    try:
        metadata = json.loads(_metadata_path(name).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not path.is_file() or not metadata.get("sha256"):
        return None
    return {
        "file_name": name,
        "path": path,
        "sha256": str(metadata["sha256"]).lower(),
        "version": metadata.get("version"),
        "size": path.stat().st_size,
    }


def has_any_package(os_name: str) -> bool:
    return any(cached_package(os_name, arch) for platform_os, arch in PLATFORMS if platform_os == os_name)


def _release_assets(release: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {str(asset.get("name")): asset for asset in release.get("assets") or [] if asset.get("name")}


async def _find_agent_release(client: httpx.AsyncClient, repo: str, headers: dict[str, str]) -> dict[str, Any] | None:
    """The newest published release carrying agent builds.

    The agent is uploaded to the same releases as the tray installers, but a
    release can be published before (or without) its agent upload, so look a
    few releases back rather than only at the latest.
    """

    response = await client.get(
        f"https://api.github.com/repos/{repo}/releases", params={"per_page": 10}, headers=headers, timeout=15.0
    )
    if response.status_code in (403, 404, 429):
        log_warning("Could not list releases for the RMM agent", repo=repo, status=response.status_code)
        return None
    response.raise_for_status()
    for release in response.json() or []:
        if release.get("draft") or release.get("prerelease"):
            continue
        names = _release_assets(release)
        if any(file_name(os_name, arch) in names for os_name, arch in PLATFORMS):
            return release
    return None


async def _download(client: httpx.AsyncClient, url: str, headers: dict[str, str], dest: Path) -> str:
    download_headers = {**headers, "Accept": "application/octet-stream"}
    tmp = dest.with_name(dest.name + ".tmp")
    digest = hashlib.sha256()
    try:
        async with client.stream("GET", url, headers=download_headers, timeout=120.0) as response:
            response.raise_for_status()
            with tmp.open("wb") as fh:
                async for chunk in response.aiter_bytes(chunk_size=_CHUNK_SIZE):
                    digest.update(chunk)
                    fh.write(chunk)
        return digest.hexdigest()
    except Exception:
        tmp.unlink(missing_ok=True)
        raise


async def _published_sha256(client: httpx.AsyncClient, asset: dict[str, Any] | None, headers: dict[str, str]) -> str | None:
    if not asset or not asset.get("browser_download_url"):
        return None
    response = await client.get(
        asset["browser_download_url"], headers={**headers, "Accept": "application/octet-stream"}, timeout=30.0
    )
    response.raise_for_status()
    text = response.text.strip()
    return text.split()[0].lower() if text else None


async def fetch_latest_agent(*, repo: str | None = None, github_token: str | None = None, force: bool = False) -> dict[str, bool]:
    """Cache the newest agent builds; returns which files were updated."""

    settings = get_settings()
    repo = repo or settings.github_tray_msi_repo
    github_token = github_token if github_token is not None else settings.github_token
    results = {file_name(os_name, arch): False for os_name, arch in PLATFORMS}
    async with _FETCH_LOCK:
        _STORAGE_DIR.mkdir(parents=True, exist_ok=True)
        headers = tray_installer._build_headers(github_token)
        async with monitored_client(httpx.AsyncClient, follow_redirects=True) as client:
            release = await _find_agent_release(client, repo, headers)
            if release is None:
                log_warning("No release carries the RMM agent yet", repo=repo)
                return results
            assets = _release_assets(release)
            version = tray_installer._parse_release_version(release.get("tag_name"))
            for name in results:
                asset = assets.get(name)
                if not asset or not asset.get("browser_download_url"):
                    continue
                marker = {"release_id": release.get("id"), "asset_id": asset.get("id"), "asset_updated_at": asset.get("updated_at")}
                try:
                    cached = json.loads(_metadata_path(name).read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    cached = {}
                dest = _STORAGE_DIR / name
                if not force and dest.is_file() and all(cached.get(key) == value for key, value in marker.items()):
                    continue
                try:
                    actual = await _download(client, asset["browser_download_url"], headers, dest)
                    expected = await _published_sha256(client, assets.get(name + ".sha256"), headers)
                    if expected and expected != actual:
                        raise ValueError(f"SHA-256 mismatch for {name}")
                    dest.with_name(dest.name + ".tmp").replace(dest)
                except Exception as exc:  # noqa: BLE001 - one platform failing leaves the rest usable
                    dest.with_name(dest.name + ".tmp").unlink(missing_ok=True)
                    log_error("Failed to download the RMM agent", repo=repo, asset=name, error=str(exc))
                    continue
                _metadata_path(name).write_text(
                    json.dumps({**marker, "version": version, "sha256": actual}, sort_keys=True), encoding="utf-8"
                )
                results[name] = True
    if any(results.values()):
        log_info("RMM agent builds updated", repo=repo, updated=[name for name, done in results.items() if done])
    return results


async def ensure_package(os_name: str, arch: str) -> dict[str, Any] | None:
    package = cached_package(os_name, arch)
    if package is None and _valid_platform(os_name, arch):
        try:
            await fetch_latest_agent()
        except Exception as exc:  # noqa: BLE001 - reported as "not available" to the caller
            log_error("Failed to fetch the RMM agent", error=str(exc))
        package = cached_package(os_name, arch)
    return package


# --------------------------------------------------------------------------- #
# Pushing
# --------------------------------------------------------------------------- #


def _as_utc(value: Any) -> datetime | None:
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


async def _send(device: dict[str, Any], command_id: int) -> bool:
    delivered = await tray_service.send_to_device(
        str(device.get("device_uid") or ""), {"type": INSTALL_COMMAND, "command_id": command_id}
    )
    if delivered:
        await deploy_repo.set_command_status(command_id, "delivered")
    return delivered


async def push_to_device(device: dict[str, Any], *, user_id: int | None, send: bool = True) -> dict[str, Any]:
    """Queue (and try to deliver) an agent install on one tray device.

    Returns ``{"status": ..., "command_id": ...}`` where status is
    ``installed`` (nothing to do), ``sent`` (reached the device now) or
    ``queued`` (the device collects it when it next connects).
    """

    if str(device.get("status") or "") != "active":
        raise AgentDeployError("The tray app on this device is not active.")
    os_name = str(device.get("os") or "").lower()
    if os_name not in TRAY_PLATFORMS:
        raise AgentDeployError("The RMM agent can only be pushed to Windows and macOS devices.")
    if await deploy_repo.device_has_agent(device):
        return {"status": "installed", "command_id": None}
    if not has_any_package(os_name):
        await ensure_package(os_name, "amd64")
        if not has_any_package(os_name):
            raise AgentDeployError(
                "MyPortal does not have the RMM agent yet: no published release carries the agent builds."
            )

    latest = await deploy_repo.latest_install_command(int(device["id"]))
    if latest and latest.get("status") == "queued":
        command_id = int(latest["id"])
    else:
        command_id = await deploy_repo.create_install_command(
            int(device["id"]), user_id=user_id, payload_json=json.dumps({"type": INSTALL_COMMAND})
        )
    delivered = await _send(device, command_id) if send else False
    log_info(
        "Queued RMM agent install", device_id=device.get("id"), command_id=command_id, delivered=delivered, user_id=user_id
    )
    return {"status": "sent" if delivered else "queued", "command_id": command_id}


async def push_to_asset(asset_id: int, *, company_id: int, user_id: int | None) -> dict[str, Any]:
    device = await deploy_repo.get_asset_tray_device(asset_id)
    if not device or device.get("company_id") is None or int(device["company_id"]) != int(company_id):
        raise AgentDeployError(
            "This device has no MyPortal tray app, which installs the RMM agent. Install the tray app first."
        )
    return await push_to_device(device, user_id=user_id)


async def _auto_enabled(company_id: Any) -> bool:
    if company_id is None:
        return False
    company = await companies_repo.get_company_by_id(int(company_id))
    return bool(company and company.get("rmm_agent_auto_deploy"))


async def _recently_tried(device_id: int, now: datetime) -> bool:
    latest = await deploy_repo.latest_install_command(device_id)
    if not latest:
        return False
    if latest.get("status") == "queued":
        return True
    created = _as_utc(latest.get("created_at"))
    return created is not None and now - created < timedelta(hours=AUTO_RETRY_HOURS)


async def auto_deploy_device(device: dict[str, Any], *, now: datetime | None = None) -> bool:
    """Queue an install for a connecting device when its company deploys automatically.

    Only queues: the caller's queued-command drain delivers it. Returns True
    when a command was queued.
    """

    if str(device.get("os") or "").lower() not in TRAY_PLATFORMS or str(device.get("status") or "") != "active":
        return False
    if not await _auto_enabled(device.get("company_id")):
        return False
    if await deploy_repo.device_has_agent(device):
        return False
    if await _recently_tried(int(device["id"]), now or datetime.now(timezone.utc)):
        return False
    try:
        await push_to_device(device, user_id=None, send=False)
    except AgentDeployError as exc:
        log_warning("Automatic RMM agent install skipped", device_id=device.get("id"), reason=str(exc))
        return False
    return True


async def auto_deploy_company(company_id: int, *, user_id: int | None = None) -> dict[str, int]:
    """Push the agent to every device of a company that does not have it."""

    counts = {"sent": 0, "queued": 0, "skipped": 0}
    for device in await deploy_repo.list_company_devices_without_agent(company_id):
        if str(device.get("os") or "").lower() not in TRAY_PLATFORMS:
            counts["skipped"] += 1
            continue
        try:
            result = await push_to_device(device, user_id=user_id)
        except AgentDeployError as exc:
            log_warning("RMM agent push skipped", device_id=device.get("id"), reason=str(exc))
            counts["skipped"] += 1
            continue
        counts[result["status"]] = counts.get(result["status"], 0) + 1
    log_info("Pushed the RMM agent to a company", company_id=company_id, **counts)
    return counts


async def record_result(device: dict[str, Any], command_id: int, *, ok: bool, message: str | None) -> bool:
    """Store what the tray app reported for an install command it was sent."""

    command = await deploy_repo.get_install_command(command_id, int(device["id"]))
    if not command:
        return False
    error = None if ok else ((message or "").strip() or "The tray app could not install the RMM agent.")[:MAX_ERROR_LENGTH]
    await deploy_repo.set_command_status(command_id, "completed" if ok else "error", error=error)
    log_info("RMM agent install reported", device_id=device.get("id"), command_id=command_id, ok=ok)
    return True


async def asset_deploy_state(asset_id: int, company_id: int) -> dict[str, Any] | None:
    """The asset page's view of the latest push, or None without an active tray device."""

    device = await deploy_repo.get_asset_tray_device(asset_id)
    if not device or device.get("company_id") is None or int(device["company_id"]) != int(company_id):
        return None
    latest = await deploy_repo.latest_install_command(int(device["id"]))
    supported = str(device.get("os") or "").lower() in TRAY_PLATFORMS
    return {
        "supported": supported,
        "status": latest.get("status") if latest else None,
        "error": latest.get("error") if latest else None,
        "requested_at": latest.get("created_at") if latest else None,
    }
