from unittest.mock import AsyncMock

import pytest

from app.repositories import pending_staff_access, user_companies


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio("asyncio")
async def test_assign_user_to_company_persists_quote_access(monkeypatch):
    execute_mock = AsyncMock()
    monkeypatch.setattr(user_companies.db, "execute", execute_mock)
    monkeypatch.setattr(
        user_companies,
        "_ensure_company_membership",
        AsyncMock(),
    )

    await user_companies.assign_user_to_company(
        user_id=7,
        company_id=4,
        can_access_quotes=True,
    )

    sql, params = execute_mock.await_args.args
    assert "can_access_quotes" in sql
    assert "can_access_quotes = VALUES(can_access_quotes)" in sql
    assert params[13] == 1


@pytest.mark.anyio("asyncio")
async def test_pending_staff_access_persists_quote_access(monkeypatch):
    execute_mock = AsyncMock()
    monkeypatch.setattr(pending_staff_access.db, "execute", execute_mock)
    monkeypatch.setattr(
        pending_staff_access,
        "get_assignment",
        AsyncMock(return_value={"can_access_quotes": True}),
    )

    result = await pending_staff_access.upsert_assignment(
        staff_id=202,
        company_id=4,
        can_access_quotes=True,
    )

    sql, params = execute_mock.await_args.args
    assert "can_access_quotes" in sql
    assert "can_access_quotes = VALUES(can_access_quotes)" in sql
    assert params[13] == 1
    assert result == {"can_access_quotes": True}
