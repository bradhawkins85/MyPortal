from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock

import pytest

from app.services import m365_jobs


@pytest.mark.anyio
async def test_enqueue_returns_existing_active_job(monkeypatch):
    existing = {"id": "job-1", "company_id": 7, "status": "running", "payload": "{}", "result": None}
    fetch = AsyncMock(return_value=existing)
    execute = AsyncMock()
    monkeypatch.setattr(m365_jobs.db, "fetch_one", fetch)
    monkeypatch.setattr(m365_jobs.db, "execute", execute)

    result = await m365_jobs.enqueue(7, "mailbox_sync", "mailboxes")

    assert result["id"] == "job-1"
    execute.assert_not_awaited()


@pytest.mark.anyio
async def test_claim_recovers_expired_running_job(monkeypatch):
    expired = {
        "id": "job-2", "company_id": 7, "job_type": "mailbox_sync",
        "status": "running", "owner_id": "dead-worker",
        "lease_expires_at": datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=2),
        "payload": "{}", "result": None,
    }
    claimed = {**expired, "owner_id": m365_jobs.instance_id(), "status": "running"}
    monkeypatch.setattr(m365_jobs.db, "fetch_all", AsyncMock(return_value=[expired]))
    monkeypatch.setattr(m365_jobs.db, "fetch_one", AsyncMock(return_value=claimed))
    execute = AsyncMock()
    monkeypatch.setattr(m365_jobs.db, "execute", execute)

    result = await m365_jobs._claim()

    assert result and result["id"] == "job-2"
    assert execute.await_count == 1


@pytest.mark.anyio
async def test_run_once_records_partial_completion(monkeypatch):
    job = {"id": "job-3", "company_id": 7, "job_type": "test-partial", "payload": {}}
    monkeypatch.setattr(m365_jobs, "_claim", AsyncMock(return_value=job))
    execute = AsyncMock()
    monkeypatch.setattr(m365_jobs.db, "execute", execute)

    async def handler(_job):
        return {"status": "partial", "items": [{"id": "safe", "error": "Unavailable"}]}

    m365_jobs.register("test-partial", handler)
    assert await m365_jobs.run_once() is True
    assert execute.await_args.args[1][0] == "partial"
