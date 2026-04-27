"""Tests for the demo_seeding service."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.fixture
def anyio_backend():
    return "asyncio"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_db_mock(*, fetch_one_return=None, execute_return=None):
    """Return a minimal mock of the db object used in demo_seeding."""
    db_mock = MagicMock()
    db_mock.fetch_one = AsyncMock(return_value=fetch_one_return)
    db_mock.execute = AsyncMock(return_value=execute_return)
    return db_mock


# ---------------------------------------------------------------------------
# is_demo_seeded
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_is_demo_seeded_returns_true_when_company_exists(monkeypatch):
    from app.services import demo_seeding as svc

    db_mock = _make_db_mock(fetch_one_return={"id": 1, "name": "Demo Company"})
    monkeypatch.setattr(svc, "db", db_mock)

    result = await svc.is_demo_seeded()

    assert result is True


@pytest.mark.anyio
async def test_is_demo_seeded_returns_false_when_no_company(monkeypatch):
    from app.services import demo_seeding as svc

    db_mock = _make_db_mock(fetch_one_return=None)
    monkeypatch.setattr(svc, "db", db_mock)

    result = await svc.is_demo_seeded()

    assert result is False


# ---------------------------------------------------------------------------
# seed_demo_data – idempotency
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_seed_demo_data_skips_when_already_seeded(monkeypatch):
    from app.services import demo_seeding as svc

    monkeypatch.setattr(svc, "is_demo_seeded", AsyncMock(return_value=True))

    result = await svc.seed_demo_data()

    assert result["skipped"] is True
    assert "reason" in result


# ---------------------------------------------------------------------------
# remove_demo_data – skip when nothing to remove
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_remove_demo_data_skips_when_no_company(monkeypatch):
    from app.services import demo_seeding as svc

    db_mock = _make_db_mock(fetch_one_return=None)
    monkeypatch.setattr(svc, "db", db_mock)

    result = await svc.remove_demo_data()

    assert result["skipped"] is True


# ---------------------------------------------------------------------------
# remove_demo_data – happy path
# ---------------------------------------------------------------------------

@pytest.mark.anyio
async def test_remove_demo_data_deletes_company_when_demo_exists(monkeypatch):
    from app.services import demo_seeding as svc

    db_mock = _make_db_mock(fetch_one_return={"id": 42, "name": "Demo Company", "is_demo": 1})
    monkeypatch.setattr(svc, "db", db_mock)

    delete_mock = AsyncMock()
    company_repo_mock = MagicMock()
    company_repo_mock.delete_company = delete_mock
    monkeypatch.setattr(svc, "company_repo", company_repo_mock)

    result = await svc.remove_demo_data()

    assert result.get("removed") is True
    assert result.get("company_id") == 42
    delete_mock.assert_awaited_once_with(42)
