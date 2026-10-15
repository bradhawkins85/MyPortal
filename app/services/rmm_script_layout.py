"""Where RMM scripts and files live in the Gitea repository.

``Common/`` holds scripts for every company and ``Companies/<company>/`` scripts
for one company, linked by a ``.myportal-company`` marker so the folder can be
renamed. ``Files/`` repeats that layout for stored files. :func:`_read_layout`
finds the folders and the files still needed to create the missing ones.
"""

from __future__ import annotations

import contextlib
import json
import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping

from app.core.logging import log_info, log_warning
from app.services import gitea

COMMON_FOLDER = "Common"
COMPANIES_FOLDER = "Companies"
FILES_FOLDER = "Files"
# Links a folder in Companies/ to a MyPortal company, so the folder can be
# renamed in Gitea without losing which company its scripts belong to.
COMPANY_MARKER = ".myportal-company"
_FOLDER_UNSAFE = re.compile(r'[\x00-\x1f\x7f/\\:*?"<>|]+')

_COMMON_README = """# Common scripts

Scripts in this folder (and its subfolders) can be run on any company's devices
from MyPortal's Scripts page. Use Sync from Gitea in MyPortal after changing them.
"""

_COMPANIES_README = """# Company scripts

MyPortal creates a folder here for every company. Scripts in a company's folder
(and its subfolders) can only be run on that company's devices.

Each folder holds a `.myportal-company` file linking it to the company. Keep that
file; the folder itself can be renamed.
"""

_COMMON_FILES_README = """# Common files

Store files shared across companies here, including executables, installers and
scripts of any type. Manage uploads and downloads in Gitea. Files in this folder
are not imported into MyPortal's runnable script library.
"""

_COMPANY_FILES_README = """# Company files

MyPortal creates a folder here for every company to store its files of any type.
Each folder holds a `.myportal-company` file linking it to the company. Keep that
file; the folder itself can be renamed. Manage uploads and downloads in Gitea.
Repository permissions cover every company folder.
"""


def _display_name(path: str) -> str:
    filename = path.rsplit("/", 1)[-1]
    return filename.rsplit(".", 1)[0] if "." in filename else filename


def _relative(path: str, root: str) -> str | None:
    """``path`` relative to the configured folder, or ``None`` outside it."""

    if not root:
        return path
    return path[len(root) + 1:] if path.startswith(root + "/") else None


def _repo_path(relative: str, root: str) -> str:
    return f"{root}/{relative}" if root else relative


def _folder(relative: str) -> str:
    return relative.rsplit("/", 1)[0] if "/" in relative else ""


def company_folder_names(companies: Iterable[Mapping[str, Any]]) -> dict[int, str]:
    """Folder name for each company: its name made safe for a path, unique
    (ignoring case) by adding the company id when two names collide."""

    names: dict[int, str] = {}
    used: set[str] = set()
    for company in sorted(companies, key=lambda item: int(item["id"])):
        company_id = int(company["id"])
        base = _FOLDER_UNSAFE.sub("-", str(company.get("name") or "")).strip(" .-")[:100].strip(" .")
        name = base or f"Company {company_id}"
        if name.casefold() in used:
            name = f"{name} ({company_id})"
        used.add(name.casefold())
        names[company_id] = name
    return names


def _marker(company_id: int, name: str) -> str:
    return json.dumps(
        {
            "company_id": company_id,
            "company": name,
            "note": "Links this folder to a MyPortal company.",
        },
        indent=2,
    ) + "\n"


@dataclass
class RepositoryLayout:
    """Which ``Companies/`` folder belongs to which company, and the files
    still needed to give every folder its place in the repository."""

    company_folders: dict[str, int] = field(default_factory=dict)
    missing: dict[str, str] = field(default_factory=dict)


async def read_layout(
    settings: gitea.GiteaSettings, files: list[gitea.GiteaFile], companies: list[Mapping[str, Any]]
) -> RepositoryLayout:
    layout = await _read_layout(settings, files, companies)
    file_layout = await _read_layout(settings, files, companies, files_folder=True)
    layout.missing.update({f"{FILES_FOLDER}/{path}": content for path, content in file_layout.missing.items()})
    return layout


