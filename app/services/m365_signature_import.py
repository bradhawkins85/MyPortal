"""Import Outlook email signatures from exported ZIP files.

When a user exports a signature from Outlook (classic or new), the result is a
ZIP file containing the HTML, TXT, and RTF versions of the signature along with
a ``files/`` folder for any embedded images or assets.  This module parses that
ZIP, extracts the content, stores embedded files in the private uploads
directory, and rewrites the HTML image references so the rendered signature is
identical to the Outlook original.
"""

from __future__ import annotations

import html
import io
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import quote as quote_url, unquote
from uuid import uuid4

import aiofiles
import nh3

from app.core.logging import log_info, log_warning
from app.services.sanitization import _filter_attribute, _ALLOWED_PROTOCOLS

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_MAX_ZIP_SIZE = 10 * 1024 * 1024  # 10 MB
_MAX_EXTRACTED_SIZE = 50 * 1024 * 1024  # 50 MB total for all files
_MAX_FILES = 200  # safety cap on number of files in the ZIP

# Allowed image extensions in the files/ folder
_ALLOWED_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg"}


@dataclass
class ParsedSignature:
    """Structured result of parsing an Outlook signature ZIP."""

    html_content: str
    text_content: str
    files: list[dict[str, Any]] = field(default_factory=list)
    source_name: str = ""

    @property
    def has_images(self) -> bool:
        return len(self.files) > 0


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def import_outlook_signature(
    zip_bytes: bytes,
    *,
    filename: str,
    uploads_root: Any,  # Path
) -> ParsedSignature:
    """Parse an Outlook signature ZIP and store embedded files.

    Returns a :class:`ParsedSignature` with the HTML content (image ``src``
    attributes rewritten to absolute portal URLs), the plain-text content, and
    a list of stored files.

    Raises ``ValueError`` for malformed or invalid archives.
    """
    if len(zip_bytes) > _MAX_ZIP_SIZE:
        raise ValueError("The uploaded ZIP file exceeds the 10 MB limit.")

    try:
        zf = zipfile.ZipFile(io.BytesIO(zip_bytes), "r")
    except zipfile.BadZipFile:
        raise ValueError("The file is not a valid ZIP archive.") from None

    with zf:
        entries = _validate_zip_contents(zf)
        signature_name = _extract_signature_name(entries, filename)

        # Locate the HTML, TXT, and files/ folder
        html_entry, txt_entry, files_dir = _locate_entries(entries)

        # Read HTML content
        html_content = _read_entry(zf, html_entry)

        # Read TXT content
        text_content = ""
        if txt_entry:
            text_content = _read_entry(zf, txt_entry)

        # Process embedded files
        storage_subdir = uuid4().hex
        files = await _extract_and_store_files(
            zf, files_dir, storage_subdir, uploads_root
        )

        # Rewrite image src attributes to absolute portal URLs
        html_content = _rewrite_image_sources(
            html_content, storage_subdir, [f["name"] for f in files]
        )

        # Sanitize the HTML (signature mode preserves inline styles)
        html_content = _sanitize_signature_html(html_content)

    log_info(
        "Imported Outlook signature",
        signature_name=signature_name,
        embedded_files=len(files),
    )

    return ParsedSignature(
        html_content=html_content,
        text_content=text_content,
        files=files,
        source_name=signature_name,
    )


# ---------------------------------------------------------------------------
# ZIP validation and parsing
# ---------------------------------------------------------------------------


