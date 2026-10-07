"""Import Outlook email signatures from exported ZIP files.

When a user exports a signature from Outlook (classic or new), the result is a
ZIP file containing the HTML, TXT, and RTF versions of the signature along with
a ``files/`` folder for any embedded images or assets.  This module parses that
ZIP, extracts the content, stores embedded files in the private uploads
directory, and rewrites the HTML image references so the rendered signature is
identical to the Outlook original.
"""

from __future__ import annotations

import io
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from typing import Any
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
        files: list[dict[str, Any]] = []
        if files_dir:
            files = await _extract_and_store_files(
                zf, files_dir, storage_subdir, uploads_root
            )

        # Rewrite image src attributes to absolute portal URLs
        html_content = _rewrite_image_sources(html_content, storage_subdir)

        # Sanitize the HTML (signature mode preserves inline styles)
        html_content = _sanitize_signature_html(html_content)

    log_info(
        "Imported Outlook signature '{}' ({} embedded files)",
        signature_name,
        len(files),
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
            log_warning("Skipping unexpected file type in signature ZIP: {}", info.filename)

    return entries


def _extract_signature_name(entries: list[zipfile.ZipInfo], filename: str) -> str:
    """Determine the display name of the imported signature."""
    for entry in entries:
        if entry.is_file() and PurePosixPath(entry.filename).suffix.lower() in {".html", ".htm"}:
            stem = PurePosixPath(entry.filename).name
            stem = PurePosixPath(stem).stem
            if stem:
                return stem
    return PurePosixPath(filename).stem or "Imported signature"


def _locate_entries(
    entries: list[zipfile.ZipInfo],
) -> tuple[str, str | None, str | None]:
    """Find the HTML file, TXT file, and files/ directory in the ZIP."""
    html_path: str | None = None
    txt_path: str | None = None
    files_dir: str | None = None

    for entry in entries:
        if entry.is_dir():
            name = entry.filename.rstrip("/")
            if name.lower() == "files":
                files_dir = name
            continue

        suffix = PurePosixPath(entry.filename).suffix.lower()
        if suffix in {".html", ".htm"}:
            if html_path is None:
                html_path = entry.filename
        elif suffix == ".txt":
            if txt_path is None:
                txt_path = entry.filename

    if html_path is None:
        raise ValueError(
            "The ZIP does not contain a signature HTML file. "
            "Export the signature from Outlook first (File > Signatures > Manage > Export)."
        )

    return html_path, txt_path, files_dir


def _read_entry(zf: zipfile.ZipFile, path: str) -> str:
    """Read a text entry from the ZIP, trying multiple encodings."""
    raw = zf.read(path)
    for encoding in ("utf-8", "utf-8-sig", "windows-1252", "latin-1"):
        try:
            return raw.decode(encoding)
        except (UnicodeDecodeError, LookupError):
            continue


# ---------------------------------------------------------------------------
# File extraction and storage
# ---------------------------------------------------------------------------


async def _extract_and_store_files(
    zf: zipfile.ZipFile,
    files_dir: str,
    storage_subdir: str,
    uploads_root: Any,
) -> list[dict[str, Any]]:
    """Extract image files from the files/ folder and store them."""
    stored: list[dict[str, Any]] = []
    prefix = files_dir.rstrip("/") + "/"

    for info in zf.infolist():
        if not info.filename.startswith(prefix) or info.is_dir():
            continue

        relative = info.filename[len(prefix):]
        clean_name = PurePosixPath(relative).name
        if not clean_name or clean_name.startswith("."):
            continue

        suffix = PurePosixPath(clean_name).suffix.lower()
        if suffix not in _ALLOWED_IMAGE_EXTENSIONS:
            log_warning("Skipping non-image file in signature ZIP: {}", info.filename)
            continue

        file_data = zf.read(info.filename)

        target_dir = uploads_root / "m365-signatures" / storage_subdir / "files"
        target_dir.mkdir(parents=True, exist_ok=True)

        target_file = target_dir / clean_name
        async with aiofiles.open(target_file, "wb") as fh:
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


def _rewrite_image_sources(html_content: str, storage_subdir: str) -> str:
    """Rewrite relative image src attributes to absolute portal URLs."""
    base_url = f"/uploads/m365-signatures/{storage_subdir}/files/"

    def _replace(match: re.Match) -> str:
        full_match = match.group(0)
        src_value = full_match.split("=", 1)[1].strip(" '\"")
        src_value = src_value.replace("%3E", "").replace("%20", " ")
        filename = PurePosixPath(src_value.replace("\\", "/")).name
        if not filename:
            return full_match
        attr = "src" if 'src=' in full_match.lower() else "href"
        quote = '"' if '"' in full_match else "'"
        return f'{attr}={quote}{base_url}{filename}{quote}'

    pattern = re.compile(
        r'''(src|href)\s*=\s*["'](?:files|images)[/\\%][^"']*["']''',
        re.IGNORECASE,
    )
    html_content = pattern.sub(_replace, html_content)
    return html_content


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
        _attrs |= {"border", "cellspacing", "cellpadding", "colspan", "rowspan", "valign", "scope"}
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