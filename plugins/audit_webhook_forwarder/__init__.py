"""Sample plugin forwarding audit logs to an external webhook."""

from __future__ import annotations

import asyncio
import os
from typing import Any

import httpx

from app.core.features import FeaturePack
from app.repositories import audit_logs as audit_logs_repo


_client: httpx.AsyncClient | None = None
_stop_event = asyncio.Event()
_last_forwarded_id = 0


def _endpoint() -> str:
    return (os.getenv("PLUGIN_AUDIT_FORWARDER_URL") or "").strip()


def _interval_seconds() -> int:
    raw = (os.getenv("PLUGIN_AUDIT_FORWARDER_INTERVAL_SECONDS") or "60").strip()
    try:
        return max(5, int(raw))
    except ValueError:
        return 60


def _batch_size() -> int:
    raw = (os.getenv("PLUGIN_AUDIT_FORWARDER_BATCH_SIZE") or "50").strip()
    try:
        return max(1, min(200, int(raw)))
    except ValueError:
        return 50


async def _startup() -> None:
    global _client
    _stop_event.clear()
    if _client is None:
        _client = httpx.AsyncClient(timeout=10.0)


async def _shutdown() -> None:
    global _client
    _stop_event.set()
    if _client is not None:
        await _client.aclose()
        _client = None


async def _forward_audit_logs() -> None:
    global _last_forwarded_id
    while not _stop_event.is_set():
        await asyncio.sleep(_interval_seconds())
        url = _endpoint()
        if not url or _client is None:
            continue
        rows = await audit_logs_repo.list_audit_logs(limit=_batch_size(), offset=0)
        rows = [row for row in rows if int(row.get("id") or 0) > _last_forwarded_id]
        if not rows:
            continue
        payload: dict[str, Any] = {"logs": rows}
        response = await _client.post(url, json=payload)
        response.raise_for_status()
        _last_forwarded_id = max(int(row.get("id") or 0) for row in rows)


PACK = FeaturePack(
    slug="plugin.audit_webhook_forwarder",
    version="1.0.0",
    author="MyPortal Team",
    description="Background plugin forwarding recent audit logs to an external webhook.",
    homepage="https://github.com/bradhawkins85/MyPortal",
    startup=_startup,
    shutdown=_shutdown,
    background_jobs=(_forward_audit_logs,),
)
