from __future__ import annotations

from base64 import urlsafe_b64decode, urlsafe_b64encode
from hashlib import pbkdf2_hmac, sha256
from hmac import compare_digest
from os import urandom

import bcrypt


# Legacy prefix for historical SHA-256 pre-hash + bcrypt password records.
_BCRYPT_SHA256_PREFIX = "bcrypt_sha256$"
_PBKDF2_SHA256_PREFIX = "pbkdf2_sha256$"
# Current OWASP guidance for PBKDF2-HMAC-SHA256 favors a high iteration count;
# revisit periodically as hardware and recommendations evolve.
_PBKDF2_ITERATIONS = 600_000
# 32 bytes matches SHA-256 output size and provides full hash strength.
_PBKDF2_DKLEN = 32


def _b64encode(value: bytes) -> str:
    return urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _b64decode(value: str) -> bytes:
    return urlsafe_b64decode(value + "=" * (-len(value) % 4))


def hash_password(password: str) -> str:
    # 16 bytes (128 bits) provides strong per-password uniqueness for PBKDF2 salts.
    salt = urandom(16)
    digest = pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt,
        _PBKDF2_ITERATIONS,
        dklen=_PBKDF2_DKLEN,
    )
    return (
        f"{_PBKDF2_SHA256_PREFIX}{_PBKDF2_ITERATIONS}$"
        f"{_b64encode(salt)}${_b64encode(digest)}"
    )


def verify_password(password: str, hashed: str) -> bool:
    password_bytes = password.encode("utf-8")

    if hashed.startswith(_PBKDF2_SHA256_PREFIX):
        try:
            _, iterations_raw, salt_raw, digest_raw = hashed.split("$", 3)
            iterations = int(iterations_raw)
            salt = _b64decode(salt_raw)
            expected = _b64decode(digest_raw)
        except (TypeError, ValueError):
            return False

        candidate = pbkdf2_hmac(
            "sha256",
            password_bytes,
            salt,
            iterations,
            dklen=len(expected),
        )
        return compare_digest(candidate, expected)

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
