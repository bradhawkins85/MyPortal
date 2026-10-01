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
from contextlib import suppress
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
_REVISION_RE = re.compile(r"^[0-9a-f]{40}$")
_TAG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")

_MAX_RELEASES = 30
_RELEASE_PAGES = 3
# GitHub's generated release notes: "* Title by @author in https://…/pull/123".
_RELEASE_ITEM_RE = re.compile(
    r"^\s*[*-]\s+(?P<title>.+?)(?:\s+by\s+@(?P<author>\S+))?(?:\s+in\s+(?P<url>https://\S+))?\s*$"
)
_PULL_URL_RE = re.compile(r"/pull/(?P<number>\d+)/?$")
_MERGE_PR_RE = re.compile(r"^Merge pull request #(?P<number>\d+)\b")

_check_cache: dict[str, Any] = {"at": 0.0, "value": None}
_changes_cache: dict[str, Any] = {"key": None, "at": 0.0, "value": None}


def _github_repo() -> tuple[str, str]:
    # .env.example ships these blank; blank means "use the default".
    repo = os.getenv("MYPORTAL_REPO", "").strip() or "bradhawkins85/MyPortal"
    api = (os.getenv("MYPORTAL_GITHUB_API", "").strip() or "https://api.github.com").rstrip("/")
    return repo, api


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
    repo, api = _github_repo()
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


def revision_url(target: str) -> str:
    """Return the GitHub page for an update target (a commit or a release tag)."""
    repo, _ = _github_repo()
    if _REVISION_RE.fullmatch(target or ""):
        return f"https://github.com/{repo}/commit/{target}"
    if _TAG_RE.fullmatch(target or ""):
        return f"https://github.com/{repo}/releases/tag/{target}"
    return ""


def _parse_release_notes(body: str) -> tuple[list[dict[str, Any]], str]:
    """Split GitHub release notes into pull request items and any other text."""
    items: list[dict[str, Any]] = []
    other: list[str] = []
    for line in (body or "").splitlines():
        stripped = line.strip()
        match = _RELEASE_ITEM_RE.match(stripped) if stripped[:1] in {"*", "-"} else None
        if match:
            url = match.group("url") or ""
            pull = _PULL_URL_RE.search(url)
            items.append({
                "title": match.group("title").strip(),
                "author": match.group("author") or "",
                "url": url if url.startswith("https://") else "",
                "number": int(pull.group("number")) if pull else None,
            })
        elif stripped and not stripped.startswith(("## What's Changed", "**Full Changelog**")):
            other.append(stripped)
    return items, "\n".join(other)


async def _release_changes(client: httpx.AsyncClient, installed: str, latest: str) -> dict[str, Any]:
    repo, api = _github_repo()
    releases: list[dict[str, Any]] = []
    for page in range(1, _RELEASE_PAGES + 1):
        response = await client.get(
            f"{api}/repos/{repo}/releases",
            params={"per_page": 100, "page": page},
            headers={"Accept": "application/vnd.github+json"},
        )
        response.raise_for_status()
        batch = response.json()
        if not isinstance(batch, list):
            raise RuntimeError("GitHub returned an unexpected release list.")
        releases.extend(batch)
        # Releases are listed newest first: stop once a page reaches the installed one.
        reached_installed = any(
            installed and not _version_newer(str(item.get("tag_name") or ""), installed)
            for item in batch
        )
        if len(batch) < 100 or reached_installed:
            break
    pending = []
    for release in releases:
        tag = str(release.get("tag_name") or "")
        if release.get("draft") or release.get("prerelease") or not _TAG_RE.fullmatch(tag):
            continue
        if not _version_newer(tag, installed) or _version_newer(tag, latest):
            continue
        items, notes = _parse_release_notes(str(release.get("body") or ""))
        html_url = str(release.get("html_url") or "")
        pending.append({
            "tag": tag,
            "name": str(release.get("name") or tag),
            "published_at": str(release.get("published_at") or ""),
            "url": html_url if html_url.startswith("https://") else "",
            "changes": items,
            "notes": notes,
        })
    pending.sort(key=lambda release: _version_key(release["tag"].lstrip("vV")), reverse=True)
    return {
        "kind": "releases",
        "releases": pending[:_MAX_RELEASES],
        "total": len(pending),
        "truncated": len(pending) > _MAX_RELEASES,
        "change_count": sum(len(release["changes"]) for release in pending),
        "compare_url": f"https://github.com/{repo}/compare/{installed}...{latest}" if installed else "",
    }


