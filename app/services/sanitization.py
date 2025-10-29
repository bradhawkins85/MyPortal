"""Utilities for sanitising and normalising rich text content."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Mapping

import bleach

_ALLOWED_TAGS: tuple[str, ...] = (
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

_ALLOWED_ATTRIBUTES: Mapping[str, list[str]] = {
    "a": ["href", "title", "target", "rel"],
    "img": ["src", "alt", "title", "width", "height", "loading", "decoding"],
    "span": ["data-mention"],
    "table": ["role"],
}

_ALLOWED_PROTOCOLS: tuple[str, ...] = ("http", "https", "mailto", "tel", "data")


@dataclass(slots=True)
class SanitizedRichText:
    """Container for sanitised HTML and its derived text content."""

    html: str
    text_content: str
    has_rich_content: bool


def sanitize_rich_text(value: str | None) -> SanitizedRichText:
    """Clean potentially unsafe HTML and normalise newlines.

    The function keeps a small subset of semantic formatting tags so replies can
    retain emphasis, lists, and links while stripping scripts and unsafe
    attributes. Plain text newlines are converted to ``<br />`` markers so legacy
    replies that were stored without HTML continue to display as expected.
    """

    raw_text = (value or "").strip()
    cleaned = bleach.clean(
        raw_text,
        tags=_ALLOWED_TAGS,
        attributes=_ALLOWED_ATTRIBUTES,
        protocols=_ALLOWED_PROTOCOLS,
        strip=True,
    )
    normalised = cleaned.replace("\r\n", "\n").replace("\r", "\n").replace("\u200b", "")
    if normalised:
        if "<" not in normalised and ">" not in normalised:
            html_value = normalised.replace("\n", "<br />")
        else:
            html_value = normalised
    else:
        html_value = ""
    text_content = bleach.clean(html_value, tags=[], strip=True).strip()
    contains_media = bool(re.search(r"<img\b[^>]*\bsrc=", html_value, flags=re.IGNORECASE))
    if not text_content and not contains_media:
        html_value = ""
    has_content = bool(text_content) or contains_media
    return SanitizedRichText(html=html_value, text_content=text_content, has_rich_content=has_content)


__all__ = ["SanitizedRichText", "sanitize_rich_text"]