def _validate_zip_contents(zf: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    """Validate the ZIP structure and return all entries."""
    entries = zf.infolist()

    if not entries:
        raise ValueError("The ZIP file is empty.")
    if len(entries) > _MAX_FILES:
        raise ValueError(f"The ZIP contains too many files (max {_MAX_FILES}).")

    # Check total size and guard against zip-bomb patterns
    total_size = sum(info.file_size for info in entries)
    if total_size > _MAX_EXTRACTED_SIZE:
        raise ValueError("The extracted files would exceed the 50 MB limit.")

    # Validate entry names to prevent path traversal
    for info in entries:
        parts = PurePosixPath(info.filename).parts
        if any(part == ".." for part in parts):
            raise ValueError(f"Invalid path in ZIP: {info.filename}")
        if info.is_dir():
            continue
        suffix = PurePosixPath(info.filename).suffix.lower()
        if suffix not in _ALLOWED_IMAGE_EXTENSIONS | {".html", ".txt", ".rtf", ".htm"}:
            log_warning("Skipping unexpected file type in signature ZIP", path=info.filename)

    return entries


def _is_asset_dir(name: str) -> bool:
    """Return True for Outlook asset folders (``files`` or ``<name>_files``)."""
    lowered = name.lower()
    return lowered in {"files", "images"} or lowered.endswith("_files")


def _in_asset_dir(path: str) -> bool:
    return any(_is_asset_dir(part) for part in PurePosixPath(path).parts[:-1])


def _file_entries(entries: list[zipfile.ZipInfo]) -> list[zipfile.ZipInfo]:
    """Return real file entries, skipping directories and macOS metadata."""
    result: list[zipfile.ZipInfo] = []
    for entry in entries:
        if entry.is_dir():
            continue
        parts = PurePosixPath(entry.filename).parts
        if not parts or parts[0] == "__MACOSX" or parts[-1].startswith("."):
            continue
        result.append(entry)
    return result


def _pick_shallowest(paths: list[str], preferred_stem: str | None = None) -> str | None:
    if not paths:
        return None
    if preferred_stem:
        matching = [p for p in paths if PurePosixPath(p).stem == preferred_stem]
        if matching:
            paths = matching
    return min(paths, key=lambda p: (len(PurePosixPath(p).parts), p.lower()))


def _find_html_entry(entries: list[zipfile.ZipInfo]) -> str | None:
    candidates = [
        e.filename
        for e in _file_entries(entries)
        if PurePosixPath(e.filename).suffix.lower() in {".html", ".htm"}
        and not _in_asset_dir(e.filename)
    ]
    return _pick_shallowest(candidates)


def _extract_signature_name(entries: list[zipfile.ZipInfo], filename: str) -> str:
    """Determine the display name of the imported signature."""
    html_path = _find_html_entry(entries)
    if html_path:
        stem = PurePosixPath(html_path).stem.strip()
        if stem:
            return stem
    return PurePosixPath(filename).stem or "Imported signature"


def _locate_entries(
    entries: list[zipfile.ZipInfo],
) -> tuple[str, str | None, str | None]:
    """Find the HTML file, TXT file, and asset folder in the ZIP.

    Outlook exports may or may not include explicit directory entries, may
    wrap everything in a top-level folder, and name the asset folder either
    ``files`` or ``<signature name>_files``.
    """
    html_path = _find_html_entry(entries)
    if html_path is None:
        raise ValueError(
            "The ZIP does not contain a signature HTML file. "
            "Export the signature from Outlook first (File > Signatures > Manage > Export)."
        )
    html_stem = PurePosixPath(html_path).stem

    file_entries = _file_entries(entries)
    txt_candidates = [
        e.filename
        for e in file_entries
        if PurePosixPath(e.filename).suffix.lower() == ".txt" and not _in_asset_dir(e.filename)
    ]
    txt_path = _pick_shallowest(txt_candidates, html_stem)

    # Collect asset folders from both directory entries and file paths.
    asset_dirs: set[str] = set()
    for entry in entries:
        parts = PurePosixPath(entry.filename.rstrip("/")).parts
        depth = len(parts) if entry.is_dir() else len(parts) - 1
        for idx in range(depth):
            if _is_asset_dir(parts[idx]):
                asset_dirs.add("/".join(parts[: idx + 1]))
                break
    files_dir: str | None = None
    if asset_dirs:
        html_parent = PurePosixPath(html_path).parent
        preferred = [
            str(html_parent / f"{html_stem}_files"),
            str(html_parent / "files"),
        ]
        preferred = [p[2:] if p.startswith("./") else p for p in preferred]
        lowered = {d.lower(): d for d in asset_dirs}
        for candidate in preferred:
            if candidate.lower() in lowered:
                files_dir = lowered[candidate.lower()]
                break
        if files_dir is None:
            files_dir = min(asset_dirs, key=lambda d: (len(PurePosixPath(d).parts), d.lower()))

    return html_path, txt_path, files_dir


def _read_entry(zf: zipfile.ZipFile, path: str) -> str:
    """Read a text entry from the ZIP, trying multiple encodings."""
    raw = zf.read(path)
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        try:
            return raw.decode("utf-16")
        except UnicodeDecodeError:
            pass
    for encoding in ("utf-8-sig", "windows-1252"):
        try:
            return raw.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("latin-1")


# ---------------------------------------------------------------------------
# File extraction and storage
# ---------------------------------------------------------------------------


async def _extract_and_store_files(
    zf: zipfile.ZipFile,
    files_dir: str | None,
    storage_subdir: str,
    uploads_root: Any,
) -> list[dict[str, Any]]:
    """Extract every image in the ZIP and store it.

    Images in the asset folder are taken first so that, if two images share a
    file name, the one the HTML most likely references wins.
    """
    stored: list[dict[str, Any]] = []
    seen: set[str] = set()
    prefix = (files_dir.rstrip("/") + "/").lower() if files_dir else None

    images = [
        info
        for info in _file_entries(zf.infolist())
        if PurePosixPath(info.filename).suffix.lower() in _ALLOWED_IMAGE_EXTENSIONS
    ]
    images.sort(key=lambda info: 0 if prefix and info.filename.lower().startswith(prefix) else 1)

    target_dir = uploads_root / "m365-signatures" / storage_subdir / "files"
    for info in images:
        clean_name = PurePosixPath(info.filename).name
        if not clean_name or clean_name.lower() in seen:
            continue
        seen.add(clean_name.lower())

        file_data = zf.read(info.filename)
        target_dir.mkdir(parents=True, exist_ok=True)
        async with aiofiles.open(target_dir / clean_name, "wb") as fh:
            await fh.write(file_data)

        stored.append(
            {
                "name": clean_name,
                "stored_path": f"m365-signatures/{storage_subdir}/files/{clean_name}",
                "size": len(file_data),
            }
        )

    return stored


# ---------------------------------------------------------------------------
# HTML rewriting
# ---------------------------------------------------------------------------

_URL_ATTR_PATTERN = re.compile(
    r"""(?P<attr>\b(?:src|href))\s*=\s*(?P<quote>["'])(?P<value>.*?)(?P=quote)""",
    re.IGNORECASE | re.DOTALL,
)


def _rewrite_image_sources(
    html_content: str,
    storage_subdir: str,
    known_files: list[str] | None = None,
) -> str:
    """Rewrite references to exported assets to absolute portal URLs.

    Handles ``files/...``, ``<name>_files/...`` (URL-encoded or with
    backslashes) and ``cid:`` references to images extracted from the ZIP.
    """
    base_url = f"/uploads/m365-signatures/{storage_subdir}/files/"
    known = {name.lower(): name for name in (known_files or [])}

    def _replace(match: re.Match) -> str:
        value = html.unescape(match.group("value")).strip()
        lowered = value.lower()
        target: str | None = None
        if lowered.startswith("cid:"):
            cid_name = unquote(value[4:]).split("@", 1)[0]
            target = known.get(cid_name.lower())
        elif not re.match(r"^[a-z][a-z0-9+.-]*:", lowered) and not value.startswith(("/", "#")):
            path = unquote(value).replace("\\", "/").split("?", 1)[0].split("#", 1)[0]
            name = PurePosixPath(path).name.lstrip(">")
            if name.lower() in known:
                target = known[name.lower()]
            elif name and _in_asset_dir(path):
                target = name
        if not target:
            return match.group(0)
        quote = match.group("quote")
        return f"{match.group('attr')}={quote}{base_url}{quote_url(target)}{quote}"

    return _URL_ATTR_PATTERN.sub(_replace, html_content)


# ---------------------------------------------------------------------------
# Sanitization (signature mode - preserves inline styles)
# ---------------------------------------------------------------------------

_SIGNATURE_ALLOWED_TAGS = frozenset(
    (
        "a", "b", "blockquote", "br", "code", "div", "em", "font",
        "h1", "h2", "h3", "h4", "h5", "h6", "hr", "i", "img", "li",
        "ol", "p", "pre", "span", "strong", "sub", "sup", "table",
        "tbody", "td", "th", "thead", "tr", "u", "ul",
    )
)

_SIGNATURE_ATTRIBUTES: dict[str, set[str]] = {}
for _tag in _SIGNATURE_ALLOWED_TAGS:
    _attrs: set[str] = {"style", "width", "height", "align"}
    if _tag == "a":
        _attrs |= {"href", "title", "target"}
    elif _tag == "img":
        _attrs |= {"src", "alt", "title", "loading", "decoding"}
    elif _tag == "font":
        _attrs |= {"face", "size", "color"}
    elif _tag in ("table", "td", "th", "tr"):
        _attrs |= {"border", "cellspacing", "cellpadding", "colspan", "rowspan", "valign", "scope", "role"}
    elif _tag == "ol":
        _attrs |= {"start", "type"}
    elif _tag == "li":
        _attrs |= {"value", "type"}
    elif _tag == "span":
        _attrs |= {"data-mention"}
    _SIGNATURE_ATTRIBUTES[_tag] = _attrs


def _sanitize_signature_html(html_content: str) -> str:
    """Sanitize imported signature HTML, preserving inline CSS styles."""
    if not html_content:
        return ""

    cleaned = nh3.clean(
        html_content,
        tags=_SIGNATURE_ALLOWED_TAGS,
        attributes=_SIGNATURE_ATTRIBUTES,
        url_schemes=_ALLOWED_PROTOCOLS,
        attribute_filter=_filter_attribute,
    )
    cleaned = cleaned.replace("\r\n", "\n").replace("\r", "\n")
    return cleaned