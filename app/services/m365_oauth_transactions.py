"""Short-lived, single-use server-side state for Microsoft OAuth redirects.

The browser receives only a random transaction identifier.  In particular,
PKCE verifiers and authorization context are never put in signed state (a
signature provides integrity, not confidentiality). Redis is preferred; the
database provides shared durable storage when Redis is not configured so a
callback can safely land on any web worker.

Legacy state containing callback context is deliberately not accepted.  It
cannot be made single-use and has no trustworthy issue time, so failing it
safely is preferable to extending an unbounded compatibility window.  This
does not affect already-stored Microsoft credentials.
"""

from __future__ import annotations

import json
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from cryptography.exceptions import InvalidTag

from app.repositories import m365_oauth_transactions as transaction_repo
from app.security.encryption import decrypt_secret, encrypt_secret
from app.services.redis import get_redis_client

TTL_SECONDS = 600
_PREFIX = "m365:oauth:transaction:"


async def create(**context: Any) -> str:
    transaction_id = secrets.token_urlsafe(32)
    payload = dict(context)
    payload["created_at"] = datetime.now(timezone.utc).isoformat()
    redis = get_redis_client()
    if redis is not None:
        await redis.setex(_PREFIX + transaction_id, TTL_SECONDS, json.dumps(payload))
        return transaction_id
    expires_at = datetime.now(timezone.utc) + timedelta(seconds=TTL_SECONDS)
    await transaction_repo.create(
        transaction_id, encrypt_secret(json.dumps(payload)), expires_at
    )
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
    encrypted_payload = await transaction_repo.consume(transaction_id)
    if not encrypted_payload:
        return None
    try:
        return json.loads(decrypt_secret(encrypted_payload))
    except (InvalidTag, TypeError, ValueError):
        return None
