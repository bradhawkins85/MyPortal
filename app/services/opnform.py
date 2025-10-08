from __future__ import annotations

import html
import re
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Optional
from urllib.parse import urljoin, urlparse, urlunparse


class OpnformValidationError(ValueError):
    """Raised when supplied OpnForm data fails validation."""


IFRAME_TAG_REGEX = re.compile(r"<iframe\b[^>]*>", re.IGNORECASE)
SCRIPT_TAG_REGEX = re.compile(r"<script\b[^>]*>[\s\S]*?</script>", re.IGNORECASE)


class _AttributeExtractor(HTMLParser):
    def __init__(self, attribute: str) -> None:
        super().__init__()
        self._target = attribute.lower()
        self.value: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, Optional[str]]]) -> None:  # type: ignore[override]
        if self.value is not None:
            return
        for name, val in attrs:
            if name and name.lower() == self._target:
                self.value = val or ""
                break


def _extract_attribute(tag: str, attribute: str) -> Optional[str]:
    parser = _AttributeExtractor(attribute)
    try:
        parser.feed(tag)
    except Exception:  # pragma: no cover - HTMLParser should not raise for these snippets
        return None
    return parser.value


def _sanitize_style(value: str) -> str:
    cleaned = re.sub(r"/\*.*?\*/", "", value, flags=re.DOTALL)
    cleaned = re.sub(r"[^a-z0-9:;,%#\.\s\-()]", "", cleaned, flags=re.IGNORECASE)
    return cleaned.strip()


def _normalise_iframe_id(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    candidate = value.strip()
    if not candidate:
        return None
    if re.fullmatch(r"[A-Za-z][A-Za-z0-9_:\\-.]*", candidate):
        return candidate
    return None


def _escape_attribute(value: str) -> str:
    return (
        html.escape(value, quote=True)
        .replace("'", "&#39;")
    )


def _normalise_url(raw_url: str) -> str:
    parsed = urlparse(raw_url)
    if not parsed.scheme or not parsed.netloc:
        raise OpnformValidationError("OpnForm URL must be absolute")
    if parsed.scheme not in {"http", "https"}:
        raise OpnformValidationError("OpnForm URL must use HTTP or HTTPS")
    # Normalise by removing redundant path segments and ensuring no fragments.
    normalised = parsed._replace(fragment="")
    return urlunparse(normalised)


@dataclass(slots=True)
class NormalizedEmbed:
    sanitized_embed_code: str
    form_url: str


def extract_allowed_host(base_url: str | None) -> str | None:
    if not base_url:
        return None
    parsed = urlparse(str(base_url))
    if not parsed.netloc:
        return None
    return parsed.netloc


def normalize_opnform_form_url(raw_url: str, *, allowed_host: str | None = None) -> str:
    trimmed = raw_url.strip()
    if not trimmed:
        raise OpnformValidationError("Form URL is required")
    normalised = _normalise_url(trimmed)
    host = urlparse(normalised).netloc
    if allowed_host and host.lower() != allowed_host.lower():
        raise OpnformValidationError("Form URL host is not allowed")
    return normalised


def normalize_opnform_embed_code(
    raw_embed_code: str,
    *,
    allowed_host: str | None = None,
) -> NormalizedEmbed:
    trimmed = raw_embed_code.strip()
    if not trimmed:
        raise OpnformValidationError("OpnForm embed code is required")

    iframe_match = IFRAME_TAG_REGEX.search(trimmed)
    if not iframe_match:
        raise OpnformValidationError("OpnForm embed code must include an iframe")
    iframe_tag = iframe_match.group(0)

    raw_src = _extract_attribute(iframe_tag, "src")
    if not raw_src:
        raise OpnformValidationError("OpnForm iframe is missing a src attribute")
    decoded_src = html.unescape(raw_src).strip()
    form_url = normalize_opnform_form_url(decoded_src, allowed_host=allowed_host)

    iframe_id = _normalise_iframe_id(_extract_attribute(iframe_tag, "id"))
    iframe_style_raw = _extract_attribute(iframe_tag, "style")
    iframe_style = (
        _sanitize_style(html.unescape(iframe_style_raw)) if iframe_style_raw else ""
    )
    if not iframe_style:
        iframe_style = "border:0;width:100%;min-height:480px;"

    attrs = [
        f'src="{_escape_attribute(form_url)}"',
        f'style="{_escape_attribute(iframe_style)}"',
        'class="form-frame"',
        'loading="lazy"',
        'allow="publickey-credentials-get *; publickey-credentials-create *"',
        'title="OpnForm form"',
        'referrerpolicy="strict-origin-when-cross-origin"',
    ]
    if iframe_id:
        attrs.append(f'id="{_escape_attribute(iframe_id)}"')

    script_snippet = ""
    script_match = SCRIPT_TAG_REGEX.search(trimmed)
    if script_match:
        script_tag = script_match.group(0)
        raw_script_src = _extract_attribute(script_tag, "src")
        if not raw_script_src:
            raise OpnformValidationError("OpnForm script embed must include a src attribute")
        script_src = html.unescape(raw_script_src).strip()
        base_for_script = form_url
        if not urlparse(script_src).scheme:
            script_src = urljoin(base_for_script, script_src)
        script_url = _normalise_url(script_src)
        script_host = urlparse(script_url).netloc
        expected_host = allowed_host or urlparse(form_url).netloc
        if script_host.lower() != expected_host.lower():
            raise OpnformValidationError("OpnForm script host must match the iframe host")
        script_snippet = (
            "\n"
            + f'<script src="{_escape_attribute(script_url)}" async data-opnform-embed="support"></script>'
        )

    sanitized_embed_code = f"<iframe {' '.join(attrs)}></iframe>{script_snippet}"
    return NormalizedEmbed(sanitized_embed_code=sanitized_embed_code, form_url=form_url)
