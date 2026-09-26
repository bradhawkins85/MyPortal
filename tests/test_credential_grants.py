from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from app.repositories import credential_grants
from app.schemas.vault import CredentialGrantCreate


def test_grant_contract_requires_bounded_reason_and_timezone_capable_expiry():
    payload = CredentialGrantCreate(
        recipient_user_id=12,
        staff_id=44,
        reason="Assist with first sign-in",
        expires_at=datetime.now(timezone.utc) + timedelta(hours=2),
    )
    assert payload.recipient_email is None
    assert payload.reason == "Assist with first sign-in"


def test_token_digest_is_deterministic_and_never_retains_token():
    token = "usable-token-that-must-not-be-stored"
    hashed = credential_grants.digest(token)
    assert hashed == credential_grants.digest(token)
    assert token.encode() not in hashed
    assert len(hashed) == 32


@pytest.mark.anyio
async def test_external_consumption_claims_before_decrypt(monkeypatch):
    monkeypatch.setattr(
        credential_grants.db, "execute_rowcount", AsyncMock(return_value=0)
    )
    fetch = AsyncMock()
    monkeypatch.setattr(credential_grants.db, "fetch_one", fetch)
    reveal = AsyncMock()
    monkeypatch.setattr(credential_grants.vault, "reveal", reveal)

    assert (
        await credential_grants.consume_external("already-used-token-value-123456")
        is None
    )
    fetch.assert_not_awaited()
    reveal.assert_not_awaited()
    sql = credential_grants.db.execute_rowcount.await_args.args[0]
    assert "consumed_at IS NULL" in sql
    assert "expires_at > CURRENT_TIMESTAMP" in sql
    assert "revoked_at IS NULL" in sql


@pytest.mark.anyio
async def test_wrong_verification_code_fails_without_state_change(monkeypatch):
    monkeypatch.setattr(
        credential_grants.db,
        "fetch_one",
        AsyncMock(
            return_value={
                "id": 9,
                "verification_hash": credential_grants.digest(
                    "valid-token-value-long-enough:123456"
                ),
            }
        ),
    )
    update = AsyncMock()
    monkeypatch.setattr(credential_grants.db, "execute_rowcount", update)
    assert (
        await credential_grants.verify_external(
            "valid-token-value-long-enough", "999999"
        )
        is None
    )
    update.assert_not_awaited()


@pytest.mark.anyio
async def test_named_grant_is_consumed_before_decrypt_and_cannot_race(monkeypatch):
    monkeypatch.setattr(
        credential_grants.db, "execute_rowcount", AsyncMock(return_value=0)
    )
    get = AsyncMock()
    monkeypatch.setattr(credential_grants, "get", get)
    reveal = AsyncMock()
    monkeypatch.setattr(credential_grants.vault, "reveal", reveal)

    assert await credential_grants.reveal_named(8, 12, 4) is None
    sql = credential_grants.db.execute_rowcount.await_args.args[0]
    assert "consumed_at = CURRENT_TIMESTAMP" in sql
    assert "consumed_at IS NULL" in sql
    get.assert_not_awaited()
    reveal.assert_not_awaited()
