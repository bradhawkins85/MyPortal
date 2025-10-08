import importlib.util
from pathlib import Path

import bcrypt
import pytest


MODULE_NAME = "app.security.passwords"
MODULE_PATH = Path(__file__).resolve().parents[1] / "app" / "security" / "passwords.py"

spec = importlib.util.spec_from_file_location(MODULE_NAME, MODULE_PATH)
passwords = importlib.util.module_from_spec(spec)
assert spec and spec.loader
spec.loader.exec_module(passwords)  # type: ignore[union-attr]

hash_password = passwords.hash_password
verify_password = passwords.verify_password


def test_hash_allows_passwords_longer_than_bcrypt_limit():
    password = "A" * 100
    hashed = hash_password(password)

    assert hashed.startswith("bcrypt_sha256$")
    assert verify_password(password, hashed)


@pytest.mark.parametrize("password", ["short", "another-password"])
def test_verify_password_success(password):
    hashed = hash_password(password)
    assert verify_password(password, hashed)


def test_verify_password_rejects_incorrect_password():
    hashed = hash_password("correct-horse-battery-staple")
    assert not verify_password("incorrect", hashed)


def test_verify_password_accepts_legacy_bcrypt_hashes():
    legacy_hash = bcrypt.hashpw(b"legacy-secret", bcrypt.gensalt()).decode()
    assert verify_password("legacy-secret", legacy_hash)
    assert not verify_password("legacy-secret-wrong", legacy_hash)
