"""Tests for the HKDF-based key derivation and legacy ciphertext fallback
in :mod:`app.security.encryption`.
"""

from __future__ import annotations

import base64
import hashlib
import os

from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


def test_roundtrip_uses_versioned_prefix():
    from app.security.encryption import decrypt_secret, encrypt_secret

    ciphertext = encrypt_secret("my-secret-value")
    # New ciphertexts must use the ``v1:`` prefix so readers know to use
    # the HKDF-derived key.
    assert ciphertext.startswith("v1:")
    assert decrypt_secret(ciphertext) == "my-secret-value"


def test_legacy_three_part_ciphertext_still_decrypts():
    """Data encrypted with the previous sha256-only key must remain readable
    after the upgrade to HKDF so that deployments with existing TOTP/M365
    tokens do not silently lose those credentials."""

    from app.core.config import get_settings

    settings = get_settings()
    legacy_key = hashlib.sha256(settings.totp_encryption_key.encode("utf-8")).digest()

    plaintext = "legacy-secret"
    iv = os.urandom(12)
    encryptor = Cipher(
        algorithms.AES(legacy_key),
        modes.GCM(iv),
        backend=default_backend(),
    ).encryptor()
    ct = encryptor.update(plaintext.encode("utf-8")) + encryptor.finalize()
    tag = encryptor.tag
    legacy_payload = ":".join(
        (
            base64.b64encode(iv).decode("utf-8"),
            base64.b64encode(tag).decode("utf-8"),
            base64.b64encode(ct).decode("utf-8"),
        )
    )

    from app.security.encryption import decrypt_secret

    assert decrypt_secret(legacy_payload) == plaintext


def test_decrypt_passthrough_for_non_ciphertext():
    from app.security.encryption import decrypt_secret

    # Values with no ``:`` separator are passed through unchanged (e.g. when
    # the caller has opted out of encryption).
    assert decrypt_secret("not-encrypted") == "not-encrypted"
