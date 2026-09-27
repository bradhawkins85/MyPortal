import asyncio
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
        await credential_grants.consume_external(
            "already-used-token-value-123456", "123456"
        )
        is None
    )
    fetch.assert_not_awaited()
    reveal.assert_not_awaited()
    sql = credential_grants.db.execute_rowcount.await_args.args[0]
    assert "consumed_at IS NULL" in sql
    assert "expires_at > CURRENT_TIMESTAMP" in sql
    assert "revoked_at IS NULL" in sql
    assert "verification_hash = %s" in sql


@pytest.mark.anyio
async def test_external_reveal_requires_code_again_at_atomic_consumption(monkeypatch):
    update = AsyncMock(return_value=0)
    monkeypatch.setattr(credential_grants.db, "execute_rowcount", update)
    reveal = AsyncMock()
    monkeypatch.setattr(credential_grants.vault, "reveal", reveal)

    token = "shared-link-token-value-long-enough"
    assert await credential_grants.consume_external(token, "wrong-code") is None

    sql, params = update.await_args.args
    assert "verification_hash = %s" in sql
    assert params == (
        credential_grants.digest(token),
        credential_grants.digest(token + ":wrong-code"),
    )
    reveal.assert_not_awaited()


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


@pytest.mark.anyio
async def test_two_link_holders_cannot_share_one_verification_or_race_reveal(monkeypatch):
    token = "shared-link-token-value-long-enough"
    correct_hash = credential_grants.digest(token + ":123456")
    consumed = False
    lock = asyncio.Lock()

    async def atomic_consume(_sql, params):
        nonlocal consumed
        async with lock:
            if consumed or params[1] != correct_hash:
                return 0
            consumed = True
            return 1

    monkeypatch.setattr(credential_grants.db, "execute_rowcount", atomic_consume)
    monkeypatch.setattr(
        credential_grants.db,
        "fetch_one",
        AsyncMock(
            return_value={
                "id": 7,
                "company_id": 3,
                "credential_id": 5,
                "credential_version": 2,
            }
        ),
    )
    reveal = AsyncMock(return_value=({}, "secret"))
    monkeypatch.setattr(credential_grants.vault, "reveal", reveal)

    attacker, recipient_a, recipient_b = await asyncio.gather(
        credential_grants.consume_external(token, "654321"),
        credential_grants.consume_external(token, "123456"),
        credential_grants.consume_external(token, "123456"),
    )

    assert attacker is None
    assert sum(result is not None for result in (recipient_a, recipient_b)) == 1
    reveal.assert_awaited_once()
