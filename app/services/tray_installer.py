"""Service for fetching the latest MyPortal Tray installers from GitHub Releases.

On startup (and optionally on demand) the app calls :func:`fetch_latest_tray_installers`
which:

1. Queries the GitHub Releases API for the latest release of the configured
   repository.
2. Finds the supported tray installer release assets.
3. Downloads those assets to ``app/static/tray`` so they are immediately served
   by the existing ``/static`` mount.

The download is skipped per asset when:
* The GitHub API returns no release or no matching installer asset.
* The release asset metadata matches the cached metadata stored alongside the
  file, meaning the file is already current.
* Any network or I/O error occurs — failures are logged but never fatal.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import httpx

from app.core.logging import log_error, log_info, log_warning

_GITHUB_API_BASE = "https://api.github.com"
_ASSET_NAMES = ("myportal-tray.msi", "myportal-tray.dmg", "myportal-tray.pkg")
_TRAY_STATIC_DIR = Path(__file__).resolve().parent.parent / "static" / "tray"
_DOWNLOAD_LOCK = asyncio.Lock()
_DOWNLOAD_CHUNK_SIZE = 65536


def _build_headers(github_token: str | None) -> dict[str, str]:
    headers: dict[str, str] = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "MyPortal-TrayInstaller/1.0",
    }
    if github_token:
        headers["Authorization"] = f"Bearer {github_token}"
    return headers


async def _get_latest_release(
    client: httpx.AsyncClient,
    repo: str,
    headers: dict[str, str],
) -> dict[str, Any] | None:
    url = f"{_GITHUB_API_BASE}/repos/{repo}/releases/latest"
    try:
        resp = await client.get(url, headers=headers, timeout=15.0)
        if resp.status_code == 404:
            log_warning("No releases found for tray installer repo", repo=repo)
            return None
        if resp.status_code in (403, 429):
            log_warning(
                "GitHub API rate-limited or forbidden when checking tray installers",
                status=resp.status_code,
                repo=repo,
            )
            return None
        resp.raise_for_status()
        return resp.json()
    except httpx.HTTPStatusError as exc:
        log_error("GitHub releases API error", repo=repo, error=str(exc))
        return None


def _find_assets(release: dict[str, Any]) -> dict[str, dict[str, Any]]:
    assets: dict[str, dict[str, Any]] = {}
    for asset in release.get("assets") or []:
        name = asset.get("name")
        if name in _ASSET_NAMES:
            assets[name] = asset
    return assets


def _asset_metadata(release: dict[str, Any], asset: dict[str, Any]) -> dict[str, Any]:
    return {
        "release_id": release.get("id"),
        "release_tag": release.get("tag_name"),
        "asset_id": asset.get("id"),
        "asset_name": asset.get("name"),
        "asset_updated_at": asset.get("updated_at"),
        "asset_size": asset.get("size"),
        "download_url": asset.get("browser_download_url"),
    }


def _metadata_path(asset_name: str) -> Path:
    return _TRAY_STATIC_DIR / f"{asset_name}.json"


def _is_current(asset_name: str, metadata: dict[str, Any]) -> bool:
    dest_path = _TRAY_STATIC_DIR / asset_name
    if not dest_path.is_file():
        return False
    try:
        cached = json.loads(_metadata_path(asset_name).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return all(
        cached.get(key) == metadata.get(key)
        for key in ("release_id", "asset_id", "asset_updated_at", "asset_size")
    )


def _parse_release_version(tag_name: str | None) -> str | None:
    """Return a display-friendly version from a GitHub release tag."""

    if not tag_name:
        return None
    tag = str(tag_name).strip()
    return tag[1:] if tag.lower().startswith("v") and len(tag) > 1 else tag


def get_cached_latest_release_info() -> dict[str, Any]:
    """Return metadata for the latest tray release currently cached on this server.

    The server downloads installer assets from GitHub Releases and stores the
    corresponding release metadata next to each asset.  This helper reads those
    local metadata files only, so admin pages show the release actually loaded on
    the MyPortal server instead of making an interactive GitHub request.
    """

    loaded_assets: list[dict[str, Any]] = []
    latest: dict[str, Any] | None = None
    for asset_name in _ASSET_NAMES:
        metadata_file = _metadata_path(asset_name)
        try:
            metadata = json.loads(metadata_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue

        asset_path = _TRAY_STATIC_DIR / asset_name
        if not asset_path.is_file():
            continue

        asset_info = {
            "name": asset_name,
            "size": metadata.get("asset_size"),
            "updated_at": metadata.get("asset_updated_at"),
            "download_url": metadata.get("download_url"),
        }
        loaded_assets.append(asset_info)

        metadata_updated_at = str(metadata.get("asset_updated_at") or "")
        latest_updated_at = str(latest.get("asset_updated_at") or "") if latest else ""
        if latest is None or metadata_updated_at > latest_updated_at:
            latest = metadata

    if latest is None:
        return {
            "version": None,
            "release_tag": None,
            "loaded_assets": loaded_assets,
        }

    release_tag = latest.get("release_tag")
    return {
        "version": _parse_release_version(release_tag),
        "release_tag": release_tag,
        "release_id": latest.get("release_id"),
        "asset_updated_at": latest.get("asset_updated_at"),
        "loaded_assets": loaded_assets,
    }


async def fetch_latest_tray_installers(
    *,
    repo: str,
    github_token: str | None = None,
    force: bool = False,
) -> dict[str, bool]:
    """Download the latest supported tray installers from GitHub Releases."""

    async with _DOWNLOAD_LOCK:
        return await _fetch(repo=repo, github_token=github_token, force=force)


async def fetch_latest_tray_msi(
    *,
    repo: str,
    github_token: str | None = None,
    force: bool = False,
) -> bool:
    """Backward-compatible wrapper that returns whether the MSI was updated."""

    results = await fetch_latest_tray_installers(
        repo=repo,
        github_token=github_token,
        force=force,
    )
    return results.get("myportal-tray.msi", False)


async def _fetch(
    *,
    repo: str,
    github_token: str | None,
    force: bool,
) -> dict[str, bool]:
    _TRAY_STATIC_DIR.mkdir(parents=True, exist_ok=True)
    headers = _build_headers(github_token)
    results = {asset_name: False for asset_name in _ASSET_NAMES}

    async with httpx.AsyncClient(follow_redirects=True) as client:
        release = await _get_latest_release(client, repo, headers)
        if release is None:
            return results

        tag_name: str = release.get("tag_name", "unknown")
        assets = _find_assets(release)
        missing_assets = [
            asset_name for asset_name in _ASSET_NAMES if asset_name not in assets
        ]
        if missing_assets:
            log_warning(
                "Latest release does not contain all tray installer assets",
                repo=repo,
                release=tag_name,
                missing=missing_assets,
            )

        for asset_name, asset in assets.items():
            results[asset_name] = await _download_asset(
                client=client,
                repo=repo,
                headers=headers,
                release=release,
                asset=asset,
                asset_name=asset_name,
                force=force,
            )

    return results


async def _download_asset(
    *,
    client: httpx.AsyncClient,
    repo: str,
    headers: dict[str, str],
    release: dict[str, Any],
    asset: dict[str, Any],
    asset_name: str,
    force: bool,
) -> bool:
    download_url: str = asset.get("browser_download_url", "")
    tag_name: str = release.get("tag_name", "unknown")
    if not download_url:
        log_error(
            "Tray installer asset has no download URL", repo=repo, asset=asset_name
        )
        return False

    metadata = _asset_metadata(release, asset)
    dest_path = _TRAY_STATIC_DIR / asset_name
    if not force and _is_current(asset_name, metadata):
        log_info(
            "Tray installer is already current",
            repo=repo,
            release=tag_name,
            asset=asset_name,
        )
        return False

    download_headers = dict(headers)
    download_headers["Accept"] = "application/octet-stream"

    try:
        async with client.stream(
            "GET", download_url, headers=download_headers, timeout=120.0
        ) as resp:
            resp.raise_for_status()
            tmp_path = dest_path.with_name(f"{asset_name}.tmp")
            try:
                with tmp_path.open("wb") as fh:
                    async for chunk in resp.aiter_bytes(
                        chunk_size=_DOWNLOAD_CHUNK_SIZE
                    ):
                        fh.write(chunk)
                tmp_path.replace(dest_path)
                metadata["etag"] = resp.headers.get("etag")
                _metadata_path(asset_name).write_text(
                    json.dumps(metadata, sort_keys=True), encoding="utf-8"
                )
            except Exception:
                tmp_path.unlink(missing_ok=True)
                raise
    except httpx.HTTPStatusError as exc:
        log_error(
            "Failed to download tray installer",
            repo=repo,
            release=tag_name,
            asset=asset_name,
            status=exc.response.status_code,
            error=str(exc),
        )
        return False
    except Exception as exc:
        log_error(
            "Unexpected error downloading tray installer",
            repo=repo,
            asset=asset_name,
            error=str(exc),
        )
        return False

    log_info(
        "Tray installer updated",
        repo=repo,
        release=tag_name,
        asset=asset_name,
        path=str(dest_path),
    )
    return True
