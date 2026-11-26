"""Restricted resource loading for HTML-to-PDF rendering (WeasyPrint).

WeasyPrint's default fetcher opens ``file:`` and ``ftp:`` URLs and follows
HTTP redirects, so any URL that reaches rendered HTML (a rich-text image, a
product image, a relative path resolved against a ``base_url`` taken from the
request's ``Host`` header) becomes a server-side request. This fetcher allows
only:

* ``data:`` URIs;
* the portal's own ``/static`` files, read from disk instead of over HTTP;
* ``http``/``https`` URLs on the configured ``PORTAL_URL`` host (private
  addresses allowed, loopback/link-local/metadata refused);
* other ``http``/``https`` URLs only when they resolve to public addresses.

Redirects are never followed, so an allowed URL cannot bounce elsewhere.
"""

from __future__ import annotations

import mimetypes
from pathlib import Path
from urllib.parse import unquote, urlsplit

from weasyprint.urls import URLFetcher, URLFetcherResponse

from app.core.config import get_settings
from app.services.outbound_url_guard import UnsafeOutboundURLError, validate_outbound_url

__all__ = ["SafePdfUrlFetcher", "URLFetcherResponse", "pdf_url_fetcher"]

_STATIC_ROOT = Path(__file__).resolve().parents[1] / "static"
_STATIC_PREFIX = "/static/"
# User uploads live under /static/uploads and are never inlined from disk.
_STATIC_EXCLUDED_DIRS = frozenset({"uploads"})
_FETCH_TIMEOUT_SECONDS = 10


def _portal_hostname() -> str | None:
    portal_url = get_settings().portal_url
    if not portal_url:
        return None
    hostname = urlsplit(str(portal_url)).hostname
    return hostname.lower() if hostname else None


def _static_file(path: str) -> Path:
    relative = unquote(path[len(_STATIC_PREFIX):]).replace("\\", "/")
    root = _STATIC_ROOT.resolve()
    candidate = (root / relative).resolve()
    if root not in candidate.parents:
        raise UnsafeOutboundURLError("PDF resource path escapes the static directory")
    if candidate.relative_to(root).parts[0].lower() in _STATIC_EXCLUDED_DIRS:
        raise UnsafeOutboundURLError("PDF resources cannot be read from uploads")
    if not candidate.is_file():
        raise UnsafeOutboundURLError("PDF static resource not found")
    return candidate


class SafePdfUrlFetcher(URLFetcher):
    """WeasyPrint fetcher that refuses local files and internal destinations."""

    def __init__(self, base_url: str | None = None) -> None:
        super().__init__(
            timeout=_FETCH_TIMEOUT_SECONDS,
            allowed_protocols={"data", "http", "https"},
            allow_redirects=False,
        )
        base_host = urlsplit(str(base_url or "")).hostname
        self._base_hostname = base_host.lower() if base_host else None

    def _fetch_remote(self, url: str, headers=None):
        return super().fetch(url, headers)

    def fetch(self, url, headers=None):
        parts = urlsplit(str(url).strip())
        scheme = parts.scheme.lower()
        if scheme == "data":
            return super().fetch(url, headers)
        if scheme not in {"http", "https"}:
            raise UnsafeOutboundURLError(f"PDF resources cannot use the {scheme or 'empty'} scheme")

        hostname = (parts.hostname or "").lower()
        portal_hostname = _portal_hostname()
        same_origin = hostname and hostname in {self._base_hostname, portal_hostname}
        if same_origin and parts.path.startswith(_STATIC_PREFIX):
            path = _static_file(parts.path)
            content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            return URLFetcherResponse(url, body=path.read_bytes(), headers={"Content-Type": content_type})

        # Only the configured portal host is trusted with private addresses;
        # the request's own host comes from the Host header and is not.
        allow_private = bool(portal_hostname) and hostname == portal_hostname
        validate_outbound_url(url, allow_private=allow_private)
        return self._fetch_remote(url, headers)


def pdf_url_fetcher(base_url: str | None = None) -> SafePdfUrlFetcher:
    """Return a fetcher for ``weasyprint.HTML(..., url_fetcher=...)``."""

    return SafePdfUrlFetcher(base_url)
