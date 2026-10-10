"""Read-only Gitea client used to load RMM scripts from a repository.

Gitea is where scripts are written and reviewed; MyPortal only reads them. The
repository, branch and optional folder come from the ``gitea`` integration
module (``GITEA_*`` settings in ``.env``).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx

from app.services import modules as modules_service

_TIMEOUT = httpx.Timeout(30.0, connect=10.0)
_TREE_PAGE_SIZE = 1000
_MAX_TREE_PAGES = 50


class GiteaError(RuntimeError):
    """Raised when Gitea is not configured or a request fails."""


@dataclass(frozen=True)
class GiteaSettings:
    base_url: str
    api_token: str
    owner: str
    repo: str
    branch: str
    path: str
    verify_ssl: bool


@dataclass(frozen=True)
class GiteaFile:
    path: str
    sha: str
    size: int


async def load_settings() -> GiteaSettings:
    module = await modules_service.get_module("gitea", redact=False)
    if not module or not module.get("enabled"):
        raise GiteaError("The Gitea scripts module is not enabled.")
    settings: dict[str, Any] = module.get("settings") or {}
    base_url = str(settings.get("base_url") or "").strip().rstrip("/")
    repository = str(settings.get("repository") or "").strip().strip("/")
    if not base_url:
        raise GiteaError("Set GITEA_BASE_URL to the address of your Gitea server.")
    if not base_url.lower().startswith(("https://", "http://")):
        raise GiteaError("GITEA_BASE_URL must start with https:// or http://.")
    owner, _, repo = repository.partition("/")
    if not owner or not repo or "/" in repo:
        raise GiteaError("Set GITEA_SCRIPTS_REPOSITORY to owner/name, for example msp/rmm-scripts.")
    return GiteaSettings(
        base_url=base_url,
        api_token=str(settings.get("api_token") or "").strip(),
        owner=owner,
        repo=repo,
        branch=str(settings.get("branch") or "").strip() or "main",
        path=str(settings.get("path") or "").strip().strip("/"),
        verify_ssl=bool(settings.get("verify_ssl", True)),
    )


def _headers(settings: GiteaSettings) -> dict[str, str]:
    headers = {"Accept": "application/json", "User-Agent": "MyPortal-RMM"}
    if settings.api_token:
        headers["Authorization"] = "token " + settings.api_token
    return headers


def _repo_url(settings: GiteaSettings) -> str:
    return settings.base_url + "/api/v1/repos/" + quote(settings.owner, safe="") + "/" + quote(settings.repo, safe="")


def _client(settings: GiteaSettings) -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=_TIMEOUT, verify=settings.verify_ssl, headers=_headers(settings))


def _raise_for(response: httpx.Response, action: str) -> None:
    if response.status_code == 401 or response.status_code == 403:
        raise GiteaError(f"Gitea refused access while {action}. Check GITEA_API_TOKEN.")
    if response.status_code == 404:
        raise GiteaError(f"Gitea could not find the repository or branch while {action}.")
    if response.status_code >= 400:
        raise GiteaError(f"Gitea returned HTTP {response.status_code} while {action}.")


def _in_folder(path: str, folder: str) -> bool:
    return not folder or path == folder or path.startswith(folder + "/")


async def list_files(settings: GiteaSettings) -> list[GiteaFile]:
    """Return every file in the configured branch and folder."""

    files: list[GiteaFile] = []
    url = _repo_url(settings) + "/git/trees/" + quote(settings.branch, safe="")
    try:
        async with _client(settings) as client:
            for page in range(1, _MAX_TREE_PAGES + 1):
                response = await client.get(
                    url, params={"recursive": "true", "per_page": _TREE_PAGE_SIZE, "page": page}
                )
                _raise_for(response, "listing scripts")
                payload = response.json()
                for entry in payload.get("tree") or []:
                    if entry.get("type") != "blob":
                        continue
                    path = str(entry.get("path") or "")
                    if path and _in_folder(path, settings.path):
                        files.append(GiteaFile(path=path, sha=str(entry.get("sha") or ""), size=int(entry.get("size") or 0)))
                if not payload.get("truncated"):
                    break
    except httpx.HTTPError as exc:
        raise GiteaError(f"Could not reach Gitea: {exc.__class__.__name__}") from exc
    return files


async def fetch_file(settings: GiteaSettings, path: str) -> bytes:
    """Return the raw content of one file on the configured branch."""

    encoded = "/".join(quote(part, safe="") for part in path.split("/"))
    url = _repo_url(settings) + "/raw/" + encoded
    try:
        async with _client(settings) as client:
            response = await client.get(url, params={"ref": settings.branch})
    except httpx.HTTPError as exc:
        raise GiteaError(f"Could not reach Gitea: {exc.__class__.__name__}") from exc
    _raise_for(response, "downloading " + path)
    return response.content


def web_url(settings: GiteaSettings, path: str) -> str:
    """Link to a script in the Gitea web editor."""

    encoded = "/".join(quote(part, safe="") for part in path.split("/"))
    return (
        settings.base_url + "/" + quote(settings.owner, safe="") + "/" + quote(settings.repo, safe="")
        + "/src/branch/" + quote(settings.branch, safe="") + "/" + encoded
    )