async def _commit_changes(client: httpx.AsyncClient, installed: str, latest: str) -> dict[str, Any]:
    repo, api = _github_repo()
    response = await client.get(
        f"{api}/repos/{repo}/compare/{installed}...{latest}",
        headers={"Accept": "application/vnd.github+json"},
    )
    response.raise_for_status()
    data = response.json()
    raw_commits = data.get("commits") or []
    commits = []
    for entry in reversed(raw_commits):  # GitHub lists them oldest first
        sha = str(entry.get("sha") or "")
        if not _REVISION_RE.fullmatch(sha):
            continue
        commit = entry.get("commit") or {}
        lines = [line.strip() for line in str(commit.get("message") or "").splitlines() if line.strip()]
        title = lines[0] if lines else sha[:12]
        pull = _MERGE_PR_RE.match(title)
        if title.startswith("Merge branch ") or title.startswith("Merge remote-tracking branch "):
            continue
        if pull and len(lines) > 1:
            title = lines[1]
        html_url = str(entry.get("html_url") or "")
        commits.append({
            "sha": sha,
            "title": title,
            "author": str((entry.get("author") or {}).get("login") or (commit.get("author") or {}).get("name") or ""),
            "date": str((commit.get("author") or {}).get("date") or ""),
            "url": html_url if html_url.startswith("https://") else "",
            "number": int(pull.group("number")) if pull else None,
        })
    total = int(data.get("total_commits") or len(raw_commits))
    compare_url = str(data.get("html_url") or "")
    return {
        "kind": "commits",
        "commits": commits,
        "total": total,
        "truncated": total > len(raw_commits),
        "change_count": len(commits),
        "compare_url": compare_url if compare_url.startswith("https://") else "",
    }


async def list_changes(check: dict[str, Any], *, refresh: bool = False) -> dict[str, Any]:
    """List what changed between the installed and the latest version.

    Docker installs list the published releases (with their notes) newer than
    the installed release; bare-metal installs list the commits on main since
    the installed revision.  Failures are reported in ``error``, never raised.
    """
    deployment = str(check.get("deployment") or deployment_type())
    installed = str(check.get("installed") or "")
    latest = str(check.get("latest") or "")
    empty: dict[str, Any] = {
        "kind": "releases" if deployment == "docker" else "commits",
        "releases": [], "commits": [], "total": 0, "truncated": False,
        "change_count": 0, "compare_url": "", "error": None,
    }
    if check.get("error") or not check.get("available") or not latest:
        return empty
    if deployment != "docker" and not (
        _REVISION_RE.fullmatch(installed) and _REVISION_RE.fullmatch(latest)
    ):
        return {**empty, "error": "The installed revision is not a Git commit, so the changes cannot be listed."}

    key = (deployment, installed, latest)
    now = time.monotonic()
    if (
        not refresh and _changes_cache.get("key") == key
        and now - float(_changes_cache["at"]) < _CHECK_CACHE_SECONDS
    ):
        return _changes_cache["value"]
    try:
        async with httpx.AsyncClient(timeout=_CHECK_TIMEOUT_SECONDS) as client:
            fetch = (
                _release_changes(client, installed, latest) if deployment == "docker"
                else _commit_changes(client, installed, latest)
            )
            result = {**empty, **await asyncio.wait_for(fetch, timeout=_CHECK_TIMEOUT_SECONDS)}
    except Exception as exc:  # network or GitHub failures are reported, not raised
        log_error("Listing system update changes failed", error=str(exc))
        reason = str(exc) if isinstance(exc, RuntimeError) else type(exc).__name__
        if isinstance(exc, asyncio.TimeoutError):
            reason = "timed out"
        return {**empty, "error": f"Could not list the changes: {reason}."}
    _changes_cache.update(key=key, at=now, value=result)
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
        with suppress(FileNotFoundError):
            _FLAG_PATH.unlink()
    except OSError:
        return
    system_update_history.update(
        str(active["id"]), status="failed", completed=True,
        error="The host did not pick up this update request. " + host_setup_hint(),
    )


