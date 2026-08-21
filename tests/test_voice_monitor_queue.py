from __future__ import annotations

import asyncio
from datetime import datetime
from unittest.mock import AsyncMock, patch

from app.repositories import voice_monitor as repo
from app.services.voice_monitor.providers import CallState, OriginatedCall
from app.workers.voice_monitor import VoiceMonitorWorker


def test_unique_dispatch_uses_stable_key_and_ignore_insert():
    due = {"id": 1, "company_id": 2, "next_run_at": datetime(2026, 1, 1),
           "interval_seconds": 60, "max_retries": 2}
    with patch.object(repo.db, "fetch_all", new_callable=AsyncMock, return_value=[due]), \
         patch.object(repo.db, "execute_rowcount", new_callable=AsyncMock, side_effect=[1, 1]) as execute:
        assert asyncio.run(repo.enqueue_due_attempts(now=datetime(2026, 1, 2))) == 1
    sql, values = execute.call_args_list[0].args
    assert "IGNORE" in sql and "dispatch_key" in sql
    assert values[5] == values[6] == repo.dispatch_key(1, due["next_run_at"])
    assert values[7] == 3


def test_concurrent_claim_is_compare_and_swap_and_tenant_bounded():
    attempts = [{"id": i, "company_id": 7, "outcome_status": "queued", "lease_owner": None,
                 "lease_until": None, "delivery_count": 0} for i in range(1, 4)]
    with patch.object(repo.db, "fetch_all", new_callable=AsyncMock, return_value=attempts), \
         patch.object(repo.db, "execute_rowcount", new_callable=AsyncMock, return_value=1) as execute:
        claimed = asyncio.run(repo.claim_attempts(worker_identity="w", limit=3, lease_seconds=30,
                                                  per_tenant=2, now=datetime(2026, 1, 1)))
    assert len(claimed) == 2
    assert all("WHERE id = %s AND outcome_status = %s" in call.args[0] for call in execute.call_args_list)


def test_lease_expiry_is_eligible_for_recovery():
    with patch.object(repo.db, "fetch_all", new_callable=AsyncMock, return_value=[]) as fetch:
        asyncio.run(repo.claim_attempts(worker_identity="w", limit=1, lease_seconds=30,
                                        per_tenant=1, now=datetime(2026, 1, 1)))
    assert "lease_until < %s" in fetch.call_args.args[0]


def test_retry_exhaustion_is_terminal():
    attempt = {"id": 4, "delivery_count": 2, "max_deliveries": 2, "retry_delay_seconds": 10}
    with patch.object(repo.db, "execute_rowcount", new_callable=AsyncMock, return_value=1) as execute:
        asyncio.run(repo.finish_delivery(attempt, "w", status="interrupted", now=datetime(2026, 1, 1)))
    assert execute.call_args.args[1][0] == "exhausted"
    assert execute.call_args.args[1][3] == datetime(2026, 1, 1)


class _Provider:
    def __init__(self):
        self.keys = []
        self.hung_up = []

    async def originate(self, destination, *, idempotency_key):
        self.keys.append(idempotency_key)
        return OriginatedCall("call-1")
    async def status(self, call_id): return CallState.RINGING
    async def hangup(self, call_id): self.hung_up.append(call_id)
    async def retrieve_media(self, call_id): return None
    def map_callback(self, payload): return CallState.COMPLETED


def test_provider_idempotency_and_graceful_shutdown_reconciliation():
    async def scenario():
        provider = _Provider()
        worker = VoiceMonitorWorker(provider, identity="w")
        attempt = {"id": 5, "company_id": 9, "destination_e164": "+611", "timeout_seconds": 99,
                   "provider_idempotency_key": "stable", "delivery_count": 1, "max_deliveries": 2,
                   "retry_delay_seconds": 1, "provider_call_id": None}
        with patch.object(repo.db, "execute_rowcount", new_callable=AsyncMock, return_value=1), \
             patch.object(repo, "finish_delivery", new_callable=AsyncMock, return_value=True) as finish:
            task = asyncio.create_task(worker._deliver(attempt))
            worker._tasks.add(task)
            await asyncio.sleep(0.01)
            await worker.stop(grace_seconds=0)
        assert provider.keys == ["stable"] and provider.hung_up == ["call-1"]
        assert finish.call_args.kwargs["status"] == "interrupted"
    asyncio.run(scenario())


def test_scheduler_service_has_no_voice_monitor_dependency():
    source = open("app/services/scheduler.py", encoding="utf-8").read()
    assert "voice_monitor" not in source
