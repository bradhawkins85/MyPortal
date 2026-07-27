"""Utilities for sanitising and normalising rich text content."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Mapping

import nh3

_ALLOWED_TAGS: frozenset[str] = frozenset(
    (
        "a",
        "b",
        "blockquote",
        "br",
        "code",
        "img",
        "div",
        "em",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "hr",
        "i",
        "iframe",
        "li",
        "ol",
        "p",
        "pre",
        "span",
        "strong",
        "sub",
        "sup",
        "table",
        "tbody",
        "td",
        "th",
        "thead",
        "tr",
        "u",
        "ul",
    )
)

_ALLOWED_ATTRIBUTES: dict[str, set[str]] = {
    "a": {"href", "title", "target"},
    "img": {"src", "alt", "title", "width", "height", "loading", "decoding"},
    "iframe": {
        "src",
        "title",
        "width",
        "height",
        "loading",
        "allow",
        "allowfullscreen",
        "referrerpolicy",
    },
    "span": {"data-mention"},
    "table": {"role"},
}

_ALLOWED_PROTOCOLS: frozenset[str] = frozenset(
    ("http", "https", "mailto", "tel", "data")
)

_INLINE_CSS_PATTERN = re.compile(r"(?is)^\s*(?:[a-z0-9._#-]+\s*\{[^}]*\}\s*)+")
_EMAIL_HEADER_PATTERN = re.compile(r"^(from|sent|to|subject|cc):", re.IGNORECASE)
_EMAIL_THREAD_DIVIDER = re.compile(r"^-{2,}\s*original message\s*-{2,}$", re.IGNORECASE)
_STYLE_OPEN_TAG = "<style"
_STYLE_CLOSE_TAG = "</style>"


@dataclass(slots=True)
class SanitizedRichText:
    """Container for sanitised HTML and its derived text content."""

    html: str
    text_content: str
    has_rich_content: bool


def _strip_html_tags(value: str) -> str:
    """Remove complete non-empty angle-bracket tags in linear time.

    Empty tags (``<>``) and unclosed tags are preserved.
    """
    if "<" not in value or ">" not in value:
        return value

    cleaned_parts: list[str] = []
    cursor = 0
    while True:
        start = value.find("<", cursor)
        if start == -1:
            cleaned_parts.append(value[cursor:])
            break

        end = value.find(">", start + 1)
        if end == -1:
            cleaned_parts.append(value[cursor:])
            break
        if end == start + 1:
            cleaned_parts.append(value[cursor : end + 1])
            cursor = end + 1
            continue

        cleaned_parts.append(value[cursor:start])
        cursor = end + 1

    return "".join(cleaned_parts)


def _strip_quoted_email_headers(value: str) -> str:
    """Remove common quoted email header blocks from replies.

    This trims sections that start with header-like prefixes such as
    "From:", "Sent:", or the "Original Message" divider. It helps keep
    imported email replies focused on the latest response instead of the
    full quoted thread.
    """

    lines = value.splitlines()
    for idx, line in enumerate(lines):
        normalised = _strip_html_tags(line).strip()
        if _EMAIL_THREAD_DIVIDER.match(normalised):
            return "\n".join(lines[:idx]).rstrip()
        if _EMAIL_HEADER_PATTERN.match(normalised):
            header_hits = 0
            header_prefixes: set[str] = set()
            for candidate in lines[idx : idx + 6]:
                candidate_text = _strip_html_tags(candidate).strip()
                if _EMAIL_HEADER_PATTERN.match(candidate_text):
                    header_hits += 1
                    header_prefixes.add(candidate_text.split(":", 1)[0].strip().lower())
            # Only strip clearly quoted header blocks that include typical message
            # metadata such as Subject, Sent, or Date. This avoids trimming legitimate
            # content (e.g. voicemail summaries) that happen to include simple From/To
            # lines within the body.
            if header_hits >= 2 and header_prefixes.intersection(
                {"subject", "sent", "date"}
            ):
                return "\n".join(lines[:idx]).rstrip()
    return value


def _strip_style_blocks(value: str) -> str:
    """Remove complete <style>...</style> blocks in linear time."""
    lower_value = value.lower()
    if _STYLE_OPEN_TAG not in lower_value:
        return value

    cleaned_parts: list[str] = []
    cursor = 0
    while True:
        start = lower_value.find(_STYLE_OPEN_TAG, cursor)
        if start == -1:
            cleaned_parts.append(value[cursor:])
            break

        tag_end = lower_value.find(">", start + len(_STYLE_OPEN_TAG))
        if tag_end == -1:
            cleaned_parts.append(value[cursor:])
            break

        block_end = lower_value.find(_STYLE_CLOSE_TAG, tag_end + 1)
        if block_end == -1:
            cleaned_parts.append(value[cursor:])
            break

        cleaned_parts.append(value[cursor:start])
        cursor = block_end + len(_STYLE_CLOSE_TAG)

    return "".join(cleaned_parts)


def sanitize_rich_text(value: str | None) -> SanitizedRichText:
    """Clean potentially unsafe HTML and normalise newlines.

    The function keeps a small subset of semantic formatting tags so replies can
    retain emphasis, lists, and links while stripping scripts and unsafe
    attributes. Plain text newlines are converted to ``<br />`` markers so legacy
    replies that were stored without HTML continue to display as expected.
    """

    raw_text = (value or "").strip()
    if raw_text:
        raw_text = _strip_style_blocks(raw_text)
        raw_text = _INLINE_CSS_PATTERN.sub("", raw_text)
        raw_text = _strip_quoted_email_headers(raw_text)
    cleaned = nh3.clean(
        raw_text,
        tags=_ALLOWED_TAGS,
        attributes=_ALLOWED_ATTRIBUTES,
        url_schemes=_ALLOWED_PROTOCOLS,
    )
    normalised = cleaned.replace("\r\n", "\n").replace("\r", "\n").replace("\u200b", "")
    if normalised:
        if "<" not in normalised and ">" not in normalised:
            html_value = normalised.replace("\n", "<br />")
        else:
            html_value = normalised
    else:
        html_value = ""
    text_content = nh3.clean(html_value, tags=frozenset()).strip()
    contains_media = bool(
        re.search(r"<(?:img|iframe)\b[^>]*\bsrc=", html_value, flags=re.IGNORECASE)
    )
    if not text_content and not contains_media:
        html_value = ""
    has_content = bool(text_content) or contains_media
    return SanitizedRichText(
        html=html_value, text_content=text_content, has_rich_content=has_content
    )


__all__ = ["SanitizedRichText", "sanitize_rich_text"]