async def _installed_revision() -> str:
    if deployment_type() == "docker":
        return _installed_version()
    from app.services.scheduler import scheduler_service

    return await scheduler_service._get_git_ref("HEAD") or ""


async def _github_revision_is_older(target: str, installed: str) -> bool:
    repo, api = _github_repo()
    try:
        async with httpx.AsyncClient(timeout=_CHECK_TIMEOUT_SECONDS) as client:
            response = await client.get(
                f"{api}/repos/{repo}/compare/{target}...{installed}",
                headers={"Accept": "application/vnd.github+json"},
            )
            response.raise_for_status()
            # "ahead": the installed revision contains the target and more.
            return response.json().get("status") == "ahead"
    except (httpx.HTTPError, ValueError) as exc:
        log_error("Could not compare system update revisions", error=str(exc))
        return False


async def _target_is_older(target: str, installed: str) -> bool:
    """Return whether ``installed`` already supersedes an update's ``target``.

    An equal target is never older: during a rolling upgrade the new release
    serves requests before the host job reports the update as finished.
    """
    if not target or not installed or target == installed:
        return False
    if deployment_type() == "docker":
        return _version_newer(installed, target)
    if not (_REVISION_RE.fullmatch(target) and _REVISION_RE.fullmatch(installed)):
        return False
    from app.services.scheduler import scheduler_service

    try:
        rc, _, _ = await scheduler_service._run_git("merge-base", "--is-ancestor", target, installed)
    except OSError:
        rc = -1
    if rc in (0, 1):
        return rc == 0
    # Releases have no .git and the control checkout may be unreadable, or may
    # not have fetched either revision yet: ask GitHub instead.
    return await _github_revision_is_older(target, installed)


async def fail_superseded_updates() -> None:
    """Fail queued or running updates whose target is older than what is installed.

    A host job that died mid-upgrade (or could not report its result) leaves
    its update active forever, which blocks every later request.
    """
    active = [
        record for record in system_update_history.list_updates()
        if record.get("status") in {"pending", "running"}
    ]
    if not active:
        return
    installed = await _installed_revision()
    for record in active:
        target = str(record.get("target_revision") or "")
        if not await _target_is_older(target, installed):
            continue
        if record.get("status") == "pending":
            try:
                _FLAG_PATH.unlink()
            except FileNotFoundError:
                # The flag was already removed; nothing to clean up for this update.
                pass
            except OSError as exc:
                log_error(
                    "Failed to remove pending system update flag for superseded update",
                    update_id=record.get("id"),
                    flag_path=str(_FLAG_PATH),
                    error=str(exc),
                )
        try:
            system_update_history.update(
                str(record["id"]), status="failed", completed=True,
                error=(
                    f"Superseded: MyPortal is already on {installed[:12]}, which is newer "
                    f"than this update's target {target[:12]}. The host job did not report "
                    "a result for this update."
                ),
            )
        except (KeyError, ValueError):
            continue
        log_info("Superseded system update marked as failed", update_id=record.get("id"),
                 target=target, installed=installed)


async def request_update() -> dict[str, Any]:
    """Queue an upgrade for the host coordinator.

    Returns ``{"record": <history entry> | None, "message": str,
    "created": bool}``.  An update that is already queued or running is
    returned instead of queuing a second one.
    """
    expire_unclaimed_requests()
    await fail_superseded_updates()
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
    with suppress(FileNotFoundError):
        _FLAG_PATH.unlink()
    return system_update_history.update(
        update_id, status="failed", error="Cancelled by an administrator before it started.",
        completed=True,
    )
