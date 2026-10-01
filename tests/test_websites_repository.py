from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.repositories import websites


@pytest.mark.anyio
async def test_create_website_returns_inserted_identifier(monkeypatch):
    insert = AsyncMock(return_value=42)
    execute = AsyncMock()
    monkeypatch.setattr(websites.db, "execute_returning_lastrowid", insert)
    monkeypatch.setattr(websites.db, "execute", execute)

    website_id = await websites.create_website(
        7,
        {
            "name": "Customer portal",
            "url": "https://example.com",
            "owner": None,
            "notes": None,
            "monitor_availability": True,
            "monitor_tls": True,
            "collect_dns": False,
            "collect_domain_expiry": False,
        },
        3,
    )

    assert website_id == 42
    insert.assert_awaited_once()
    execute.assert_not_awaited()


@pytest.mark.anyio
async def test_enqueue_check_returns_inserted_identifier(monkeypatch):
    monkeypatch.setattr(websites.db, "fetch_one", AsyncMock(return_value=None))
    insert = AsyncMock(return_value=84)
    monkeypatch.setattr(websites.db, "execute_returning_lastrowid", insert)

    job_id = await websites.enqueue_check(42)

    assert job_id == 84
    insert.assert_awaited_once_with(
        "INSERT INTO website_check_jobs (website_id, check_type, idempotency_key) VALUES (%s, %s, %s)",
        (42, "website", None),
    )


@pytest.mark.anyio
async def test_enqueue_check_reuses_pending_job(monkeypatch):
    monkeypatch.setattr(websites.db, "fetch_one", AsyncMock(return_value={"id": 9}))
    insert = AsyncMock()
    monkeypatch.setattr(websites.db, "execute_returning_lastrowid", insert)

    job_id = await websites.enqueue_check(42)

    assert job_id == 9
    insert.assert_not_awaited()


@pytest.mark.anyio
async def test_all_company_schedule_excludes_company_override_even_when_disabled(monkeypatch):
    fetch_all = AsyncMock(return_value=[])
    monkeypatch.setattr(websites.db, "fetch_all", fetch_all)

    result = await websites.enqueue_scheduled_scope(
        command="refresh_dns_records", company_id=None, task_id=5,
        due_window="202609271200",
    )

    assert result == {"queued": 0, "checked": 0, "changed": 0, "skipped": 0, "failed": 0}
    query, params = fetch_all.await_args.args
    assert "NOT EXISTS" in query
    assert "t.active" not in query
    assert "w.collect_dns = 1" in query
    assert params == ("refresh_dns_records",)


@pytest.mark.anyio
async def test_company_schedule_is_tenant_scoped_and_dns_typed(monkeypatch):
    monkeypatch.setattr(websites.db, "fetch_all", AsyncMock(return_value=[{"id": 12}]))
    monkeypatch.setattr(websites.db, "fetch_one", AsyncMock(return_value=None))
    insert = AsyncMock(return_value=77)
    monkeypatch.setattr(websites.db, "execute_returning_lastrowid", insert)

    result = await websites.enqueue_scheduled_scope(
        command="refresh_dns_records", company_id=9, task_id=5,
        due_window="202609271200",
    )

    query, params = websites.db.fetch_all.await_args.args
    assert "w.company_id = %s" in query
    assert params == (9,)
    assert result["queued"] == 1
    assert insert.await_args.args[1][1] == "dns"
    assert insert.await_args.args[1][2] == "scheduled:dns:5:202609271200:12"
