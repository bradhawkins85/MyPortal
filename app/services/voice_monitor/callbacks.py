"""Authenticated, replay-safe provider callback processing."""
from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime, timedelta, timezone

from app.core.database import db


class InvalidCallback(PermissionError):
    pass


def verify_signature(body: bytes, signature: str | None, timestamp: str | None,
                     secret: str, *, now: datetime | None = None, tolerance_seconds: int = 300) -> str:
    if not signature or not timestamp or not secret:
        raise InvalidCallback("callback authentication unavailable")
    try:
        sent = datetime.fromtimestamp(int(timestamp), timezone.utc)
    except (TypeError, ValueError, OSError) as exc:
        raise InvalidCallback("invalid callback timestamp") from exc
    now = now or datetime.now(timezone.utc)
    if abs((now - sent).total_seconds()) > tolerance_seconds:
        raise InvalidCallback("stale callback")
    digest = hmac.new(secret.encode(), timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()
    supplied = signature.removeprefix("sha256=")
    if not hmac.compare_digest(digest, supplied):
        raise InvalidCallback("invalid callback signature")
    return hashlib.sha256(timestamp.encode() + b"." + body).hexdigest()


async def handle_callback(body: bytes, signature: str | None, timestamp: str | None,
                          secret: str) -> bool:
    event_hash = verify_signature(body, signature, timestamp, secret)
    try:
        payload = json.loads(body)
        call_id = str(payload["call_id"])
        event_id = str(payload["event_id"])
        status = str(payload["status"]).lower()
    except (ValueError, KeyError, TypeError) as exc:
        raise InvalidCallback("invalid callback payload") from exc
    if status not in {"answered", "completed", "busy", "no_answer", "failed"}:
        raise InvalidCallback("unsupported callback status")
    verb = "INSERT OR IGNORE" if db.is_sqlite() else "INSERT IGNORE"
    inserted = await db.execute_rowcount(
        f"{verb} INTO voice_monitor_callback_events (provider_event_id,event_hash,provider_call_id,event_status) VALUES (%s,%s,%s,%s)",
        (event_id[:255], event_hash, call_id[:255], status),
    )
    if not inserted:
        return False
    terminal = status in {"completed", "busy", "no_answer", "failed"}
    outcome = "passed" if status == "completed" else ("answered" if not terminal else "failed")
    await db.execute_rowcount(
        "UPDATE voice_monitor_attempts SET outcome_status=%s, answered_at=CASE WHEN %s='answered' THEN COALESCE(answered_at,UTC_TIMESTAMP(6)) ELSE answered_at END, "
        "completed_at=CASE WHEN %s=1 THEN COALESCE(completed_at,UTC_TIMESTAMP(6)) ELSE completed_at END, final_callback_at=CASE WHEN %s=1 THEN UTC_TIMESTAMP(6) ELSE final_callback_at END "
        "WHERE provider_call_id=%s AND completed_at IS NULL", (outcome, status, terminal, terminal, call_id),
    )
    return True


async def calls_missing_final_callback(*, now: datetime | None = None, grace_minutes: int = 10) -> list[dict]:
    cutoff = (now or datetime.now(timezone.utc)).replace(tzinfo=None) - timedelta(minutes=grace_minutes)
    return await db.fetch_all(
        "SELECT * FROM voice_monitor_attempts WHERE provider_call_id IS NOT NULL AND final_callback_at IS NULL "
        "AND started_at<%s AND outcome_status IN ('dialing','answered')", (cutoff,),
    )
