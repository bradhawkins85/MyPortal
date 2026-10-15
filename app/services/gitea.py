"""Gitea client used to load RMM scripts from a repository.

Gitea is where scripts are written and reviewed. MyPortal reads them, and only
writes the script and file folder skeleton (``Common/``, ``Companies/<company>/``,
``Files/Common/``, ``Files/Companies/<company>/``) with
:func:`create_files`. The repository, branch and optional folder come from the
``gitea`` integration module (``GITEA_*`` settings in ``.env``).

For MyPortal sign-in, :func:`sign_in_as` makes the bundled Gitea create a
technician's account and :func:`set_collaborator` gives it access to the
repository (see ``app.services.gitea_sign_in``).
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any
from urllib.parse import quote

import base64
import hashlib
import json
import re

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
    public_url: str
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
    public_url = str(settings.get("public_url") or "").strip().rstrip("/")
    if public_url and (
        public_url.startswith("//") or not public_url.lower().startswith(("https://", "http://", "/"))
    ):
        raise GiteaError("GITEA_PUBLIC_URL must start with https://, http:// or /.")
    owner, _, repo = repository.partition("/")
    if not owner or not repo or "/" in repo:
        raise GiteaError("Set GITEA_SCRIPTS_REPOSITORY to owner/name, for example msp/rmm-scripts.")
    return GiteaSettings(
        base_url=base_url,
        public_url=public_url or base_url,
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


async def resolve_commit(settings: GiteaSettings) -> str:
    """Pin the configured branch before downloading associated files."""
    try:
        async with _client(settings) as client:
            response = await client.get(_repo_url(settings) + "/branches/" + quote(settings.branch, safe=""))
    except httpx.HTTPError as exc:
        raise GiteaError(f"Could not reach Gitea: {exc.__class__.__name__}") from exc
    _raise_for(response, "resolving the script file version")
    try:
        sha = response.json()["commit"]["id"]
        if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{40}", sha):
            raise ValueError("Invalid commit")
    except (ValueError, KeyError, TypeError) as exc:
        raise GiteaError("Gitea returned an invalid file commit.") from exc
    return sha


async def fetch_blob(
    settings: GiteaSettings, sha: str, *, max_bytes: int,
    path: str | None = None, ref: str | None = None,
) -> bytes:
    """Fetch an immutable Git blob, preserving the file version queued for a run."""
    if not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise GiteaError("Gitea returned an invalid file version.")
    url = _repo_url(settings) + "/git/blobs/" + sha
    try:
        async with _client(settings) as client:
            async with client.stream("GET", url) as response:
                _raise_for(response, "downloading a script file")
                data = bytearray()
                async for chunk in response.aiter_bytes():
                    data.extend(chunk)
                    if len(data) > max_bytes * 2 + 65536:
                        raise GiteaError("The script file exceeds the download size limit.")
    except httpx.HTTPError as exc:
        raise GiteaError(f"Could not reach Gitea: {exc.__class__.__name__}") from exc
    try:
        payload = json.loads(data)
        if not isinstance(payload, dict):
            raise ValueError("Unexpected blob response")
        # Gitea omits content above API.DEFAULT_MAX_BLOB_SIZE. The raw
        # endpoint has no such threshold; only use it at a pinned commit.
        omitted = payload.get("encoding") is None and payload.get("content") is None
        if omitted and path and ref and re.fullmatch(r"[0-9a-f]{40}", ref):
            encoded = "/".join(quote(part, safe="") for part in path.split("/"))
            try:
                async with _client(settings) as client:
                    async with client.stream("GET", _repo_url(settings) + "/raw/" + encoded, params={"ref": ref}) as response:
                        _raise_for(response, "downloading a script file")
                        data = bytearray()
                        async for chunk in response.aiter_bytes():
                            data.extend(chunk)
                            if len(data) > max_bytes:
                                raise GiteaError("The script file exceeds the download size limit.")
            except httpx.HTTPError as exc:
                raise GiteaError(f"Could not reach Gitea: {exc.__class__.__name__}") from exc
            raw = bytes(data)
        elif omitted:
            raise GiteaError("Gitea omitted the script file content. Queue a new run to download it at a pinned commit.")
        else:
            if payload.get("encoding") != "base64" or not isinstance(payload.get("content"), str):
                raise ValueError("Unexpected blob encoding")
            raw = base64.b64decode("".join(payload["content"].split()), validate=True)
    except (ValueError, KeyError, TypeError) as exc:
        raise GiteaError("Gitea returned an unreadable script file.") from exc
    if len(raw) > max_bytes:
        raise GiteaError("The script file exceeds the download size limit.")
    digest = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw, usedforsecurity=False).hexdigest()
    if digest != sha:
        raise GiteaError("The script file did not match its Gitea version.")
    return raw


async def create_files(settings: GiteaSettings, files: dict[str, str], message: str) -> None:
    """Create text files on the configured branch in a single commit.

    ``files`` maps repository paths to their content. Needs a token with write
    access to the repository.
    """

    if not files:
        return
    body = {
        "branch": settings.branch,
        "message": message,
        "files": [
            {"operation": "create", "path": path, "content": base64.b64encode(content.encode("utf-8")).decode("ascii")}
            for path, content in files.items()
        ],
    }
    try:
        async with _client(settings) as client:
            response = await client.post(_repo_url(settings) + "/contents", json=body)
    except httpx.HTTPError as exc:
        raise GiteaError(f"Could not reach Gitea: {exc.__class__.__name__}") from exc
    if response.status_code in (401, 403):
        raise GiteaError("Gitea refused to create folders. GITEA_API_TOKEN needs write access to the repository.")
    _raise_for(response, "creating folders")


# Headers the bundled Gitea trusts from its proxy (REVERSE_PROXY_AUTHENTICATION_*).
SIGN_IN_USER_HEADER = "X-WEBAUTH-USER"
SIGN_IN_EMAIL_HEADER = "X-WEBAUTH-EMAIL"
SIGN_IN_NAME_HEADER = "X-WEBAUTH-FULLNAME"


def sign_in_headers(login: str, *, email: str = "", full_name: str = "") -> dict[str, str]:
    headers = {SIGN_IN_USER_HEADER: login}
    if email:
        headers[SIGN_IN_EMAIL_HEADER] = email
    if full_name:
        headers[SIGN_IN_NAME_HEADER] = full_name
    return headers


async def sign_in_as(settings: GiteaSettings, login: str, *, email: str = "", full_name: str = "") -> None:
    """Open a page as ``login`` the way the proxy does, so Gitea creates the
    account on first use. MyPortal reaches Gitea from an address Gitea trusts
    as its proxy; the API token is not sent."""

    headers = {"User-Agent": "MyPortal-RMM", **sign_in_headers(login, email=email, full_name=full_name)}
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT, verify=settings.verify_ssl, headers=headers) as client:
            response = await client.get(settings.base_url + "/user/settings")
    except httpx.HTTPError as exc:
        raise GiteaError(f"Could not reach Gitea: {exc.__class__.__name__}") from exc
    if response.status_code != 200:
        raise GiteaError(
            f"Gitea did not sign in {login} (HTTP {response.status_code}). "
            "MyPortal sign-in needs the bundled Gitea, which trusts MyPortal's sign-in headers."
        )


async def set_collaborator(settings: GiteaSettings, login: str, permission: str) -> None:
    """Give ``login`` read or write access to the script repository."""

    url = _repo_url(settings) + "/collaborators/" + quote(login, safe="")
    try:
        async with _client(settings) as client:
            response = await client.put(url, json={"permission": permission})
    except httpx.HTTPError as exc:
        raise GiteaError(f"Could not reach Gitea: {exc.__class__.__name__}") from exc
    if response.status_code == 422:
        raise GiteaError(f"Gitea has no account named {login}.")
    _raise_for(response, "giving " + login + " access")


async def remove_collaborator(settings: GiteaSettings, login: str) -> None:
    url = _repo_url(settings) + "/collaborators/" + quote(login, safe="")
    try:
        async with _client(settings) as client:
            response = await client.delete(url)
    except httpx.HTTPError as exc:
        raise GiteaError(f"Could not reach Gitea: {exc.__class__.__name__}") from exc
    _raise_for(response, "removing " + login + "'s access")


def repository_url(settings: GiteaSettings) -> str:
    """Link to the script repository in the Gitea web interface."""

    return settings.public_url + "/" + quote(settings.owner, safe="") + "/" + quote(settings.repo, safe="")


def web_url(settings: GiteaSettings, path: str) -> str:
    """Link to a script in the Gitea web editor."""

    encoded = "/".join(quote(part, safe="") for part in path.split("/"))
    return (
        repository_url(settings) + "/src/branch/" + quote(settings.branch, safe="") + "/" + encoded
    )


def public_settings(settings: GiteaSettings) -> GiteaSettings:
    """The separate public download repository owned by the scripts owner."""
    if settings.repo.casefold() == "public":
        raise GiteaError("The private scripts repository must be separate from Public.")
    return replace(settings, repo="Public", branch="main", path="")
