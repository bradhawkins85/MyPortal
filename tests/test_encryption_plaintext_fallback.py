from __future__ import annotations

import pytest

from app.security import encryption


@pytest.fixture(autouse=True)
def _reset_warned(monkeypatch):
    monkeypatch.setattr(encryption, "_plaintext_warned", set())


def test_legacy_plaintext_still_returned_but_warns_once_per_field(monkeypatch):
    warnings: list[tuple[str, dict]] = []
    monkeypatch.setattr(encryption, "log_warning", lambda msg, **meta: warnings.append((msg, meta)))

    assert encryption.decrypt_secret("legacy-value", field="m365.client_secret") == "legacy-value"
    assert encryption.decrypt_secret("legacy-value", field="m365.client_secret") == "legacy-value"
    assert encryption.decrypt_secret("other", field="imap.password") == "other"

    assert [meta["source"] for _, meta in warnings] == ["m365.client_secret", "imap.password"]
    # The secret itself must never be logged.
    assert all("legacy-value" not in repr(item) for item in warnings)


def test_plaintext_warning_keyed_by_call_site(monkeypatch):
    warnings: list[dict] = []
    monkeypatch.setattr(encryption, "log_warning", lambda msg, **meta: warnings.append(meta))

    for _ in range(3):
        encryption.decrypt_secret("legacy")

    assert len(warnings) == 1
    assert __name__ in warnings[0]["source"]


def test_empty_value_does_not_warn(monkeypatch):
    warnings: list[dict] = []
    monkeypatch.setattr(encryption, "log_warning", lambda msg, **meta: warnings.append(meta))
    assert encryption.decrypt_secret("") == ""
    assert warnings == []


def test_strict_mode_rejects_plaintext():
    with pytest.raises(ValueError):
        encryption.decrypt_secret('["forged"]', allow_plaintext=False)


def test_ciphertext_format_unchanged_and_roundtrips():
    token = encryption.encrypt_secret("hello")
    assert token.startswith("v1:") and len(token.split(":")) == 4
    assert encryption.is_encrypted_value(token)
    assert not encryption.is_encrypted_value("plain")
    assert encryption.decrypt_secret(token, allow_plaintext=False) == "hello"
