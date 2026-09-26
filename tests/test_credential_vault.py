from __future__ import annotations

import base64

import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from pydantic import SecretStr

from app.core.config import get_settings
from app.schemas.vault import CredentialCreate, CredentialMetadata, CredentialUpdate
from app.security import vault


@pytest.fixture
def vault_settings(monkeypatch):
    settings = get_settings()
    old_keys, old_active = settings.vault_keys, settings.vault_active_key_id
    first = base64.b64encode(b"a" * 32).decode()
    second = base64.b64encode(b"b" * 32).decode()
    monkeypatch.setattr(settings, "vault_keys", f"old:{first},new:{second}")
    monkeypatch.setattr(settings, "vault_active_key_id", "old")
    yield settings
    settings.vault_keys, settings.vault_active_key_id = old_keys, old_active


def test_ciphertext_never_contains_plaintext_and_tampering_fails(vault_settings):
    encrypted = vault.encrypt(
        "correct horse battery staple", company_id=7, credential_id=9, version=1
    )
    assert b"correct horse" not in encrypted.ciphertext
    assert (
        vault.decrypt(encrypted, company_id=7, credential_id=9, version=1)
        == "correct horse battery staple"
    )

    damaged = vault.EncryptedSecret(
        encrypted.key_id,
        encrypted.nonce,
        encrypted.ciphertext[:-1] + bytes([encrypted.ciphertext[-1] ^ 1]),
    )
    with pytest.raises(vault.VaultIntegrityError, match="integrity"):
        vault.decrypt(damaged, company_id=7, credential_id=9, version=1)


def test_ciphertext_is_bound_to_company(vault_settings):
    encrypted = vault.encrypt("tenant secret", company_id=7, credential_id=9, version=1)
    with pytest.raises(vault.VaultIntegrityError):
        vault.decrypt(encrypted, company_id=8, credential_id=9, version=1)


def test_rotation_keeps_old_and_new_versions_readable(vault_settings):
    old = vault.encrypt("old value", company_id=1, credential_id=2, version=1)
    vault_settings.vault_active_key_id = "new"
    new = vault.encrypt("new value", company_id=1, credential_id=2, version=2)
    assert old.key_id == "old" and new.key_id == "new"
    assert vault.decrypt(old, company_id=1, credential_id=2, version=1) == "old value"
    assert vault.decrypt(new, company_id=1, credential_id=2, version=2) == "new value"


def test_secret_request_repr_and_metadata_serialization_do_not_leak():
    request = CredentialCreate(
        company_id=1, name="Router", secret=SecretStr("never serialize me")
    )
    assert "never serialize me" not in repr(request)
    metadata = CredentialMetadata(id=2, company_id=1, name="Router", current_version=3)
    serialized = metadata.model_dump()
    assert "secret" not in serialized
    assert "ciphertext" not in serialized


def test_unknown_key_fails_closed(vault_settings):
    forged = vault.EncryptedSecret(
        "missing", b"0" * 12, AESGCM(b"x" * 32).encrypt(b"0" * 12, b"value", b"aad")
    )
    with pytest.raises(vault.VaultConfigurationError, match="unavailable"):
        vault.decrypt(forged, company_id=1, credential_id=2, version=1)


def test_metadata_edit_contract_cannot_accept_a_secret():
    payload = CredentialUpdate(
        name="Onboarding password",
        credential_class="onboarding",
        owner="Service desk",
        intended_recipient="New starter",
        secret="must not be accepted",
    )
    assert "secret" not in payload.model_dump()


def test_process_run_is_a_safe_reference_type():
    payload = CredentialCreate(
        company_id=4,
        name="Deployment account",
        secret=SecretStr("not in the ticket"),
        links=[("process_run", 12), ("ticket", 20)],
    )
    assert payload.links == [("process_run", 12), ("ticket", 20)]
