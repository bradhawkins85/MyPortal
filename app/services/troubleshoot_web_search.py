"""Optional public web search for the multi-stage AI troubleshooter.

Enabled with ``TROUBLESHOOT_WEB_SEARCH_ENABLED``. The troubleshooter passes the
generic search terms its research stage wrote (never raw ticket text) to the
configured provider, then reads the top public pages so the planning stage can
extract their troubleshooting steps.

Providers:

* ``searxng`` - a SearXNG instance at ``TROUBLESHOOT_WEB_SEARCH_URL`` with the
  JSON output format enabled.
* ``brave`` - the Brave Search API, keyed by ``TROUBLESHOOT_WEB_SEARCH_API_KEY``.

Result pages are fetched with the outbound URL guard (no private, loopback or
metadata addresses, checked on every redirect hop) and bounded in size.
"""

from __future__ import annotations

import html
import re
from collections.abc import Sequence
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlparse

import httpx

from app.core.config import get_settings
from app.core.logging import log_warning
from app.services.monitored_http import monitored_client
from app.services.outbound_url_guard import (
    UnsafeOutboundURLError,
    redirect_guard_hooks,
    validate_outbound_url_async,
)

BRAVE_SEARCH_URL = "https://api.search.brave.com/res/v1/web/search"
USER_AGENT = "MyPortal-Troubleshooter/1.0 (+https://github.com/bradhawkins85/MyPortal)"

_SEARCH_TIMEOUT_SECONDS = 15
_PAGE_TIMEOUT_SECONDS = 15
_MAX_PAGE_BYTES = 768 * 1024
_PAGE_TEXT_CHARS = 6000
_MAX_REDIRECTS = 3
_MAX_RESULTS = 8

_TAG_RE = re.compile(r"<[^<>]*>")
_INLINE_SPACE_RE = re.compile(r"[ \t\r\f\v]+")
_BLANK_LINES_RE = re.compile(r"\n\s*\n+")


def is_enabled() -> bool:
    return bool(get_settings().troubleshoot_web_search_enabled)


def max_pages() -> int:
    return max(1, min(5, int(get_settings().troubleshoot_web_search_max_pages or 3)))


def _clean(value: Any, limit: int) -> str:
    text = re.sub(r"\s+", " ", html.unescape(_TAG_RE.sub(" ", str(value or "")))).strip()
    return text[:limit]


def _public_http_url(value: Any) -> str:
    url = str(value or "").strip()
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname or len(url) > 500:
        return ""
    return url


def _result(title: Any, url: Any, snippet: Any) -> dict[str, str] | None:
    clean_url = _public_http_url(url)
    clean_title = _clean(title, 200)
    if not clean_url or not clean_title:
        return None
    return {"title": clean_title, "url": clean_url, "snippet": _clean(snippet, 400)}


async def _search_searxng(client: httpx.AsyncClient, query: str) -> list[dict[str, str]]:
    base_url = str(get_settings().troubleshoot_web_search_url or "").strip().rstrip("/")
    if not base_url:
        raise ValueError("TROUBLESHOOT_WEB_SEARCH_URL is not set")
    # The SearXNG instance is admin-configured and may live on the LAN.
    await validate_outbound_url_async(base_url, allow_private=True)
    response = await client.get(
        f"{base_url}/search",
        params={"q": query, "format": "json", "safesearch": 1},
    )
    response.raise_for_status()
    payload = response.json()
    results = payload.get("results") if isinstance(payload, dict) else None
    return [
        item
        for item in (
            _result(entry.get("title"), entry.get("url"), entry.get("content"))
            for entry in (results if isinstance(results, list) else [])
            if isinstance(entry, dict)
        )
        if item
    ]


async def _search_brave(client: httpx.AsyncClient, query: str) -> list[dict[str, str]]:
    api_key = str(get_settings().troubleshoot_web_search_api_key or "").strip()
    if not api_key:
        raise ValueError("TROUBLESHOOT_WEB_SEARCH_API_KEY is not set")
    response = await client.get(
        BRAVE_SEARCH_URL,
        params={"q": query, "count": _MAX_RESULTS, "safesearch": "moderate"},
        headers={"Accept": "application/json", "X-Subscription-Token": api_key},
    )
    response.raise_for_status()
    payload = response.json()
    web = payload.get("web") if isinstance(payload, dict) else None
    results = web.get("results") if isinstance(web, dict) else None
    return [
        item
        for item in (
            _result(entry.get("title"), entry.get("url"), entry.get("description"))
            for entry in (results if isinstance(results, list) else [])
            if isinstance(entry, dict)
        )
        if item
    ]


