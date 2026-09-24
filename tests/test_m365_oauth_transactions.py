from datetime import datetime, timedelta, timezone

import pytest

from app.services import m365_oauth_transactions as transactions


@pytest.mark.anyio("asyncio")
async def test_transaction_is_single_use(monkeypatch):
    monkeypatch.setattr(transactions, "get_redis_client", lambda: None)
    transaction_id = await transactions.create(
        user_id=7,
        session_id=11,
        company_id=23,
        flow="provision",
        code_verifier="server-secret",
        client_id="client-at-start",
        redirect_uri="https://portal.example/m365/callback",
    )

    first = await transactions.consume(transaction_id)
    second = await transactions.consume(transaction_id)

    assert first is not None
    assert first["code_verifier"] == "server-secret"
    assert first["client_id"] == "client-at-start"
    assert second is None


@pytest.mark.anyio("asyncio")
async def test_expired_transaction_is_rejected(monkeypatch):
    monkeypatch.setattr(transactions, "get_redis_client", lambda: None)
    transaction_id = await transactions.create(user_id=1, session_id=2, flow="discover")
    payload, _ = transactions._store[transaction_id]
    transactions._store[transaction_id] = (
        payload,
        datetime.now(timezone.utc) - timedelta(seconds=1),
    )

    assert await transactions.consume(transaction_id) is None


@pytest.mark.anyio("asyncio")
async def test_simultaneous_company_transactions_remain_isolated(monkeypatch):
    monkeypatch.setattr(transactions, "get_redis_client", lambda: None)
    first_id = await transactions.create(user_id=1, session_id=9, company_id=100, code_verifier="one")
    second_id = await transactions.create(user_id=1, session_id=9, company_id=200, code_verifier="two")

    second = await transactions.consume(second_id)
    first = await transactions.consume(first_id)

    assert (first["company_id"], first["code_verifier"]) == (100, "one")
    assert (second["company_id"], second["code_verifier"]) == (200, "two")
