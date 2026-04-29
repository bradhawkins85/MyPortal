"""Tests for the Trello webhook public callback URL resolution.

The function under test (`_build_public_callback_url`) decides what URL to
register with Trello when the operator clicks "Register webhook". Getting
this wrong is the root cause of Trello sending GETs (instead of POSTs) to
MyPortal: if the URL is registered as ``http://``, the reverse proxy's
HTTP→HTTPS 301 redirect downgrades Trello's event POSTs to GETs.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from app.api.routes.trello import _WEBHOOK_PATH, _build_public_callback_url


def _make_request(headers: dict[str, str], scheme: str = "http", host: str = "internal:8000"):
    """Build a minimal Request-like stub for `_build_public_callback_url`."""
    return SimpleNamespace(
        headers={k.lower(): v for k, v in headers.items()},
        url=SimpleNamespace(scheme=scheme, netloc=host),
        base_url=f"{scheme}://{host}/",
    )


def _settings_with(public_base_url: str | None):
    return SimpleNamespace(public_base_url=public_base_url)


# ---------------------------------------------------------------------------
# 1. PUBLIC_BASE_URL setting wins over everything
# ---------------------------------------------------------------------------

def test_uses_public_base_url_setting_when_set():
    request = _make_request({"host": "internal:8000"}, scheme="http")
    with patch(
        "app.api.routes.trello.get_settings",
        return_value=_settings_with("https://portal.example.com"),
    ):
        url = _build_public_callback_url(request)
    assert url == f"https://portal.example.com{_WEBHOOK_PATH}"


def test_public_base_url_setting_strips_trailing_slash():
    request = _make_request({"host": "internal:8000"})
    with patch(
        "app.api.routes.trello.get_settings",
        return_value=_settings_with("https://portal.example.com/"),
    ):
        url = _build_public_callback_url(request)
    assert url == f"https://portal.example.com{_WEBHOOK_PATH}"


def test_public_base_url_setting_assumes_https_when_scheme_missing():
    request = _make_request({"host": "internal:8000"})
    with patch(
        "app.api.routes.trello.get_settings",
        return_value=_settings_with("portal.example.com"),
    ):
        url = _build_public_callback_url(request)
    assert url == f"https://portal.example.com{_WEBHOOK_PATH}"


# ---------------------------------------------------------------------------
# 2. X-Forwarded-Proto / X-Forwarded-Host are honored
# ---------------------------------------------------------------------------

def test_uses_forwarded_proto_and_host():
    request = _make_request(
        {
            "host": "internal:8000",
            "x-forwarded-proto": "https",
            "x-forwarded-host": "portal.example.com",
            "x-forwarded-for": "1.2.3.4",
        },
        scheme="http",
    )
    with patch(
        "app.api.routes.trello.get_settings",
        return_value=_settings_with(None),
    ):
        url = _build_public_callback_url(request)
    assert url == f"https://portal.example.com{_WEBHOOK_PATH}"


def test_forwarded_headers_take_first_value_in_list():
    request = _make_request(
        {
            "host": "internal:8000",
            "x-forwarded-proto": "https, http",
            "x-forwarded-host": "portal.example.com, internal",
            "x-forwarded-for": "1.2.3.4",
        }
    )
    with patch(
        "app.api.routes.trello.get_settings",
        return_value=_settings_with(None),
    ):
        url = _build_public_callback_url(request)
    assert url == f"https://portal.example.com{_WEBHOOK_PATH}"


# ---------------------------------------------------------------------------
# 3. Behind-a-proxy heuristic: default to https when X-Forwarded-For is
#    present but X-Forwarded-Proto is not. This is the fix for the reported
#    bug: the customer's proxy forwards X-Forwarded-For but not -Proto, so
#    `request.url.scheme` is "http" and the registered callback was http://.
# ---------------------------------------------------------------------------

def test_defaults_to_https_when_proxy_detected_without_forwarded_proto():
    request = _make_request(
        {
            "host": "portal.example.com",
            "x-forwarded-for": "104.192.142.248",
        },
        scheme="http",
    )
    with patch(
        "app.api.routes.trello.get_settings",
        return_value=_settings_with(None),
    ):
        url = _build_public_callback_url(request)
    assert url == f"https://portal.example.com{_WEBHOOK_PATH}"


def test_explicit_forwarded_proto_http_is_respected_even_with_xff():
    """If the proxy explicitly says http (rare but possible), respect it."""
    request = _make_request(
        {
            "host": "portal.example.com",
            "x-forwarded-proto": "http",
            "x-forwarded-for": "104.192.142.248",
        }
    )
    with patch(
        "app.api.routes.trello.get_settings",
        return_value=_settings_with(None),
    ):
        url = _build_public_callback_url(request)
    assert url == f"http://portal.example.com{_WEBHOOK_PATH}"


# ---------------------------------------------------------------------------
# 4. No proxy at all – fall back to the request scheme
# ---------------------------------------------------------------------------

def test_no_proxy_uses_request_scheme():
    request = _make_request({"host": "localhost:8000"}, scheme="http")
    with patch(
        "app.api.routes.trello.get_settings",
        return_value=_settings_with(None),
    ):
        url = _build_public_callback_url(request)
    assert url == f"http://localhost:8000{_WEBHOOK_PATH}"


# ---------------------------------------------------------------------------
# 5. Defensive: spoofed/unexpected scheme is normalised to https
# ---------------------------------------------------------------------------

def test_unexpected_forwarded_scheme_is_normalised_to_https():
    request = _make_request(
        {
            "host": "portal.example.com",
            "x-forwarded-proto": "javascript",
            "x-forwarded-for": "1.2.3.4",
        }
    )
    with patch(
        "app.api.routes.trello.get_settings",
        return_value=_settings_with(None),
    ):
        url = _build_public_callback_url(request)
    assert url == f"https://portal.example.com{_WEBHOOK_PATH}"
