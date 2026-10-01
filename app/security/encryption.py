from __future__ import annotations

import base64
import hashlib
import os
import sys
import threading
from typing import Final

from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from app.core.config import get_settings
from app.core.logging import log_warning


# Versioned ciphertext prefix. New ciphertexts are written as
# ``v1:<iv>:<tag>:<ct>`` using an HKDF-derived key. Legacy ciphertexts stored
# as ``<iv>:<tag>:<ct>`` (no version prefix) remain decryptable using the
# previous sha256-based derivation.
_VERSION_PREFIX: Final[str] = "v1"

_settings = get_settings()
_raw_secret: Final[bytes] = _settings.totp_encryption_key.encode("utf-8")

# Per-install HKDF salt. We derive the salt from SESSION_SECRET so two
# separate deployments end up with distinct encryption keys even if they
# happen to share the same TOTP_ENCRYPTION_KEY. The salt is not itself a
# secret – it just widens the KDF output domain.
_hkdf_salt: Final[bytes] = hashlib.sha256(
    ("myportal-encryption-v1|" + _settings.secret_key).encode("utf-8")
).digest()

# Primary key derived via HKDF-SHA256. HKDF strengthens short or
# low-entropy TOTP_ENCRYPTION_KEY values by mixing them with a domain
# separator and per-install salt.
_key: Final[bytes] = HKDF(
    algorithm=hashes.SHA256(),
    length=32,
    salt=_hkdf_salt,
    info=b"myportal-totp-encryption-v1",
    backend=default_backend(),
).derive(_raw_secret)

# Legacy key used to decrypt ciphertexts written before the HKDF migration.
_legacy_key: Final[bytes] = hashlib.sha256(_raw_secret).digest()


def encrypt_secret(secret: str) -> str:
    iv = os.urandom(12)
    encryptor = Cipher(
        algorithms.AES(_key),
        modes.GCM(iv),
        backend=default_backend(),
    ).encryptor()
    ciphertext = encryptor.update(secret.encode("utf-8")) + encryptor.finalize()
    tag = encryptor.tag
    return ":".join(
        (
            _VERSION_PREFIX,
            base64.b64encode(iv).decode("utf-8"),
            base64.b64encode(tag).decode("utf-8"),
            base64.b64encode(ciphertext).decode("utf-8"),
        )
    )


def _decrypt_with_key(iv: bytes, tag: bytes, data: bytes, key: bytes) -> str:
    decryptor = Cipher(
        algorithms.AES(key),
        modes.GCM(iv, tag),
        backend=default_backend(),
    ).decryptor()
    decrypted = decryptor.update(data) + decryptor.finalize()
    return decrypted.decode("utf-8")


# Deliberate non-change: the ciphertext format (AES-256-GCM, no associated
# data) is left as-is.  Adding AAD or changing the layout would make every
# stored secret undecryptable without a data migration.

_plaintext_warned: set[str] = set()
_plaintext_warned_lock = threading.Lock()


def is_encrypted_value(value: str | None) -> bool:
    """Return True when ``value`` looks like ciphertext produced by this module.

    Useful for opportunistic migrations: callers that read a legacy plaintext
    secret can re-save it through :func:`encrypt_secret`.
    """
    if not value or ":" not in value:
        return False
    parts = value.split(":")
    return (len(parts) == 4 and parts[0] == _VERSION_PREFIX) or len(parts) == 3


def _warn_plaintext_once(field: str | None) -> None:
    if field:
        key = field
    else:
        try:
            frame = sys._getframe(2)
            key = f"{frame.f_globals.get('__name__', '?')}:{frame.f_code.co_name}:{frame.f_lineno}"
        except ValueError:  # pragma: no cover - no caller frame
            key = "unknown"
    with _plaintext_warned_lock:
        if key in _plaintext_warned:
            return
        _plaintext_warned.add(key)
    # Never log the value itself.
    log_warning(
        "Legacy plaintext secret read where ciphertext was expected; re-save it to encrypt it",
        source=key,
    )


def decrypt_secret(payload: str, *, allow_plaintext: bool = True, field: str | None = None) -> str:
    """Decrypt ``payload`` produced by :func:`encrypt_secret`.

    Values without a ``:`` separator are treated as legacy plaintext and
    returned unchanged for backward compatibility (for example secrets that
    were configured manually before encryption-at-rest existed).  A warning
    is logged once per ``field`` (or per call site when ``field`` is omitted)
    so operators can find and re-save them.  Pass ``allow_plaintext=False``
    where the value must always be ciphertext (tokens, cookies) so a
    client-supplied plaintext value is rejected instead of trusted.
    """
    if ":" not in payload:
        if not allow_plaintext:
            raise ValueError("Value is not encrypted")
        if payload:
            _warn_plaintext_once(field)
        return payload
    parts = payload.split(":")
    if len(parts) == 4 and parts[0] == _VERSION_PREFIX:
        iv = base64.b64decode(parts[1])
        tag = base64.b64decode(parts[2])
        data = base64.b64decode(parts[3])
        return _decrypt_with_key(iv, tag, data, _key)

    if len(parts) == 3:
        # Legacy format: try the legacy (sha256) key first, then fall back to
        # the HKDF-derived key. Falling back both ways keeps rotated keys
        # decryptable during migration.
        iv = base64.b64decode(parts[0])
        tag = base64.b64decode(parts[1])
        data = base64.b64decode(parts[2])
        try:
            return _decrypt_with_key(iv, tag, data, _legacy_key)
        except Exception:
            return _decrypt_with_key(iv, tag, data, _key)

    raise ValueError("Unsupported ciphertext format")
