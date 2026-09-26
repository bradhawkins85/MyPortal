"""Lease-based, multi-instance-safe website monitoring worker."""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from typing import Any

from app.core.config import get_settings
from app.repositories import websites as repo
from app.services.website_monitoring import check_website


class WebsiteCheckWorker:
    def __init__(self) -> None:
        self.owner = uuid.uuid4().hex
        self._lock = asyncio.Lock()

    async def run_once(self) -> dict[str, int]:
        """Schedule due sites and process one bounded batch."""
        if self._lock.locked():
            return {"scheduled": 0, "claimed": 0}
        async with self._lock:
            settings = get_settings()
            now = datetime.now(timezone.utc).replace(tzinfo=None)
            scheduled = await repo.enqueue_due(now, settings.website_check_batch_size * 2)
            jobs = await repo.claim_jobs(
                owner=self.owner, now=now,
                lease_seconds=settings.website_check_lease_seconds,
                limit=settings.website_check_batch_size,
                company_limit=settings.website_check_company_concurrency,
            )
            await asyncio.gather(*(self._run(job) for job in jobs))
            return {"scheduled": scheduled, "claimed": len(jobs)}

    async def _run(self, job: dict[str, Any]) -> None:
        settings = get_settings()
        website = await repo.get_website_by_id(int(job["website_id"]))
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        if not website:
            await repo.finish_job(job, owner=self.owner, ok=True, now=now,
                                  interval_seconds=settings.website_check_interval_seconds)
            return
        try:
            result = await check_website(website)
        except Exception as exc:  # worker isolation; checker normally returns safe failures
            result = {"ok": False, "error": exc.__class__.__name__}
        await repo.finish_job(
            job, owner=self.owner, ok=bool(result.get("ok")), now=now,
            interval_seconds=settings.website_check_interval_seconds,
            error=str(result.get("error") or "Website check failed"),
        )


website_check_worker = WebsiteCheckWorker()
