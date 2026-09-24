"""Short-lived, single-use server-side state for Microsoft OAuth redirects.

The browser receives only a random transaction identifier.  In particular,
PKCE verifiers and authorization context are never put in signed state (a
signature provides integrity, not confidentiality).  Deployments with more
than one worker must configure Redis; the in-process store is a development
fallback only.

Legacy state containing callback context is deliberately not accepted.  It
cannot be made single-use and has no trustworthy issue time, so failing it
safely is preferable to extending an unbounded compatibility window.  This
does not affect already-stored Microsoft credentials.
"""

from __future__ import annotations

import asyncio
import json
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from app.services.redis import get_redis_client

TTL_SECONDS = 600
_PREFIX = "m365:oauth:transaction:"
_store: dict[str, tuple[dict[str, Any], datetime]] = {}
_lock = asyncio.Lock()


async def create(**context: Any) -> str:
    transaction_id = secrets.token_urlsafe(32)
    payload = dict(context)
    payload["created_at"] = datetime.now(timezone.utc).isoformat()
    redis = get_redis_client()
    if redis is not None:
        await redis.setex(_PREFIX + transaction_id, TTL_SECONDS, json.dumps(payload))
        return transaction_id
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=TTL_SECONDS)
    async with _lock:
        _store[transaction_id] = (payload, expires_at)
    return transaction_id


async def consume(transaction_id: str) -> dict[str, Any] | None:
    """Atomically consume a transaction, returning ``None`` on replay/expiry."""
    if not transaction_id:
        return None
    redis = get_redis_client()
    if redis is not None:
        key = _PREFIX + transaction_id
        # Redis GETDEL is atomic and prevents two callback workers succeeding.
        raw = await redis.getdel(key)
        if not raw:
            return None
        try:
            return json.loads(raw)
        except (TypeError, ValueError):
            return None
    now = datetime.now(timezone.utc)
    async with _lock:
        entry = _store.pop(transaction_id, None)
        for key, (_, expiry) in list(_store.items()):
            if expiry <= now:
                _store.pop(key, None)
    if not entry or entry[1] <= now:
        return None
    return entry[0]
