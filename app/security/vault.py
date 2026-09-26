"""Authenticated encryption for the credential vault.

This module intentionally has no dependency on ``encryption.py``: vault keys
live in a separate deployment key domain and never fall back to the TOTP key.
"""

from __future__ import annotations

import base64
import binascii
import os
from dataclasses import dataclass

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.core.config import get_settings


class VaultConfigurationError(RuntimeError):
    pass


class VaultIntegrityError(ValueError):
    """Raised without sensitive detail when ciphertext authentication fails."""


@dataclass(frozen=True)
class EncryptedSecret:
    key_id: str
    nonce: bytes
    ciphertext: bytes


def _decode_keyring(raw: str) -> dict[str, bytes]:
    keys: dict[str, bytes] = {}
    for entry in filter(None, (item.strip() for item in raw.split(","))):
        key_id, separator, encoded = entry.partition(":")
        if not separator or not key_id or not encoded:
            raise VaultConfigurationError(
                "VAULT_KEYS must contain key-id:base64 entries"
            )
        try:
            key = base64.b64decode(encoded, validate=True)
        except (ValueError, binascii.Error) as exc:
            raise VaultConfigurationError("VAULT_KEYS contains invalid base64") from exc
        if len(key) != 32:
            raise VaultConfigurationError("Vault keys must be 32 bytes")
        keys[key_id] = key
    return keys


def associated_data(*, company_id: int, credential_id: int, version: int) -> bytes:
    return f"myportal-vault-v1|{company_id}|{credential_id}|{version}".encode("ascii")


def encrypt(
    plaintext: str, *, company_id: int, credential_id: int, version: int
) -> EncryptedSecret:
    settings = get_settings()
    keys = _decode_keyring(settings.vault_keys)
    key_id = settings.vault_active_key_id
    if not key_id or key_id not in keys:
        raise VaultConfigurationError("The active vault key is not configured")
    nonce = os.urandom(12)
    ciphertext = AESGCM(keys[key_id]).encrypt(
        nonce,
        plaintext.encode("utf-8"),
        associated_data(
            company_id=company_id, credential_id=credential_id, version=version
        ),
    )
    return EncryptedSecret(key_id, nonce, ciphertext)


def decrypt(
    secret: EncryptedSecret, *, company_id: int, credential_id: int, version: int
) -> str:
    key = _decode_keyring(get_settings().vault_keys).get(secret.key_id)
    if key is None:
        raise VaultConfigurationError("The required vault key is unavailable")
    try:
        value = AESGCM(key).decrypt(
            secret.nonce,
            secret.ciphertext,
            associated_data(
                company_id=company_id, credential_id=credential_id, version=version
            ),
        )
        return value.decode("utf-8")
    except (InvalidTag, UnicodeDecodeError) as exc:
        raise VaultIntegrityError("Secret integrity verification failed") from exc
