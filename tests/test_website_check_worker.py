from __future__ import annotations

import asyncio
from datetime import datetime
from unittest.mock import AsyncMock

from app.repositories import websites
from app.services.website_check_worker import WebsiteCheckWorker
from app.services import website_monitoring


def test_worker_finishes_claimed_check_once(monkeypatch):
    worker = WebsiteCheckWorker()
    job = {"id": 4, "website_id": 8, "attempt_count": 1, "max_attempts": 3}
    monkeypatch.setattr(websites, "enqueue_due", AsyncMock(return_value=1))
    monkeypatch.setattr(websites, "claim_jobs", AsyncMock(return_value=[job]))
    monkeypatch.setattr(websites, "get_website_by_id", AsyncMock(return_value={"id": 8, "url": "https://example.com"}))
    monkeypatch.setattr("app.services.website_check_worker.check_website", AsyncMock(return_value={"ok": True}))
    finish = AsyncMock()
    monkeypatch.setattr(websites, "finish_job", finish)

    result = asyncio.run(worker.run_once())

    assert result == {"scheduled": 1, "claimed": 1}
    finish.assert_awaited_once()
    assert finish.await_args.kwargs["ok"] is True


def test_failed_job_uses_backoff_without_erasing_observation(monkeypatch):
    execute = AsyncMock(return_value=1)
    monkeypatch.setattr(websites.db, "execute", execute)
    job = {"id": 3, "website_id": 9, "attempt_count": 1, "max_attempts": 3}

    asyncio.run(websites.finish_job(job, owner="worker", ok=False,
                                    now=datetime(2026, 1, 1), interval_seconds=86400,
                                    error="blocked destination"))

    assert execute.await_count == 1
    query, params = execute.await_args.args
    assert "status = %s" in query
    assert params[0] == "pending"
    assert params[2] == "blocked destination"


def test_expired_lease_can_be_reclaimed(monkeypatch):
    candidate = {"id": 2, "website_id": 5, "company_id": 7}
    monkeypatch.setattr(websites.db, "fetch_all", AsyncMock(return_value=[candidate]))
    monkeypatch.setattr(websites.db, "execute", AsyncMock(return_value=1))
    monkeypatch.setattr(websites.db, "fetch_one", AsyncMock(return_value=candidate | {"lease_owner": "new"}))

    claimed = asyncio.run(websites.claim_jobs(owner="new", now=datetime(2026, 1, 1),
                                               lease_seconds=60, limit=1, company_limit=1))

    assert len(claimed) == 1
    update = websites.db.execute.await_args.args[0]
    assert "lease_expires_at < %s" in update
    assert "lease_owner = %s" in update


def test_company_limit_bounds_claims(monkeypatch):
    candidates = [
        {"id": 1, "website_id": 1, "company_id": 4},
        {"id": 2, "website_id": 2, "company_id": 4},
    ]
    monkeypatch.setattr(websites.db, "fetch_all", AsyncMock(return_value=candidates))
    monkeypatch.setattr(websites.db, "execute", AsyncMock(return_value=1))
    monkeypatch.setattr(websites.db, "fetch_one", AsyncMock(side_effect=[candidates[0]]))

    claimed = asyncio.run(websites.claim_jobs(owner="one", now=datetime(2026, 1, 1),
                                               lease_seconds=60, limit=10, company_limit=1))

    assert [row["id"] for row in claimed] == [1]


def test_dns_job_uses_dns_only_checker(monkeypatch):
    worker = WebsiteCheckWorker()
    job = {"id": 4, "website_id": 8, "check_type": "dns", "attempt_count": 1, "max_attempts": 3}
    monkeypatch.setattr(websites, "enqueue_due", AsyncMock(return_value=0))
    monkeypatch.setattr(websites, "claim_jobs", AsyncMock(return_value=[job]))
    monkeypatch.setattr(websites, "get_website_by_id", AsyncMock(return_value={"id": 8, "url": "https://example.com", "collect_dns": True}))
    dns_check = AsyncMock(return_value={"ok": True})
    website_check = AsyncMock(return_value={"ok": True})
    monkeypatch.setattr("app.services.website_check_worker.check_dns", dns_check)
    monkeypatch.setattr("app.services.website_check_worker.check_website", website_check)
    monkeypatch.setattr(websites, "finish_job", AsyncMock())

    asyncio.run(worker.run_once())

    dns_check.assert_awaited_once()
    website_check.assert_not_awaited()


def test_dns_check_does_not_make_http_or_tls_observations(monkeypatch):
    monkeypatch.setattr(website_monitoring, "lookup_dns", AsyncMock(return_value={
        "source": "recursive-dns", "coverage": "public lookup", "records": []
    }))
    monkeypatch.setattr(website_monitoring, "fetch_availability", AsyncMock())
    monkeypatch.setattr(website_monitoring, "inspect_certificate", AsyncMock())
    monkeypatch.setattr(websites, "record_dns_success", AsyncMock())

    result = asyncio.run(website_monitoring.check_dns({
        "id": 8, "url": "https://example.com", "collect_dns": True,
    }))

    assert result["ok"] is True
    website_monitoring.fetch_availability.assert_not_awaited()
    website_monitoring.inspect_certificate.assert_not_awaited()
    websites.record_dns_success.assert_awaited_once()
