"""Associate Gitea files with scripts and serve the immutable versions queued for runs."""

from __future__ import annotations

import hashlib
import json
import ntpath
import posixpath
import re
from pathlib import PurePosixPath, PureWindowsPath
from typing import Any, Mapping

from app.repositories import companies as companies_repo
from app.repositories import rmm as rmm_repo
from app.security.encryption import decrypt_secret
from app.services import gitea
from app.services import rmm_script_layout as layouts

MAX_FILES = 20
MAX_FILE_BYTES = 50 * 1024 * 1024
# Stands for the company folder of the company a Common script runs for.
COMPANY_TOKEN = "{company}"


class ScriptFileError(ValueError):
    pass


def validate_associations(files: list[Mapping[str, Any]]) -> list[dict[str, str]]:
    if len(files) > MAX_FILES:
        raise ScriptFileError(f"Associate at most {MAX_FILES} files with a script.")
    result: list[dict[str, str]] = []
    destinations: set[str] = set()
    for item in files:
        source = str(item.get("source_path") or "").strip()
        destination = str(item.get("destination") or "").strip()
        if not source or len(source) > 1024 or source.startswith("/") or "\\" in source:
            raise ScriptFileError("Enter a repository file path under Files/Common or Files/Companies.")
        if any(part in ("", ".", "..") for part in source.split("/")) or any(ord(c) < 32 for c in source):
            raise ScriptFileError("The repository path contains invalid characters or path traversal.")
        if not destination or len(destination) > 1024 or any(ord(c) < 32 for c in destination):
            raise ScriptFileError("Enter the full destination file path on the endpoint.")
        windows = bool(re.match(r"^[A-Za-z]:", destination)) or destination.startswith("\\")
        path = PureWindowsPath(destination) if windows else PurePosixPath(destination)
        if not path.is_absolute() or ".." in path.parts or not path.name or str(path) == path.anchor:
            raise ScriptFileError("Use an absolute destination file path without '..', such as C:\\Tools\\setup.exe or /opt/tools/setup.sh.")
        if windows:
            if not re.match(r"^[A-Za-z]:[\\/]", destination):
                raise ScriptFileError("Use a local Windows drive path for the destination.")
            if any(re.search(r'[:*?"<>|]', part) or part.endswith((" ", ".")) or PureWindowsPath(part).is_reserved()
                   for part in path.parts[1:]):
                raise ScriptFileError("The destination contains an invalid Windows filename.")
            canonical = ntpath.normcase(ntpath.normpath(destination))
        else:
            if destination.startswith("//") or destination.endswith("/"):
                raise ScriptFileError("Enter an absolute local file path, including its filename.")
            canonical = posixpath.normpath(destination)
        if canonical in destinations:
            raise ScriptFileError("Each file needs a different destination path.")
        destinations.add(canonical)
        result.append({"source_path": source, "destination": destination})
    return result


def _allowed(
    settings: gitea.GiteaSettings, files: list[gitea.GiteaFile], layout: layouts.RepositoryLayout,
    script: Mapping[str, Any], company_ids: set[int] | None = None,
) -> dict[str, gitea.GiteaFile]:
    """Files ``script`` may use, keyed by association path.

    A company script uses Files/Common and its own company's folder. A Common
    script reaches a company's files as ``Files/Companies/{company}/...``, which
    each run resolves to the company it runs for; ``company_ids`` limits those
    to the given companies (``None`` means any company).
    """
    root = layouts._repo_path(layouts.FILES_FOLDER, settings.path)
    owner = script.get("company_id")
    allowed: dict[str, gitea.GiteaFile] = {}
    for item in files:
        relative = layouts._relative(item.path, root)
        if relative is None or relative.rsplit("/", 1)[-1] == layouts.COMPANY_MARKER:
            continue
        usable, company_id, _reason = layouts.script_scope(relative, layout)
        if not usable:
            continue
        if company_id is None or (owner is not None and company_id == int(owner)):
            allowed[item.path] = item
        elif owner is None and (company_ids is None or company_id in company_ids):
            parts = relative.split("/")
            parts[1] = COMPANY_TOKEN
            allowed.setdefault(layouts._repo_path("/".join(parts), root), item)
    return allowed


async def _files_layout(settings: gitea.GiteaSettings) -> tuple[list[gitea.GiteaFile], layouts.RepositoryLayout]:
    # Reuse the established company markers, including renamed file folders.
    files = await gitea.list_files(settings)
    companies = await companies_repo.list_companies(include_archived=True)
    return files, await layouts._read_layout(settings, files, companies, files_folder=True)


async def available_files(
    settings: gitea.GiteaSettings, script: Mapping[str, Any], company_ids: set[int] | None = None,
) -> dict[str, gitea.GiteaFile]:
    files, layout = await _files_layout(settings)
    return _allowed(settings, files, layout, script, company_ids)