async def _read_layout(
    settings: gitea.GiteaSettings, files: list[gitea.GiteaFile], companies: list[Mapping[str, Any]],
    *, files_folder: bool = False,
) -> RepositoryLayout:
    layout = RepositoryLayout()
    root = _repo_path(FILES_FOLDER, settings.path) if files_folder else settings.path
    known = {int(company["id"]): company for company in companies}
    relative_paths = [rel for rel in (_relative(item.path, root) for item in files) if rel is not None]
    company_dirs = {
        rel.split("/")[1] for rel in relative_paths if rel.startswith(COMPANIES_FOLDER + "/") and rel.count("/") >= 2
    }

    markers: dict[str, str] = {}
    for item in files:
        rel = _relative(item.path, root)
        parts = rel.split("/") if rel else []
        if len(parts) == 3 and parts[0] == COMPANIES_FOLDER and parts[2] == COMPANY_MARKER:
            markers[item.path] = parts[1]

    async with contextlib.aclosing(gitea.file_batches(settings, markers)) as batches:
        async for batch in batches:
            for path, raw in batch:
                try:
                    company_id = int(json.loads(raw.decode("utf-8"))["company_id"])
                except (ValueError, KeyError, TypeError, UnicodeDecodeError):
                    log_warning("Ignoring an unreadable company folder marker", path=path)
                    continue
                folder = markers[path]
                if company_id in known and folder not in layout.company_folders:
                    layout.company_folders[folder] = company_id

    linked = set(layout.company_folders.values())
    by_name = {name.casefold(): name for name in company_dirs if name not in layout.company_folders}
    for company_id, name in company_folder_names(companies).items():
        if company_id in linked:
            continue
        folder = by_name.pop(name.casefold(), None)
        if folder is None:
            if known[company_id].get("archived"):
                continue
            folder = name
        # A folder someone made by hand for this company is linked, not duplicated.
        layout.company_folders[folder] = company_id
        layout.missing[f"{COMPANIES_FOLDER}/{folder}/{COMPANY_MARKER}"] = _marker(company_id, str(known[company_id].get("name") or name))

    if not any(rel.startswith(COMMON_FOLDER + "/") for rel in relative_paths):
        layout.missing[f"{COMMON_FOLDER}/README.md"] = _COMMON_FILES_README if files_folder else _COMMON_README
    if not any(rel.startswith(COMPANIES_FOLDER + "/") for rel in relative_paths):
        layout.missing[f"{COMPANIES_FOLDER}/README.md"] = _COMPANY_FILES_README if files_folder else _COMPANIES_README
    # Gitea rejects the whole commit when one file already exists, such as an
    # unreadable marker or one naming a deleted company, so never recreate one.
    existing = set(relative_paths)
    layout.missing = {path: content for path, content in layout.missing.items() if path not in existing}
    return layout


def script_scope(relative: str, layout: RepositoryLayout) -> tuple[bool, int | None, str]:
    """``(usable, company_id, reason)`` for a script at ``relative``."""

    parts = relative.split("/")
    if parts[0] == COMMON_FOLDER and len(parts) > 1:
        return True, None, ""
    if parts[0] == COMPANIES_FOLDER and len(parts) > 2:
        company_id = layout.company_folders.get(parts[1])
        if company_id is None:
            return False, None, f"{COMPANIES_FOLDER}/{parts[1]} is not linked to a company"
        return True, company_id, ""
    return False, None, f"Not in {COMMON_FOLDER}/ or a company folder in {COMPANIES_FOLDER}/"


async def _create_missing(settings: gitea.GiteaSettings, layout: RepositoryLayout) -> int:
    if not layout.missing:
        return 0
    await gitea.create_files(
        settings,
        {_repo_path(path, settings.path): content for path, content in sorted(layout.missing.items())},
        "Add MyPortal script and file folders",
    )
    log_info("Created script and file folders in Gitea", files=sorted(layout.missing))
    return len(layout.missing)
