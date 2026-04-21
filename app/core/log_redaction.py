"""Utilities for redacting sensitive values from log messages.

The application logs request metadata and structured events. To avoid
leaking credentials or PII into log files, these helpers scrub values whose
keys or headers look sensitive (``authorization``, ``cookie``,
``x-api-key``, ``password``, ``token``, ``secret``, ``totp``).

Callers should use :func:`redact_headers` for HTTP headers and
:func:`redact_mapping` for generic key/value structures (for example JSON
request bodies captured in logs).
"""
from __future__ import annotations

import re
from typing import Any, Mapping

_REDACTED: str = "[REDACTED]"

_SENSITIVE_HEADER_NAMES: frozenset[str] = frozenset(
    {
        "authorization",
        "proxy-authorization",
        "cookie",
        "set-cookie",
        "x-api-key",
        "x-csrf-token",
        "x-csrftoken",
        "csrf-token",
    }
)

_SENSITIVE_KEY_PATTERN = re.compile(
    r"(password|passwd|secret|token|api[_-]?key|authorization|session|cookie|totp|otp|credential)",
    re.IGNORECASE,
)


def redact_headers(headers: Mapping[str, str] | None) -> dict[str, str]:
    """Return a copy of ``headers`` with sensitive values replaced."""

    if not headers:
        return {}
    redacted: dict[str, str] = {}
    for name, value in headers.items():
        try:
            key_lower = str(name).lower()
        except Exception:  # pragma: no cover - defensive
            key_lower = ""
        if key_lower in _SENSITIVE_HEADER_NAMES or _SENSITIVE_KEY_PATTERN.search(key_lower):
            redacted[str(name)] = _REDACTED
        else:
            redacted[str(name)] = str(value)
    return redacted


def redact_mapping(data: Any, *, max_depth: int = 4) -> Any:
    """Recursively redact values whose keys look sensitive.

    Non-mapping, non-list values are returned unchanged. Depth is bounded to
    guard against deeply nested or cyclic structures.
    """

    if max_depth <= 0:
        return data
    if isinstance(data, Mapping):
        return {
            str(key): (
                _REDACTED
                if isinstance(key, str) and _SENSITIVE_KEY_PATTERN.search(key)
                else redact_mapping(value, max_depth=max_depth - 1)
            )
            for key, value in data.items()
        }
    if isinstance(data, list):
        return [redact_mapping(item, max_depth=max_depth - 1) for item in data]
    if isinstance(data, tuple):
        return tuple(redact_mapping(item, max_depth=max_depth - 1) for item in data)
    return data


__all__ = ["redact_headers", "redact_mapping"]
