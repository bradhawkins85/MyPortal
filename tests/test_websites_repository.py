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
        "INSERT INTO website_check_jobs (website_id) VALUES (%s)", (42,)
    )


@pytest.mark.anyio
async def test_enqueue_check_reuses_pending_job(monkeypatch):
    monkeypatch.setattr(websites.db, "fetch_one", AsyncMock(return_value={"id": 9}))
    insert = AsyncMock()
    monkeypatch.setattr(websites.db, "execute_returning_lastrowid", insert)

    job_id = await websites.enqueue_check(42)

    assert job_id == 9
    insert.assert_not_awaited()
