from __future__ import annotations

import hmac
import secrets
from dataclasses import dataclass
from hashlib import sha256
from typing import Final

from app.core.config import get_settings


_settings = get_settings()
_PEPPER: Final[bytes] = _settings.secret_key.encode("utf-8")
_DEFAULT_PREFIX_LENGTH: Final[int] = 8


@dataclass(slots=True, frozen=True)
class GeneratedApiKey:
    """Represents a freshly generated API key."""

    value: str
    hashed: str
    prefix: str


def generate_api_key(*, prefix_length: int = _DEFAULT_PREFIX_LENGTH) -> GeneratedApiKey:
    """Generate a secure API key value along with its hashed representation."""

    raw_value = secrets.token_hex(32)
    digest = hash_api_key(raw_value)
    prefix = raw_value[:prefix_length]
    return GeneratedApiKey(value=raw_value, hashed=digest, prefix=prefix)


def hash_api_key(value: str) -> str:
    """Hash an API key using an application-wide peppered SHA-256 digest."""

    digest = hmac.new(_PEPPER, value.encode("utf-8"), sha256).hexdigest()
    return digest


def mask_api_key(prefix: str | None, *, placeholder: str = "••••") -> str:
    """Return a display-safe representation of an API key."""

    if not prefix:
        return placeholder
    return f"{prefix}{placeholder}"
