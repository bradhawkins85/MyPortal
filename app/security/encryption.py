from __future__ import annotations

import base64
import hashlib
import os
from typing import Final

from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from app.core.config import get_settings


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


def decrypt_secret(payload: str) -> str:
    if ":" not in payload:
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
