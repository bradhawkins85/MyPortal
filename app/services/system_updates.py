"""Check for and request MyPortal upgrades from the administrator web UI.

The web application never upgrades itself and never holds root, sudo or the
Docker socket.  It only records a request in ``var/state/system_update.flag``
and a pending entry in the update history.  A root job on the host claims the
request and runs the existing upgrade tooling:

* bare metal: ``scripts/process_update_flag.sh`` (root cron) runs
  ``scripts/upgrade.sh`` against ``origin/main``;
* Docker: ``myportal-docker process-requests`` (root cron) reads the request
  through ``docker exec`` and runs ``myportal-docker upgrade`` to the
  latest published GitHub release.

Neither host job takes a target, mode or command from the request beyond the
identifier used to report progress, so a compromised application can at most
ask for the same upgrade the nightly job would already apply.
"""

from __future__ import annotations

import asyncio
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from app.core.logging import log_error, log_info
from app.services import system_update_history

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_FLAG_PATH = _PROJECT_ROOT / "var" / "state" / "system_update.flag"
_CHECK_TIMEOUT_SECONDS = 20
_CHECK_CACHE_SECONDS = 600
# Both host jobs poll every minute, so a request nobody claimed in this long
# means the host job is not installed; withdraw it rather than leave it queued.
_UNCLAIMED_REQUEST_SECONDS = 15 * 60
_TAG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")

_check_cache: dict[str, Any] = {"at": 0.0, "value": None}


def deployment_type() -> str:
    if os.getenv("MYPORTAL_DEPLOYMENT", "").strip().lower() == "docker":
        return "docker"
    return "baremetal"


def _installed_version() -> str:
    try:
        return (_PROJECT_ROOT / "version.txt").read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _version_key(value: str) -> tuple[int, ...]:
    return tuple(int(part) for part in re.findall(r"\d+", value))


def _version_newer(candidate: str, current: str) -> bool:
    """Mirror ``version_newer`` in myportal-docker.sh (``sort -V`` ordering)."""
    if not candidate or candidate == current:
        return False
    if not current:
        return True
    return _version_key(candidate.lstrip("vV")) > _version_key(current.lstrip("vV"))


async def _latest_release_tag() -> str:
    # .env.example ships these blank; blank means "use the default".
    repo = os.getenv("MYPORTAL_REPO", "").strip() or "bradhawkins85/MyPortal"
    api = (os.getenv("MYPORTAL_GITHUB_API", "").strip() or "https://api.github.com").rstrip("/")
    async with httpx.AsyncClient(timeout=_CHECK_TIMEOUT_SECONDS) as client:
        response = await client.get(
            f"{api}/repos/{repo}/releases/latest",
            headers={"Accept": "application/vnd.github+json"},
        )
        response.raise_for_status()
        tag = str(response.json().get("tag_name") or "")
    if not _TAG_RE.fullmatch(tag):
        raise RuntimeError("GitHub did not report a valid latest release.")
    return tag


async def _check_docker() -> dict[str, Any]:
    installed = _installed_version()
    latest = await _latest_release_tag()
    return {
        "installed": installed,
        "latest": latest,
        "available": _version_newer(latest, installed),
    }


async def _check_baremetal() -> dict[str, Any]:
    from app.services.scheduler import scheduler_service

    installed = await scheduler_service._get_git_ref("HEAD") or ""
    if not installed:
        raise RuntimeError(
            "the installed revision is unknown (version.txt does not hold a Git "
            "revision and the control checkout could not be read)"
        )
    latest = await scheduler_service._get_remote_main_ref() or ""
    if not latest:
        raise RuntimeError("neither the control checkout nor the GitHub API reported the latest revision")
    return {"installed": installed, "latest": latest, "available": installed != latest}


