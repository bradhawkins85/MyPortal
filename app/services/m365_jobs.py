"""Durable execution queue for restart-safe Microsoft 365 work."""
from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable

from app.core.database import db
from app.core.logging import log_error
from app.services.singleton_jobs import instance_id

TERMINAL = frozenset({"partial", "succeeded", "failed"})
LEASE_SECONDS = 90
POLL_SECONDS = 2
_stop: asyncio.Event | None = None
_worker: asyncio.Task[None] | None = None
Handler = Callable[[dict[str, Any]], Awaitable[dict[str, Any] | None]]
_handlers: dict[str, Handler] = {}


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def register(job_type: str, handler: Handler) -> None:
    _handlers[job_type] = handler


async def enqueue(company_id: int, job_type: str, resource_key: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    """Create one active operation for a tenant/resource, returning an existing one on races."""
    active_key = f"{job_type}:{resource_key}"
    existing = await db.fetch_one(
        "SELECT * FROM m365_jobs WHERE company_id = %s AND active_key = %s",
        (company_id, active_key),
    )
    if existing:
        return _decode(existing)
    job_id, now = str(uuid.uuid4()), _now()
    try:
        await db.execute(
            "INSERT INTO m365_jobs (id,company_id,job_type,resource_key,active_key,status,payload,available_at,created_at,updated_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (job_id, company_id, job_type, resource_key, active_key, "queued", json.dumps(payload or {}), now, now, now),
        )
    except Exception:
        existing = await db.fetch_one(
            "SELECT * FROM m365_jobs WHERE company_id = %s AND active_key = %s",
            (company_id, active_key),
        )
        if existing:
            return _decode(existing)
        raise
    return _decode(await db.fetch_one("SELECT * FROM m365_jobs WHERE id = %s", (job_id,)))


def _decode(row: Any) -> dict[str, Any]:
    item = dict(row)
    for field in ("payload", "result"):
        if isinstance(item.get(field), str):
            try:
                item[field] = json.loads(item[field])
            except json.JSONDecodeError:
                item[field] = None
    return item


async def get(job_id: str, company_id: int) -> dict[str, Any] | None:
    row = await db.fetch_one("SELECT * FROM m365_jobs WHERE id = %s AND company_id = %s", (job_id, company_id))
    return _decode(row) if row else None


async def _claim() -> dict[str, Any] | None:
    now, lease = _now(), _now() + timedelta(seconds=LEASE_SECONDS)
    rows = await db.fetch_all(
        "SELECT * FROM m365_jobs WHERE available_at <= %s AND (status = 'queued' OR (status = 'running' AND lease_expires_at < %s)) ORDER BY created_at LIMIT 10",
        (now, now),
    )
    for raw in rows:
        row = dict(raw)
        old_owner, old_lease = row.get("owner_id"), row.get("lease_expires_at")
        await db.execute(
            "UPDATE m365_jobs SET status='running',owner_id=%s,lease_expires_at=%s,heartbeat_at=%s,started_at=COALESCE(started_at,%s),attempt_count=attempt_count+1,updated_at=%s WHERE id=%s AND ((owner_id IS NULL AND %s IS NULL) OR owner_id=%s) AND ((lease_expires_at IS NULL AND %s IS NULL) OR lease_expires_at=%s)",
            (instance_id(), lease, now, now, now, row["id"], old_owner, old_owner, old_lease, old_lease),
        )
        claimed = await db.fetch_one("SELECT * FROM m365_jobs WHERE id=%s", (row["id"],))
        claimed_row = dict(claimed) if claimed else None
        if claimed_row and claimed_row.get("owner_id") == instance_id():
            return _decode(claimed_row)
    return None


async def _heartbeat(job_id: str, finished: asyncio.Event) -> None:
    while not finished.is_set():
        try:
            await asyncio.wait_for(finished.wait(), LEASE_SECONDS / 3)
            return
        except asyncio.TimeoutError:
            now = _now()
            await db.execute(
                "UPDATE m365_jobs SET heartbeat_at=%s,lease_expires_at=%s,updated_at=%s WHERE id=%s AND owner_id=%s AND status='running'",
                (now, now + timedelta(seconds=LEASE_SECONDS), now, job_id, instance_id()),
            )


async def run_once() -> bool:
    job = await _claim()
    if not job:
        return False
    status, result, error = "succeeded", None, None
    finished = asyncio.Event()
    heartbeat = asyncio.create_task(_heartbeat(job["id"], finished))
    try:
        handler = _handlers.get(job["job_type"])
        if handler is None:
            raise RuntimeError("Operation type is not available on this worker")
        result = await handler(job)
        if result and result.get("status") == "partial":
            status = "partial"
    except Exception as exc:  # noqa: BLE001 - durable boundary sanitises the response
        status, error = "failed", "The provider operation failed. Review server logs for details."
        log_error("Durable M365 job failed", job_id=job["id"], job_type=job["job_type"], error=str(exc))
    finally:
        finished.set()
        await heartbeat
    now = _now()
    await db.execute(
        "UPDATE m365_jobs SET status=%s,result=%s,safe_error=%s,active_key=NULL,completed_at=%s,updated_at=%s,owner_id=NULL,lease_expires_at=NULL WHERE id=%s AND owner_id=%s",
        (status, json.dumps(result) if result is not None else None, error, now, now, job["id"], instance_id()),
    )
    return True


async def _loop() -> None:
    assert _stop is not None
    while not _stop.is_set():
        if not await run_once():
            try:
                await asyncio.wait_for(_stop.wait(), POLL_SECONDS)
            except asyncio.TimeoutError:
                pass


def start_worker() -> None:
    global _stop, _worker
    if _worker and not _worker.done():
        return
    _stop = asyncio.Event()
    _worker = asyncio.create_task(_loop())


async def stop_worker() -> None:
    if _stop:
        _stop.set()
    if _worker:
        await _worker
