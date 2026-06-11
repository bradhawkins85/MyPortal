import pytest

from app.repositories import issues as issues_repo
from app.repositories import notifications as notifications_repo


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_issue_list_filters_to_allowed_company_ids(monkeypatch):
    calls = []

    async def fake_fetch_all(sql, params=()):
        calls.append((sql, params))
        return []

    monkeypatch.setattr(issues_repo.db, "fetch_all", fake_fetch_all)

    await issues_repo.list_issues_with_assignments(company_ids=[7, 11, 7])

    assert calls
    sql, params = calls[0]
    assert "ics.company_id IN (%s, %s)" in sql
    assert params == (7, 11)


@pytest.mark.anyio
async def test_issue_list_empty_allowed_companies_returns_no_rows(monkeypatch):
    calls = []

    async def fake_fetch_all(sql, params=()):
        calls.append((sql, params))
        return []

    monkeypatch.setattr(issues_repo.db, "fetch_all", fake_fetch_all)

    await issues_repo.list_issues_with_assignments(company_ids=[])

    assert calls
    sql, params = calls[0]
    assert "1 = 0" in sql
    assert params == ()


@pytest.mark.anyio
async def test_notifications_filter_to_exact_user_only(monkeypatch):
    calls = []

    async def fake_fetch_all(sql, params=()):
        calls.append((sql, params))
        return []

    monkeypatch.setattr(notifications_repo.db, "fetch_all", fake_fetch_all)

    await notifications_repo.list_notifications(user_id=42, limit=5, offset=0)

    assert calls
    sql, params = calls[0]
    assert "user_id = %s" in sql
    assert "user_id IS NULL" not in sql
    assert params[-3:] == (42, 5, 0)


@pytest.mark.anyio
async def test_notification_event_types_filter_to_exact_user_only(monkeypatch):
    calls = []

    async def fake_fetch_all(sql, params=()):
        calls.append((sql, params))
        return []

    monkeypatch.setattr(notifications_repo.db, "fetch_all", fake_fetch_all)

    await notifications_repo.list_event_types(user_id=42)

    assert calls
    sql, params = calls[0]
    assert "user_id = %s" in sql
    assert "user_id IS NULL" not in sql
    assert params == (42,)
