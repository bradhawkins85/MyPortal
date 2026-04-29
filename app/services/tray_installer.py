"""Service for fetching the latest MyPortal Tray MSI from GitHub Releases.

On startup (and optionally on demand) the app calls :func:`fetch_latest_tray_msi`
which:

1. Queries the GitHub Releases API for the latest release of the configured
   repository.
2. Finds the ``myportal-tray.msi`` release asset.
3. Downloads the asset to ``app/static/tray/myportal-tray.msi`` so it is
   immediately served by the existing ``/static`` mount used by ``install.ps1``.

The download is skipped when:
* The GitHub API returns no release or no MSI asset.
* The remote ``ETag`` / ``Last-Modified`` header matches the cached value stored
  alongside the file, meaning the file is already current.
* Any network or I/O error occurs — failures are logged but never fatal.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import httpx

from app.core.logging import log_error, log_info, log_warning

_GITHUB_API_BASE = "https://api.github.com"
_ASSET_NAME = "myportal-tray.msi"
_TRAY_STATIC_DIR = Path(__file__).resolve().parent.parent / "static" / "tray"
_DEST_PATH = _TRAY_STATIC_DIR / _ASSET_NAME
_ETAG_PATH = _TRAY_STATIC_DIR / f"{_ASSET_NAME}.etag"

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
            log_warning("No releases found for tray MSI repo", repo=repo)
            return None
        if resp.status_code in (403, 429):
            log_warning(
                "GitHub API rate-limited or forbidden when checking tray MSI",
                status=resp.status_code,
                repo=repo,
            )
            return None
        resp.raise_for_status()
        return resp.json()
    except httpx.HTTPStatusError as exc:
        log_error("GitHub releases API error", repo=repo, error=str(exc))
        return None


def _find_msi_asset(release: dict[str, Any]) -> dict[str, Any] | None:
    for asset in release.get("assets") or []:
        if asset.get("name") == _ASSET_NAME:
            return asset
    return None


async def fetch_latest_tray_msi(
    *,
    repo: str,
    github_token: str | None = None,
    force: bool = False,
) -> bool:
    """Download the latest ``myportal-tray.msi`` from GitHub Releases.

    Parameters
    ----------
    repo:
        GitHub repository in ``owner/name`` format.
    github_token:
        Optional personal-access token or fine-grained token with
        ``contents:read`` permission. Required for private repositories.
    force:
        When ``True``, re-download even if the local file appears current.

    Returns
    -------
    bool
        ``True`` when a (new) file was written to disk, ``False`` otherwise.
    """

    async with _DOWNLOAD_LOCK:
        return await _fetch(repo=repo, github_token=github_token, force=force)


async def _fetch(
    *,
    repo: str,
    github_token: str | None,
    force: bool,
) -> bool:
    _TRAY_STATIC_DIR.mkdir(parents=True, exist_ok=True)
    headers = _build_headers(github_token)

    async with httpx.AsyncClient(follow_redirects=True) as client:
        release = await _get_latest_release(client, repo, headers)
        if release is None:
            return False

        asset = _find_msi_asset(release)
        if asset is None:
            log_warning(
                "Latest release does not contain myportal-tray.msi",
                repo=repo,
                release=release.get("tag_name"),
            )
            return False

        download_url: str = asset.get("browser_download_url", "")
        if not download_url:
            log_error("MSI asset has no download URL", repo=repo)
            return False

        tag_name: str = release.get("tag_name", "unknown")

        # Check ETag to skip unnecessary re-downloads.
        cached_etag: str | None = None
        if not force and _ETAG_PATH.is_file() and _DEST_PATH.is_file():
            try:
                cached_etag = _ETAG_PATH.read_text(encoding="utf-8").strip()
            except OSError:
                cached_etag = None

        download_headers = dict(headers)
        download_headers["Accept"] = "application/octet-stream"
        if cached_etag:
            download_headers["If-None-Match"] = cached_etag

        try:
            async with client.stream(
                "GET", download_url, headers=download_headers, timeout=120.0
            ) as resp:
                if resp.status_code == 304:
                    log_info(
                        "Tray MSI is already current (304 Not Modified)",
                        repo=repo,
                        release=tag_name,
                    )
                    return False
                resp.raise_for_status()
                tmp_path = _DEST_PATH.with_suffix(".msi.tmp")
                try:
                    with tmp_path.open("wb") as fh:
                        async for chunk in resp.aiter_bytes(chunk_size=_DOWNLOAD_CHUNK_SIZE):
                            fh.write(chunk)
                    tmp_path.replace(_DEST_PATH)
                except Exception:
                    tmp_path.unlink(missing_ok=True)
                    raise
                new_etag = resp.headers.get("etag", "")
                if new_etag:
                    try:
                        _ETAG_PATH.write_text(new_etag, encoding="utf-8")
                    except OSError:
                        pass
        except httpx.HTTPStatusError as exc:
            log_error(
                "Failed to download tray MSI",
                repo=repo,
                release=tag_name,
                status=exc.response.status_code,
                error=str(exc),
            )
            return False
        except Exception as exc:
            log_error(
                "Unexpected error downloading tray MSI",
                repo=repo,
                error=str(exc),
            )
            return False

    log_info(
        "Tray MSI updated",
        repo=repo,
        release=tag_name,
        path=str(_DEST_PATH),
    )
    return True
