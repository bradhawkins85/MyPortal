from __future__ import annotations

import base64
import hashlib
import os
from typing import Final

from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from app.core.config import get_settings


_settings = get_settings()
_key: Final[bytes] = hashlib.sha256(_settings.totp_encryption_key.encode("utf-8")).digest()


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
            base64.b64encode(iv).decode("utf-8"),
            base64.b64encode(tag).decode("utf-8"),
            base64.b64encode(ciphertext).decode("utf-8"),
        )
    )


def decrypt_secret(payload: str) -> str:
    if ":" not in payload:
        return payload
    iv_b64, tag_b64, data_b64 = payload.split(":")
    iv = base64.b64decode(iv_b64)
    tag = base64.b64decode(tag_b64)
    data = base64.b64decode(data_b64)
    decryptor = Cipher(
        algorithms.AES(_key),
        modes.GCM(iv, tag),
        backend=default_backend(),
    ).decryptor()
    decrypted = decryptor.update(data) + decryptor.finalize()
    return decrypted.decode("utf-8")
