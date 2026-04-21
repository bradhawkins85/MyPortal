"""Helpers for computing audit diffs and redacting sensitive data.

These utilities power :func:`app.services.audit.record` so route handlers can
pass full "before" and "after" snapshots and have the audit row store only the
fields that actually changed, with sensitive values masked out.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import date, datetime, time, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any

# Substring patterns (case-insensitive) that mark a field as sensitive. Any key
# matching one of these is masked in the audit record. Keep this list narrow to
# avoid masking innocuous fields - extend deliberately.
_SENSITIVE_KEY_PATTERNS: tuple[str, ...] = (
    "password",
    "passwd",
    "pwd",
    "secret",
    "token",
    "api_key",
    "apikey",
    "access_key",
    "private_key",
    "client_secret",
    "refresh_token",
    "totp_secret",
    "encryption_key",
    "session_id",
    "authorization",
    "auth_token",
    "credential",
)

# Sentinel used in the diff output to indicate a value was redacted.
REDACTED = "***REDACTED***"

# Maximum string length stored in audit rows to keep payloads compact. Long
# free-text fields (descriptions, notes, ticket reply bodies, etc.) are
# truncated with a marker to make it obvious in the UI that the audit captured
# only metadata, not the full body.
MAX_FIELD_LENGTH = 500
TRUNCATION_SUFFIX = "...[truncated]"


def is_sensitive_key(key: Any) -> bool:
    """Return True if a field name matches any of the sensitive-key patterns."""

    if not isinstance(key, str):
        return False
    lowered = key.lower()
    return any(pattern in lowered for pattern in _SENSITIVE_KEY_PATTERNS)


def _coerce(value: Any) -> Any:
    """Normalise a value so equality comparison and JSON serialisation work."""

    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, datetime):
        target = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
        return target.astimezone(timezone.utc).isoformat()
    if isinstance(value, (date, time)):
        return value.isoformat()
    if isinstance(value, Decimal):
        if value == value.to_integral():
            return int(value)
        return float(value)
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, Mapping):
        return {str(key): _coerce(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_coerce(item) for item in value]
    # Pydantic v2 models expose model_dump(); v1 dict(); SQLAlchemy rows behave
    # like dicts. Fall back to str() for anything we cannot serialise cleanly.
    dump = getattr(value, "model_dump", None)
    if callable(dump):
        try:
            return _coerce(dump())
        except Exception:  # pragma: no cover - defensive
            return str(value)
    dump_v1 = getattr(value, "dict", None)
    if callable(dump_v1):
        try:
            return _coerce(dump_v1())
        except Exception:  # pragma: no cover - defensive
            return str(value)
    return str(value)


def _truncate(value: Any) -> Any:
    """Cap long string fields to keep audit payloads small."""

    if isinstance(value, str) and len(value) > MAX_FIELD_LENGTH:
        return value[:MAX_FIELD_LENGTH] + TRUNCATION_SUFFIX
    return value


def _normalise(value: Any) -> Any:
    coerced = _coerce(value)
    if isinstance(coerced, dict):
        return {key: _normalise(item) for key, item in coerced.items()}
    if isinstance(coerced, list):
        return [_normalise(item) for item in coerced]
    return _truncate(coerced)


def _to_dict(value: Any) -> dict[str, Any] | None:
    """Coerce supported inputs into a flat dict for diffing."""

    if value is None:
        return None
    coerced = _coerce(value)
    if isinstance(coerced, dict):
        return coerced
    return None


def redact(value: Any, *, sensitive_extra_keys: tuple[str, ...] = ()) -> Any:
    """Return a deep-copied version of ``value`` with sensitive fields masked.

    Strings are truncated to keep the audit payload compact. Mapping keys are
    matched case-insensitively against :data:`_SENSITIVE_KEY_PATTERNS` plus any
    extra keys provided by the caller (e.g. fields that are sensitive only in
    a specific context, like ticket reply ``body``).
    """

    extras = tuple(key.lower() for key in sensitive_extra_keys)

    def _is_sensitive(key: Any) -> bool:
        if is_sensitive_key(key):
            return True
        return isinstance(key, str) and key.lower() in extras

    coerced = _coerce(value)
    if isinstance(coerced, dict):
        result: dict[str, Any] = {}
        for key, item in coerced.items():
            if _is_sensitive(key):
                result[str(key)] = REDACTED if item is not None else None
            else:
                result[str(key)] = redact(item, sensitive_extra_keys=sensitive_extra_keys)
        return result
    if isinstance(coerced, list):
        return [redact(item, sensitive_extra_keys=sensitive_extra_keys) for item in coerced]
    return _truncate(coerced)


def diff(
    before: Any,
    after: Any,
    *,
    sensitive_extra_keys: tuple[str, ...] = (),
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Compute a field-level diff between two snapshots.

    Returns a ``(previous_value, new_value)`` tuple where each side contains
    only the fields whose value changed. Both sides have the same keys to make
    it easy to render side-by-side in the admin UI.

    * If ``before`` is ``None`` (creation), all fields of ``after`` are
      returned and ``previous_value`` will be ``None``.
    * If ``after`` is ``None`` (deletion), all fields of ``before`` are
      returned and ``new_value`` will be ``None``.
    * If both can be coerced into mappings, only changed keys are reported.
    * If neither is a mapping, the raw (redacted/normalised) values are
      returned as-is.
    """

    before_dict = _to_dict(before)
    after_dict = _to_dict(after)

    if before is None and after is None:
        return None, None

    if before_dict is None and after_dict is None:
        return (
            redact(before, sensitive_extra_keys=sensitive_extra_keys) if before is not None else None,
            redact(after, sensitive_extra_keys=sensitive_extra_keys) if after is not None else None,
        )

    # Creation: no previous, full after snapshot (redacted)
    if before is None and after_dict is not None:
        return None, redact(after_dict, sensitive_extra_keys=sensitive_extra_keys)

    # Deletion: previous snapshot only (redacted)
    if after is None and before_dict is not None:
        return redact(before_dict, sensitive_extra_keys=sensitive_extra_keys), None

    assert before_dict is not None and after_dict is not None
    keys = set(before_dict.keys()) | set(after_dict.keys())
    prev_changed: dict[str, Any] = {}
    new_changed: dict[str, Any] = {}
    for key in sorted(keys):
        prev_value = _normalise(before_dict.get(key))
        new_value = _normalise(after_dict.get(key))
        if prev_value == new_value:
            continue
        if is_sensitive_key(key) or (
            isinstance(key, str) and key.lower() in tuple(k.lower() for k in sensitive_extra_keys)
        ):
            prev_changed[str(key)] = REDACTED if before_dict.get(key) is not None else None
            new_changed[str(key)] = REDACTED if after_dict.get(key) is not None else None
        else:
            prev_changed[str(key)] = redact(
                before_dict.get(key), sensitive_extra_keys=sensitive_extra_keys
            )
            new_changed[str(key)] = redact(
                after_dict.get(key), sensitive_extra_keys=sensitive_extra_keys
            )
    if not prev_changed and not new_changed:
        return None, None
    return prev_changed or None, new_changed or None


# Reply body should never reach the audit log: tickets often contain rich text
# that may include PII, so callers building a ticket-reply audit row should
# pass the body via metadata which routes through redact() with this key.
TICKET_REPLY_REDACT_KEYS: tuple[str, ...] = ("body", "html", "text", "content")


_HTML_TAG_RE = re.compile(r"<[^>]+>")


def summarise_reply_body(body: str | None) -> dict[str, int]:
    """Return non-sensitive descriptive metadata for a ticket reply body.

    Captures only counts and lengths so the audit row reflects that a reply
    was made without storing the actual content.
    """

    if not body:
        return {"length": 0, "word_count": 0}
    text = _HTML_TAG_RE.sub(" ", body).strip()
    word_count = len(text.split()) if text else 0
    return {
        "length": len(body),
        "text_length": len(text),
        "word_count": word_count,
    }
