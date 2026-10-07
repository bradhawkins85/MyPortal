"""Import Outlook email signatures from exported ZIP files.

When a user exports a signature from Outlook (classic or new), the result is a
ZIP file containing the HTML, TXT, and RTF versions of the signature along with
a ``files/`` (or ``<name>_files/``) folder for any embedded images.  This
module parses that ZIP, converts Outlook's stylesheet rules into inline styles,
normalises the whitespace Outlook wraps its markup with, and embeds the images
as base64 ``data:`` URIs so the signature is fully self-contained.
"""

from __future__ import annotations

import base64
import html
import io
import re
import zipfile
from dataclasses import dataclass, field
from html.parser import HTMLParser
from pathlib import PurePosixPath
from typing import Any
from urllib.parse import unquote

import nh3
from PIL import Image, UnidentifiedImageError

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
) -> ParsedSignature:
    """Parse an Outlook signature ZIP into self-contained signature HTML.

    Returns a :class:`ParsedSignature` with the HTML content (styles inlined,
    images embedded as ``data:`` URIs), the plain-text content, and a list of
    the embedded images.

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

        html_entry, txt_entry, files_dir = _locate_entries(entries)
        html_content = _read_entry(zf, html_entry)
        text_content = ""
        if txt_entry:
            text_content = (
                _read_entry(zf, txt_entry).replace("\r\n", "\n").replace("\r", "\n").strip()
            )

        images = _extract_images(zf, files_dir)
        html_content = _convert_outlook_html(html_content, images)
        html_content = _sanitize_signature_html(html_content)

    files = [
        {"name": image["name"], "content_type": image["content_type"], "size": image["size"]}
        for image in images.values()
    ]
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
# Image extraction (embedded as data: URIs)
# ---------------------------------------------------------------------------

_IMAGE_CONTENT_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
}


def _image_data_uri(name: str, data: bytes) -> tuple[str, str] | None:
    """Return ``(content_type, data_uri)`` for an image, or ``None`` if unusable.

    PNG/JPEG/GIF/WebP are embedded as-is; other raster formats (e.g. BMP) are
    converted to PNG.  SVG is never embedded because it can carry script.
    """
    suffix = PurePosixPath(name).suffix.lower()
    content_type = _IMAGE_CONTENT_TYPES.get(suffix)
    if content_type is None:
        if suffix == ".svg":
            return None
        try:
            with Image.open(io.BytesIO(data)) as img:
                buffer = io.BytesIO()
                img.save(buffer, format="PNG")
        except (UnidentifiedImageError, OSError, ValueError):
            return None
        data = buffer.getvalue()
        content_type = "image/png"
    encoded = base64.b64encode(data).decode("ascii")
    return content_type, f"data:{content_type};base64,{encoded}"


def _extract_images(
    zf: zipfile.ZipFile, files_dir: str | None
) -> dict[str, dict[str, Any]]:
    """Read every image in the ZIP, keyed by lower-cased file name.

    Images in the asset folder are taken first so that, if two images share a
    file name, the one the HTML most likely references wins.
    """
    images: dict[str, dict[str, Any]] = {}
    prefix = (files_dir.rstrip("/") + "/").lower() if files_dir else None

    candidates = [
        info
        for info in _file_entries(zf.infolist())
        if PurePosixPath(info.filename).suffix.lower() in _ALLOWED_IMAGE_EXTENSIONS
    ]
    candidates.sort(
        key=lambda info: 0 if prefix and info.filename.lower().startswith(prefix) else 1
    )

    for info in candidates:
        name = PurePosixPath(info.filename).name
        if not name or name.lower() in images:
            continue
        data = zf.read(info.filename)
        converted = _image_data_uri(name, data)
        if converted is None:
            log_warning("Skipping unsupported image in signature ZIP", path=info.filename)
            continue
        content_type, data_uri = converted
        images[name.lower()] = {
            "name": name,
            "content_type": content_type,
            "size": len(data),
            "data_uri": data_uri,
        }

    return images


def _resolve_image_reference(value: str, images: dict[str, dict[str, Any]]) -> str | None:
    """Map an Outlook ``src``/``href`` value to an embedded image's data URI.

    Handles ``files/...``, ``<name>_files/...`` (URL-encoded or with
    backslashes) and ``cid:`` references.  Returns ``None`` when the value does
    not refer to an image from the ZIP.
    """
    value = value.strip()
    lowered = value.lower()
    if lowered.startswith("cid:"):
        name = unquote(value[4:]).split("@", 1)[0]
    elif re.match(r"^[a-z][a-z0-9+.-]*:", lowered) or value.startswith(("/", "#")):
        return None
    else:
        path = unquote(value).replace("\\", "/").split("?", 1)[0].split("#", 1)[0]
        name = PurePosixPath(path).name.lstrip(">")
    image = images.get(name.lower())
    return image["data_uri"] if image else None


# ---------------------------------------------------------------------------
# Outlook HTML conversion
# ---------------------------------------------------------------------------

_CSS_COMMENT = re.compile(r"/\*.*?\*/", re.DOTALL)
_CSS_AT_RULE = re.compile(r"@[^{;]*(?:;|\{[^{}]*\})")
_CSS_RULE = re.compile(r"([^{}]+)\{([^{}]*)\}")
_CSS_SIMPLE_SELECTOR = re.compile(r"^([a-z][a-z0-9]*)?(?:\.([a-z0-9_-]+))?$", re.IGNORECASE)
_WHITESPACE = re.compile(r"[ \t\r\n\f]+")
_BLOCK_TAG_WHITESPACE = re.compile(
    r"[ \t\r\n\f]*(</?(?:p|div|table|thead|tbody|tr|td|th|ul|ol|li|h[1-6]|blockquote|hr|br)\b[^>]*>)[ \t\r\n\f]*",
    re.IGNORECASE,
)
# Containers whose text content is never part of the visible signature.
_SKIP_CONTENT_TAGS = frozenset(("head", "title", "style", "script", "xml"))
_VOID_TAGS = frozenset(("br", "hr", "img", "meta", "link", "input", "col", "area", "base", "wbr"))


def _parse_css_declarations(block: str) -> list[tuple[str, str]]:
    declarations: list[tuple[str, str]] = []
    for item in block.split(";"):
        prop, sep, value = item.partition(":")
        prop = prop.strip().lower()
        value = value.strip()
        if not sep or not prop or not value:
            continue
        # Word/Outlook-only properties are meaningless outside Office.
        if prop.startswith("mso-") or prop in {"page", "tab-stops"}:
            continue
        declarations.append((prop, value))
    return declarations


def _parse_stylesheet(css: str) -> list[tuple[int, int, str | None, str | None, list[tuple[str, str]]]]:
    """Parse simple ``tag``, ``.class`` and ``tag.class`` rules from Outlook CSS."""
    css = _CSS_COMMENT.sub("", css).replace("<!--", "").replace("-->", "")
    css = _CSS_AT_RULE.sub("", css)
    rules = []
    order = 0
    for match in _CSS_RULE.finditer(css):
        declarations = _parse_css_declarations(match.group(2))
        if not declarations:
            continue
        for selector in match.group(1).split(","):
            simple = _CSS_SIMPLE_SELECTOR.match(selector.strip())
            if not simple or not (simple.group(1) or simple.group(2)):
                continue
            tag = simple.group(1).lower() if simple.group(1) else None
            cls = simple.group(2).lower() if simple.group(2) else None
            specificity = (10 if cls else 0) + (1 if tag else 0)
            rules.append((specificity, order, tag, cls, declarations))
            order += 1
    rules.sort(key=lambda rule: (rule[0], rule[1]))
    return rules


class _StyleCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.css: list[str] = []
        self._in_style = False

    def handle_starttag(self, tag, attrs):
        if tag == "style":
            self._in_style = True

    def handle_endtag(self, tag):
        if tag == "style":
            self._in_style = False

    def handle_data(self, data):
        if self._in_style:
            self.css.append(data)


class _OutlookHtmlConverter(HTMLParser):
    """Re-emit Outlook HTML with inlined styles and embedded images."""

    def __init__(self, rules, images: dict[str, dict[str, Any]]) -> None:
        super().__init__(convert_charrefs=True)
        self._rules = rules
        self._images = images
        self._out: list[str] = []
        self._skip_depth = 0
        self._anchor_stack: list[bool] = []

    def result(self) -> str:
        return "".join(self._out)

    def _inline_style(self, tag: str, attrs: dict[str, str]) -> str:
        classes = {c.lower() for c in (attrs.get("class") or "").split()}
        merged: dict[str, str] = {}
        for _, _, rule_tag, rule_cls, declarations in self._rules:
            if rule_tag and rule_tag != tag:
                continue
            if rule_cls and rule_cls not in classes:
                continue
            for prop, value in declarations:
                merged[prop] = value
        for prop, value in _parse_css_declarations(attrs.get("style") or ""):
            merged[prop] = value
        return ";".join(f"{prop}:{value}" for prop, value in merged.items())

    def handle_starttag(self, tag, attrs):
        if tag == "body":
            # </head> is optional; <body> always ends any skipped head content.
            self._skip_depth = 0
        if tag in _SKIP_CONTENT_TAGS:
            self._skip_depth += 1
            return
        if self._skip_depth or ":" in tag:
            # Office namespaced elements (o:p, v:shape, w:...) are dropped but
            # their content is kept.
            return
        attr_map = {name.lower(): (value or "") for name, value in attrs}
        if tag == "a":
            # Outlook wraps the signature in bookmark anchors such as
            # <a name="_MailAutoSig">; without an href they are not links.
            keep = bool(attr_map.get("href", "").strip())
            self._anchor_stack.append(keep)
            if not keep:
                return
        for url_attr in ("src", "href"):
            if url_attr in attr_map:
                resolved = _resolve_image_reference(attr_map[url_attr], self._images)
                if resolved:
                    attr_map[url_attr] = resolved
        style = self._inline_style(tag, attr_map)
        attr_map.pop("class", None)
        attr_map.pop("style", None)
        if style:
            attr_map["style"] = style
        rendered = "".join(
            f' {name}="{html.escape(value, quote=True)}"' for name, value in attr_map.items()
        )
        self._out.append(f"<{tag}{rendered}>")

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in _VOID_TAGS:
            self.handle_endtag(tag)

    def handle_endtag(self, tag):
        if tag in _SKIP_CONTENT_TAGS:
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if self._skip_depth or ":" in tag or tag in _VOID_TAGS:
            return
        if tag == "a":
            keep = self._anchor_stack.pop() if self._anchor_stack else True
            if not keep:
                return
        self._out.append(f"</{tag}>")

    def handle_data(self, data):
        if self._skip_depth:
            return
        # HTML collapses whitespace, but the portal editor renders with
        # ``white-space: pre-wrap``, so Outlook's line-wrapped markup must be
        # collapsed here or every wrapped line becomes a blank line.
        self._out.append(html.escape(_WHITESPACE.sub(" ", data), quote=False))


def _convert_outlook_html(html_content: str, images: dict[str, dict[str, Any]]) -> str:
    """Inline Outlook stylesheet rules, embed images and normalise whitespace."""
    collector = _StyleCollector()
    collector.feed(html_content)
    collector.close()
    rules = _parse_stylesheet("\n".join(collector.css))

    converter = _OutlookHtmlConverter(rules, images)
    converter.feed(html_content)
    converter.close()
    return converter.result()


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


def sanitize_signature_html(html_content: str, *, collapse_whitespace: bool = False) -> str:
    """Sanitize signature HTML, preserving inline CSS styles and data: images.

    ``collapse_whitespace`` folds insignificant source whitespace (as Outlook
    exports wrap their markup) so it does not render under ``pre-wrap``.
    """
    if not html_content:
        return ""

    cleaned = nh3.clean(
        html_content,
        tags=_SIGNATURE_ALLOWED_TAGS,
        attributes=_SIGNATURE_ATTRIBUTES,
        url_schemes=_ALLOWED_PROTOCOLS,
        attribute_filter=_filter_attribute,
    )
    if collapse_whitespace:
        cleaned = _WHITESPACE.sub(" ", cleaned)
        cleaned = _BLOCK_TAG_WHITESPACE.sub(r"\1", cleaned)
    else:
        cleaned = cleaned.replace("\r\n", "\n").replace("\r", "\n")
    return cleaned.strip()


def _sanitize_signature_html(html_content: str) -> str:
    """Sanitize imported Outlook HTML (whitespace collapsed)."""
    return sanitize_signature_html(html_content, collapse_whitespace=True)