async def check_for_update(*, refresh: bool = False) -> dict[str, Any]:
    """Report the installed and newest available version (cached briefly)."""
    now = time.monotonic()
    cached = _check_cache.get("value")
    if cached and not refresh and now - float(_check_cache["at"]) < _CHECK_CACHE_SECONDS:
        return cached
    deployment = deployment_type()
    result: dict[str, Any] = {
        "deployment": deployment, "installed": _installed_version(), "latest": "",
        "available": False, "error": None,
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        check = _check_docker() if deployment == "docker" else _check_baremetal()
        result.update(await asyncio.wait_for(check, timeout=_CHECK_TIMEOUT_SECONDS))
    except Exception as exc:  # network, git or GitHub failures are reported, not raised
        log_error("System update check failed", error=str(exc))
        reason = str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__
        if isinstance(exc, asyncio.TimeoutError):
            reason = "timed out"
        result["error"] = f"Could not check for updates: {reason}. See the application log for details."
    _check_cache.update(at=now, value=result)
    return result


def _write_docker_request(update_id: str, target: str, requested_at: str) -> None:
    _FLAG_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = (
        f"requested_at={requested_at}\n"
        f"update_id={update_id}\n"
        "requested_from_ui=true\n"
        "requested_mode=docker\n"
        f"target_version={target}\n"
    )
    temporary = _FLAG_PATH.with_name(f".{_FLAG_PATH.name}.{update_id}")
    temporary.write_text(payload, encoding="utf-8")
    os.chmod(temporary, 0o600)
    os.replace(temporary, _FLAG_PATH)


def host_setup_hint() -> str:
    if deployment_type() == "docker":
        return "Enable web upgrades on the Docker host with 'sudo myportal-docker web-upgrades on'."
    return (
        "Make sure /etc/cron.d/myportal-update runs scripts/process_update_flag.sh "
        "(re-run scripts/install_environment.sh to restore it)."
    )


def expire_unclaimed_requests() -> None:
    """Fail queued updates that no host job picked up."""
    active = system_update_history.find_active()
    if not active or active.get("status") != "pending":
        return
    try:
        started = datetime.fromisoformat(str(active.get("started_at")).replace("Z", "+00:00"))
    except ValueError:
        return
    if (datetime.now(timezone.utc) - started).total_seconds() < _UNCLAIMED_REQUEST_SECONDS:
        return
    try:
        _FLAG_PATH.unlink()
    except FileNotFoundError:
        pass
    except OSError:
        return
    system_update_history.update(
        str(active["id"]), status="failed", completed=True,
        error="The host did not pick up this update request. " + host_setup_hint(),
    )


async def request_update() -> dict[str, Any]:
    """Queue an upgrade for the host coordinator.

    Returns ``{"record": <history entry> | None, "message": str,
    "created": bool}``.  An update that is already queued or running is
    returned instead of queuing a second one.
    """
    expire_unclaimed_requests()
    active = system_update_history.find_active()
    if active:
        return {"record": active, "message": "An update is already in progress.", "created": False}

    if deployment_type() == "docker":
        check = await check_for_update(refresh=True)
        if check.get("error"):
            return {"record": None, "message": check["error"], "created": False}
        if not check.get("available"):
            return {"record": None, "message": "MyPortal is already on the latest release.", "created": False}
        requested_at = datetime.now(timezone.utc).isoformat()
        record = system_update_history.create_pending(
            requested_at=requested_at, target_revision=str(check["latest"]),
            source="web", mode="docker",
            output="Upgrade requested. Waiting for the Docker host to pick it up (checked every minute).",
        )
        _write_docker_request(record["id"], str(check["latest"]), requested_at)
        log_info("Docker system update requested", update_id=record["id"], target=check["latest"])
        return {"record": record, "message": "Upgrade requested.", "created": True}

    from app.services.scheduler import scheduler_service

    try:
        message = await scheduler_service.run_system_update(source="web") or ""
    except Exception as exc:
        log_error("System update request failed", error=str(exc))
        return {"record": None, "message": "Could not request the update. Check the application log.", "created": False}
    record = system_update_history.find_active()
    return {"record": record, "message": message, "created": record is not None}


def cancel_request(update_id: str) -> dict[str, Any]:
    """Withdraw an update the host has not picked up yet."""
    record = system_update_history.get(update_id)
    if record.get("status") != "pending":
        raise ValueError("Only an update that has not started can be cancelled.")
    try:
        _FLAG_PATH.unlink()
    except FileNotFoundError:
        pass
    return system_update_history.update(
        update_id, status="failed", error="Cancelled by an administrator before it started.",
        completed=True,
    )
