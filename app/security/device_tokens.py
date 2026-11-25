"""Authentication tokens shared by the tray and RMM agents."""

import hashlib
import hmac
import secrets

from app.core.config import get_settings

_settings = get_settings()


def generate_install_token() -> str:
    """Return a fresh install token (URL-safe, ~43 chars)."""

    return secrets.token_urlsafe(32)


def generate_auth_token() -> str:
    """Return a fresh per-device auth token (URL-safe, ~64 chars)."""

    return secrets.token_urlsafe(48)


def hash_token(token: str) -> str:
    """Return a deterministic hash for ``token``.

    The hash is salted with the application secret so a leaked database
    cannot be used to brute-force the token offline. SHA-256 is sufficient
    here because tokens are 256+ bits of entropy generated above; we are
    not protecting against weak passwords, only obscuring the value at
    rest. Using HMAC binds the hash to this MyPortal install.
    """

    key = (_settings.secret_key or "").encode("utf-8") or b"myportal-tray"
    digest = hmac.new(key, token.encode("utf-8"), hashlib.sha256).hexdigest()
    return digest


def token_prefix(token: str) -> str:
    return token[:8]