async def search(query: str) -> list[dict[str, str]]:
    """Return ``[{title, url, snippet}]`` for ``query`` from the configured provider."""

    query = _clean(query, 200)
    if not query:
        return []
    provider = str(get_settings().troubleshoot_web_search_provider or "searxng").strip().lower()
    async with monitored_client(
        httpx.AsyncClient,
        timeout=_SEARCH_TIMEOUT_SECONDS,
        headers={"User-Agent": USER_AGENT},
    ) as client:
        if provider == "brave":
            results = await _search_brave(client, query)
        elif provider == "searxng":
            results = await _search_searxng(client, query)
        else:
            raise ValueError(f"Unknown TROUBLESHOOT_WEB_SEARCH_PROVIDER: {provider}")
    return results[:_MAX_RESULTS]


class _TextExtractor(HTMLParser):
    """Collect a page's visible text, one block element per line."""

    _SKIP = frozenset(
        {"head", "title", "script", "style", "noscript", "svg", "nav", "footer", "header", "form", "iframe", "template"}
    )
    _BLOCK = frozenset(
        {"p", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6", "br", "tr", "section", "article", "pre", "td", "dt", "dd"}
    )

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.title_parts: list[str] = []
        self._skip_depth = 0
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        if tag == "title":
            self._in_title = True
        if tag in self._SKIP:
            self._skip_depth += 1
        elif tag in self._BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
        if tag in self._SKIP:
            self._skip_depth = max(0, self._skip_depth - 1)
        elif tag in self._BLOCK:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title_parts.append(data)
        if not self._skip_depth:
            self.parts.append(data)


def html_to_text(markup: str) -> tuple[str, str]:
    """Return ``(title, text)`` from an HTML page, keeping line structure."""

    parser = _TextExtractor()
    try:
        parser.feed(markup)
        parser.close()
    except Exception:  # noqa: BLE001 - malformed markup keeps what was parsed
        pass
    title = _clean("".join(parser.title_parts), 200)
    lines = [_INLINE_SPACE_RE.sub(" ", line).strip() for line in "".join(parser.parts).split("\n")]
    text = _BLANK_LINES_RE.sub("\n\n", "\n".join(line for line in lines if line)).strip()
    return title, text


async def fetch_page_text(url: str) -> str:
    """Fetch a public page and return bounded plain text (empty on failure)."""

    try:
        await validate_outbound_url_async(url, allow_private=False)
    except UnsafeOutboundURLError as exc:
        log_warning("Troubleshooter skipped unsafe web result", url=url, error=str(exc))
        return ""
    try:
        async with monitored_client(
            httpx.AsyncClient,
            timeout=_PAGE_TIMEOUT_SECONDS,
            follow_redirects=True,
            max_redirects=_MAX_REDIRECTS,
            headers={"User-Agent": USER_AGENT, "Accept": "text/html,text/plain;q=0.9"},
            event_hooks=redirect_guard_hooks(allow_private=False),
        ) as client:
            async with client.stream("GET", url) as response:
                response.raise_for_status()
                content_type = response.headers.get("content-type", "").lower()
                if "html" not in content_type and "text/plain" not in content_type:
                    return ""
                chunks: list[bytes] = []
                size = 0
                async for chunk in response.aiter_bytes():
                    chunks.append(chunk)
                    size += len(chunk)
                    if size >= _MAX_PAGE_BYTES:
                        break
                encoding = response.encoding or "utf-8"
    except Exception as exc:  # noqa: BLE001 - one bad page must not stop the run
        log_warning("Troubleshooter could not read web result", url=url, error=str(exc))
        return ""
    raw = b"".join(chunks)[:_MAX_PAGE_BYTES].decode(encoding, errors="replace")
    if "html" in content_type:
        _, raw = html_to_text(raw)
    return raw[:_PAGE_TEXT_CHARS]


async def research(terms: Sequence[str]) -> list[dict[str, str]]:
    """Search for the first usable term and read the top pages.

    Returns ``[{title, url, snippet, content}]``; pages that cannot be read
    are dropped.
    """

    pages: list[dict[str, str]] = []
    seen: set[str] = set()
    limit = max_pages()
    for term in [t for t in terms if str(t or "").strip()][:2]:
        try:
            results = await search(term)
        except Exception as exc:  # noqa: BLE001 - reported by the caller as no results
            log_warning("Troubleshooter web search failed", error=str(exc))
            raise
        for result in results:
            if result["url"] in seen:
                continue
            seen.add(result["url"])
            content = await fetch_page_text(result["url"])
            if not content.strip():
                continue
            pages.append({**result, "content": content})
            if len(pages) >= limit:
                return pages
    return pages
