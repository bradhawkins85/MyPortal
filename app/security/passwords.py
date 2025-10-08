from __future__ import annotations

from hashlib import sha256

import bcrypt


_BCRYPT_SHA256_PREFIX = "bcrypt_sha256$"
# Prefix indicating passwords hashed via SHA-256 pre-hashing and bcrypt.


def hash_password(password: str) -> str:
    digest = sha256(password.encode("utf-8")).digest()
    hashed = bcrypt.hashpw(digest, bcrypt.gensalt())
    return f"{_BCRYPT_SHA256_PREFIX}{hashed.decode()}"


def verify_password(password: str, hashed: str) -> bool:
    password_bytes = password.encode("utf-8")

    if hashed.startswith(_BCRYPT_SHA256_PREFIX):
        digest = sha256(password_bytes).digest()
        stored = hashed[len(_BCRYPT_SHA256_PREFIX) :].encode()
        try:
            return bcrypt.checkpw(digest, stored)
        except ValueError:
            return False

    try:
        return bcrypt.checkpw(password_bytes, hashed.encode())
    except ValueError:
        # bcrypt raises ValueError when the candidate password exceeds its 72-byte
        # limit. Treat this as a failed verification to avoid leaking errors during
        # authentication attempts.
        return False
