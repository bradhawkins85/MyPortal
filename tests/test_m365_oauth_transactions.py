import pytest
from unittest.mock import AsyncMock

from app.services import m365_oauth_transactions as transactions


@pytest.mark.anyio("asyncio")
async def test_transaction_is_single_use(monkeypatch):
    monkeypatch.setattr(transactions, "get_redis_client", lambda: None)
    stored = {}

    async def store(transaction_id, payload, expires_at):
        stored[transaction_id] = payload

    async def consume(transaction_id):
        return stored.pop(transaction_id, None)

    monkeypatch.setattr(transactions.transaction_repo, "create", store)
    monkeypatch.setattr(transactions.transaction_repo, "consume", consume)
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
    monkeypatch.setattr(transactions.transaction_repo, "create", AsyncMock())
    monkeypatch.setattr(transactions.transaction_repo, "consume", AsyncMock(return_value=None))
    transaction_id = await transactions.create(user_id=1, session_id=2, flow="discover")

    assert await transactions.consume(transaction_id) is None


@pytest.mark.anyio("asyncio")
async def test_database_fallback_encrypts_callback_context(monkeypatch):
    monkeypatch.setattr(transactions, "get_redis_client", lambda: None)
    create = AsyncMock()
    monkeypatch.setattr(transactions.transaction_repo, "create", create)

    await transactions.create(
        user_id=1, session_id=2, flow="provision", code_verifier="private-verifier"
    )

    encrypted_payload = create.await_args.args[1]
    assert "private-verifier" not in encrypted_payload
    assert encrypted_payload.startswith("v1:")


@pytest.mark.anyio("asyncio")
async def test_simultaneous_company_transactions_remain_isolated(monkeypatch):
    monkeypatch.setattr(transactions, "get_redis_client", lambda: None)
    stored = {}

    async def store(transaction_id, payload, expires_at):
        stored[transaction_id] = payload

    async def consume(transaction_id):
        return stored.pop(transaction_id, None)

    monkeypatch.setattr(transactions.transaction_repo, "create", store)
    monkeypatch.setattr(transactions.transaction_repo, "consume", consume)
    first_id = await transactions.create(user_id=1, session_id=9, company_id=100, code_verifier="one")
    second_id = await transactions.create(user_id=1, session_id=9, company_id=200, code_verifier="two")

    second = await transactions.consume(second_id)
    first = await transactions.consume(first_id)

    assert (first["company_id"], first["code_verifier"]) == (100, "one")
    assert (second["company_id"], second["code_verifier"]) == (200, "two")
