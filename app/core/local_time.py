"""Render stored UTC timestamps so the browser shows them in local time.

Templates call ``local_time(value)`` to emit a ``<span data-utc=…>`` that
``app/static/js/local_time.js`` rewrites in the viewer's time zone. The text
inside the span is a UTC fallback shown only before (or without) JavaScript.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from markupsafe import Markup, escape

_FORMATS = {"datetime", "date", "time", "short"}


def _as_utc_datetime(value: Any) -> datetime | date | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    candidate = text.replace("Z", "+00:00").replace("z", "+00:00")
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError:
        try:
            return date.fromisoformat(text)
        except ValueError:
            return None
    if len(text) == 10:
        return parsed.date()
    return _as_utc_datetime(parsed)


def utc_iso(value: Any) -> str:
    """Return an ISO-8601 UTC string (``…Z``), a ``YYYY-MM-DD`` date, or ``''``."""

    parsed = _as_utc_datetime(value)
    if parsed is None:
        return ""
    if isinstance(parsed, datetime):
        return parsed.replace(microsecond=0).isoformat().replace("+00:00", "Z")
    return parsed.isoformat()


def _fallback_text(parsed: datetime | date, fmt: str) -> str:
    if not isinstance(parsed, datetime):
        return parsed.isoformat()
    if fmt == "date":
        return parsed.strftime("%Y-%m-%d")
    if fmt == "time":
        return parsed.strftime("%H:%M UTC")
    return parsed.strftime("%Y-%m-%d %H:%M UTC")


def utc_text(value: Any, fmt: str = "datetime") -> str:
    """Plain-text UTC fallback (``2026-01-02 03:04 UTC``) for attributes.

    Pair it with ``data-utc-title="{{ utc_iso(value) }}"`` so the browser
    swaps this text for local time inside the element's ``title``.
    """

    parsed = _as_utc_datetime(value)
    if parsed is None:
        return ""
    return _fallback_text(parsed, fmt if fmt in _FORMATS else "datetime")


def local_time(value: Any, fmt: str = "datetime", empty: str = "—") -> Markup:
    """Render ``value`` as a span the browser localises to its own time zone."""

    mode = fmt if fmt in _FORMATS else "datetime"
    parsed = _as_utc_datetime(value)
    if parsed is None:
        return Markup(escape(empty))
    iso = utc_iso(parsed)
    return Markup(
        '<span data-utc="{iso}" data-utc-format="{mode}">{text}</span>'
    ).format(iso=iso, mode=mode, text=_fallback_text(parsed, mode))


def register(env: Any) -> None:
    """Expose the helpers to a Jinja environment."""

    env.globals["local_time"] = local_time
    env.globals["utc_iso"] = utc_iso
    env.globals["utc_text"] = utc_text
    env.filters["local_time"] = local_time
    env.filters["utc_iso"] = utc_iso