async def file_choices(script: Mapping[str, Any], company_id: int) -> tuple[list[str], str | None]:
    """Files the editor can pick for ``script`` while viewing ``company_id``, and
    a warning when the missing ``Files/`` folders could not be created first."""
    settings = await gitea.load_settings()
    files, layout = await _files_layout(settings)
    warning = None
    if layout.missing:
        missing = {f"{layouts.FILES_FOLDER}/{path}": content for path, content in layout.missing.items()}
        try:
            await layouts._create_missing(settings, layouts.RepositoryLayout(missing=missing))
        except gitea.GiteaError as exc:
            warning = f"The Files folders could not be created in Gitea. {exc}"
    choices = sorted(_allowed(settings, files, layout, script, {int(company_id)}), key=str.casefold)
    return choices, warning


async def validate_sources(script: Mapping[str, Any], files: list[Mapping[str, Any]]) -> list[dict[str, str]]:
    result = validate_associations(files)
    if not result:
        return result
    settings = await gitea.load_settings()
    allowed = await available_files(settings, script)
    for item in result:
        source = allowed.get(item["source_path"])
        if source is None:
            raise ScriptFileError(
                f"{item['source_path']} is missing or unavailable to this script. Company scripts use Files/Common and "
                f"their company's files; Common scripts use Files/Common and Files/Companies/{COMPANY_TOKEN}/ for the "
                "company each run is for."
            )
        if source.size > MAX_FILE_BYTES:
            raise ScriptFileError(f"{source.path} is larger than 50 MB.")
    return result


async def prepare_files(script: Mapping[str, Any], agent: Mapping[str, Any]) -> list[dict[str, Any]]:
    associations = validate_associations(script.get("files") or [])
    if not associations:
        return []
    if not agent.get("script_files_supported"):
        raise ScriptFileError("Update the RMM agent before running a script with associated files.")
    if script.get("company_id") is not None and int(script["company_id"]) != int(agent["company_id"]):
        raise ScriptFileError("The script's files are unavailable to this company.")
    settings = await gitea.load_settings()
    allowed = await available_files(settings, script, {int(agent["company_id"])})
    prepared: list[dict[str, Any]] = []
    metadata_by_sha: dict[str, dict[str, Any]] = {}
    for item in associations:
        destination = item["destination"]
        windows = str(agent.get("os") or "").lower() == "windows"
        if bool(re.match(r"^[A-Za-z]:[\\/]", destination)) != windows:
            raise ScriptFileError(f"{destination} is not an absolute file path for this endpoint's operating system.")
        source = allowed.get(item["source_path"])
        if source is None:
            company = " for this company" if COMPANY_TOKEN in item["source_path"] else ""
            raise ScriptFileError(f"{item['source_path']} is missing{company} or unavailable to this script.")
        if source.size > MAX_FILE_BYTES:
            raise ScriptFileError(f"{source.path} is larger than 50 MB.")
        if source.sha not in metadata_by_sha:
            raw = await gitea.fetch_blob(settings, source.sha, max_bytes=MAX_FILE_BYTES)
            metadata_by_sha[source.sha] = {
                "size": len(raw), "md5": hashlib.md5(raw, usedforsecurity=False).hexdigest(),
                "sha256": hashlib.sha256(raw).hexdigest(),
            }
        prepared.append({
            **item, "source_path": source.path, "blob_sha": source.sha, **metadata_by_sha[source.sha],
            "repository": f"{settings.owner}/{settings.repo}", "base_url": settings.base_url,
        })
    return prepared


async def download_file(agent: Mapping[str, Any], run_id: int, index: int) -> tuple[bytes, str] | None:
    row = await rmm_repo.get_run_file_payload(run_id, int(agent["id"]))
    if row is None or index < 0:
        return None
    try:
        payload = json.loads(decrypt_secret(row["payload_encrypted"], allow_plaintext=False))
        files = payload.get("files") or []
        item = files[index]
    except (IndexError, KeyError, TypeError, ValueError):
        return None
    settings = await gitea.load_settings()
    if settings.base_url != item["base_url"] or f"{settings.owner}/{settings.repo}" != item["repository"]:
        raise ScriptFileError("The Gitea repository configuration has changed. Queue a new run.")
    raw = await gitea.fetch_blob(settings, item["blob_sha"], max_bytes=MAX_FILE_BYTES)
    if len(raw) != item["size"] or hashlib.md5(raw, usedforsecurity=False).hexdigest() != item["md5"] or hashlib.sha256(raw).hexdigest() != item["sha256"]:
        raise ScriptFileError("The Gitea file failed download verification.")
    return raw, item["source_path"].rsplit("/", 1)[-1]
