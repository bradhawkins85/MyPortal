"""Distributed singleton-job lease helper.

In a multi-instance deployment (Track B2 — blue/green) the same
``APScheduler`` job must not fire from more than one process, otherwise
side-effects (emails, webhooks, sync) double up.  This module provides
a tiny lease primitive that any background task can wrap itself in:

    from app.services.singleton_jobs import singleton_run

    @singleton_run("sync_xero", ttl_seconds=600)
    async def sync_xero() -> None:
        ...

The lease is stored in the ``singleton_jobs`` table (see
``migrations/248_singleton_jobs.sql``) and rotates ownership via an
optimistic update on ``owner_id`` + ``expires_at``.  A row whose lease
expired is considered free.

The helper is safe to use on a single instance too — it becomes
effectively a no-op apart from the row write.
"""

from __future__ import annotations

import asyncio
import os
import socket
import uuid
from datetime import datetime, timedelta, timezone
from functools import wraps
from typing import Any, Awaitable, Callable

from loguru import logger

from app.core.database import db


_INSTANCE_ID = f"{socket.gethostname()}-{os.getpid()}-{uuid.uuid4().hex[:8]}"


def instance_id() -> str:
    return _INSTANCE_ID


def _now() -> datetime:
    return datetime.now(timezone.utc)


async def acquire(job_name: str, ttl_seconds: int) -> bool:
    """Try to grab the lease for ``job_name``.

    Returns ``True`` if this instance now owns the lease.
    """

    expires_at = _now() + timedelta(seconds=ttl_seconds)
    expires_at_str = expires_at.strftime("%Y-%m-%d %H:%M:%S")
    now_str = _now().strftime("%Y-%m-%d %H:%M:%S")

    if db.is_sqlite():
        # Atomic upsert: insert if missing, otherwise update only when
        # the existing lease has expired.
        try:
            await db.execute(
                """
                INSERT INTO singleton_jobs (job_name, owner_id, expires_at, updated_at)
                VALUES (?, ?, ?, ?)
                """,
                (job_name, _INSTANCE_ID, expires_at_str, now_str),
            )
            return True
        except Exception:
            # Row exists – try to steal an expired lease.
            row = await db.fetch_one(
                "SELECT owner_id, expires_at FROM singleton_jobs WHERE job_name = ?",
                (job_name,),
            )
            if row is None:
                return False
            if str(row["owner_id"]) == _INSTANCE_ID:
                await db.execute(
                    "UPDATE singleton_jobs SET expires_at = ?, updated_at = ? WHERE job_name = ?",
                    (expires_at_str, now_str, job_name),
                )
                return True
            existing_expires = row["expires_at"]
            if existing_expires and str(existing_expires) > now_str:
                return False
            # Best-effort steal.  On SQLite this race is benign because
            # the helper is mainly used in single-instance mode.
            await db.execute(
                """
                UPDATE singleton_jobs
                SET owner_id = ?, expires_at = ?, updated_at = ?
                WHERE job_name = ? AND (owner_id = ? OR expires_at <= ?)
                """,
                (_INSTANCE_ID, expires_at_str, now_str, job_name, row["owner_id"], now_str),
            )
            confirm = await db.fetch_one(
                "SELECT owner_id FROM singleton_jobs WHERE job_name = ?",
                (job_name,),
            )
            return bool(confirm and str(confirm["owner_id"]) == _INSTANCE_ID)

    # MySQL path uses INSERT ... ON DUPLICATE KEY UPDATE with a
    # conditional update so only an expired or self-owned lease is
    # overwritten.  The ``ROW_COUNT()`` returned by MySQL distinguishes
    # an insert (1) from an actual update (2) and a no-op (0).
    await db.execute(
        """
        INSERT INTO singleton_jobs (job_name, owner_id, expires_at, updated_at)
        VALUES (%s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            owner_id = IF(owner_id = VALUES(owner_id) OR expires_at <= VALUES(updated_at),
                          VALUES(owner_id), owner_id),
            expires_at = IF(owner_id = VALUES(owner_id) OR expires_at <= VALUES(updated_at),
                            VALUES(expires_at), expires_at),
            updated_at = VALUES(updated_at)
        """,
        (job_name, _INSTANCE_ID, expires_at_str, now_str),
    )
    row = await db.fetch_one(
        "SELECT owner_id FROM singleton_jobs WHERE job_name = %s",
        (job_name,),
    )
    return bool(row and str(row["owner_id"]) == _INSTANCE_ID)


async def release(job_name: str) -> None:
    """Release the lease for ``job_name`` if we still own it."""

    placeholder = "?" if db.is_sqlite() else "%s"
    sql = (
        f"UPDATE singleton_jobs SET expires_at = {placeholder}, updated_at = {placeholder} "
        f"WHERE job_name = {placeholder} AND owner_id = {placeholder}"
    )
    epoch = "1970-01-01 00:00:00"
    now_str = _now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        await db.execute(sql, (epoch, now_str, job_name, _INSTANCE_ID))
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("Failed to release singleton lease {job}: {error}", job=job_name, error=str(exc))


def singleton_run(job_name: str, ttl_seconds: int = 300):
    """Decorator that runs the wrapped coroutine only when this instance owns ``job_name``."""

    def _decorator(func: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
        @wraps(func)
        async def _wrapper(*args: Any, **kwargs: Any) -> Any:
            owned = False
            try:
                owned = await acquire(job_name, ttl_seconds)
            except Exception as exc:
                logger.warning(
                    "singleton_jobs.acquire failed for {job}: {error}",
                    job=job_name,
                    error=str(exc),
                )
                # If the lease backend is unavailable, fall back to
                # running the job.  Better to risk a duplicate than to
                # silently drop scheduled work in single-instance mode.
                owned = True
            if not owned:
                logger.debug(
                    "Skipping {job}: lease held by another instance", job=job_name
                )
                return None
            try:
                return await func(*args, **kwargs)
            finally:
                # We intentionally do NOT release the lease here on
                # success.  Letting it expire naturally is what
                # prevents two instances from both grabbing it
                # immediately after a job finishes early.
                await asyncio.sleep(0)

        return _wrapper

    return _decorator


__all__ = ["acquire", "release", "singleton_run", "instance_id"]
