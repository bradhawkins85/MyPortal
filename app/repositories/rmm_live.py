"""Storage for RMM live tools sessions and the frames relayed between workers."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Iterable

from app.core.database import db

OPEN_STATUSES = ("requested", "connected")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


async def create_session(
    *,
    company_id: int,
    asset_id: int,
    agent_id: int,
    requested_by_user_id: int | None,
    browser_token_hash: str,
    expires_at: datetime,
) -> int:
    now = _utcnow()
    return await db.execute_returning_lastrowid(
        "INSERT INTO rmm_live_sessions (company_id, asset_id, agent_id, requested_by_user_id, browser_token_hash, "
        "status, created_at, last_activity_at, expires_at) VALUES (%s, %s, %s, %s, %s, 'requested', %s, %s, %s)",
        (company_id, asset_id, agent_id, requested_by_user_id, browser_token_hash, now, now, expires_at),
    )


async def get_session(session_id: int) -> dict[str, Any] | None:
    row = await db.fetch_one("SELECT * FROM rmm_live_sessions WHERE id = %s", (session_id,))
    return dict(row) if row else None


async def offer_requested(agent_id: int, *, reoffer_after: timedelta) -> list[int]:
    """Requested sessions to list in this agent's job poll, marking them offered.

    A session is offered again only after ``reoffer_after``, so a poll that
    follows the one that offered it does not return straight away while the
    agent is still connecting."""

    now = _utcnow()
    rows = await db.fetch_all(
        "SELECT id FROM rmm_live_sessions WHERE agent_id = %s AND status = 'requested' AND expires_at > %s "
        "AND (offered_at IS NULL OR offered_at < %s) ORDER BY id",
        (agent_id, now, now - reoffer_after),
    )
    ids = [int(row["id"]) for row in rows]
    for session_id in ids:
        await db.execute("UPDATE rmm_live_sessions SET offered_at = %s WHERE id = %s", (now, session_id))
    return ids


async def agents_with_unoffered(*, reoffer_after: timedelta) -> set[int]:
    """Agents with a requested session waiting to be offered (one query per worker)."""

    now = _utcnow()
    rows = await db.fetch_all(
        "SELECT DISTINCT agent_id FROM rmm_live_sessions WHERE status = 'requested' AND expires_at > %s "
        "AND (offered_at IS NULL OR offered_at < %s)",
        (now, now - reoffer_after),
    )
    return {int(row["agent_id"]) for row in rows}


async def mark_agent_connected(session_id: int) -> bool:
    now = _utcnow()
    changed = await db.execute_rowcount(
        "UPDATE rmm_live_sessions SET status = 'connected', agent_connected_at = %s, last_activity_at = %s "
        "WHERE id = %s AND status IN ('requested', 'connected') AND expires_at > %s",
        (now, now, session_id, now),
    )
    return changed > 0


async def mark_agent_disconnected(session_id: int) -> None:
    """The agent's socket dropped: offer the session to it again."""

    await db.execute(
        "UPDATE rmm_live_sessions SET status = 'requested', offered_at = NULL, agent_connected_at = NULL "
        "WHERE id = %s AND status = 'connected'",
        (session_id,),
    )


async def mark_browser_connected(session_id: int) -> None:
    now = _utcnow()
    await db.execute(
        "UPDATE rmm_live_sessions SET browser_connected_at = %s, last_activity_at = %s WHERE id = %s",
        (now, now, session_id),
    )


async def touch(session_id: int) -> None:
    await db.execute("UPDATE rmm_live_sessions SET last_activity_at = %s WHERE id = %s", (_utcnow(), session_id))


async def close_session(session_id: int, reason: str) -> bool:
    changed = await db.execute_rowcount(
        "UPDATE rmm_live_sessions SET status = 'closed', closed_at = %s, close_reason = %s "
        "WHERE id = %s AND status IN ('requested', 'connected')",
        (_utcnow(), reason[:255], session_id),
    )
    return changed > 0


async def open_sessions_for(asset_id: int, user_id: int | None) -> list[int]:
    rows = await db.fetch_all(
        "SELECT id FROM rmm_live_sessions WHERE asset_id = %s AND requested_by_user_id = %s "
        "AND status IN ('requested', 'connected')",
        (asset_id, user_id),
    )
    return [int(row["id"]) for row in rows]


async def expire_sessions() -> list[int]:
    """Close sessions past their expiry; returns their IDs."""

    now = _utcnow()
    rows = await db.fetch_all(
        "SELECT id FROM rmm_live_sessions WHERE status IN ('requested', 'connected') AND expires_at <= %s", (now,)
    )
    ids = [int(row["id"]) for row in rows]
    for session_id in ids:
        await close_session(session_id, "expired")
    return ids


async def recent_sessions(asset_id: int, *, limit: int = 10) -> list[dict[str, Any]]:
    rows = await db.fetch_all(
        "SELECT s.id, s.status, s.created_at, s.agent_connected_at, s.closed_at, s.close_reason, "
        "s.requested_by_user_id, u.email AS requested_by_email FROM rmm_live_sessions s "
        "LEFT JOIN users u ON u.id = s.requested_by_user_id WHERE s.asset_id = %s ORDER BY s.id DESC LIMIT %s",
        (asset_id, limit),
    )
    return [dict(row) for row in rows]


# --------------------------------------------------------------------------- #
# Frames relayed through the database (no Redis, different workers)
# --------------------------------------------------------------------------- #


async def insert_frame(channel: str, payload: str) -> None:
    await db.execute(
        "INSERT INTO rmm_live_frames (channel, payload, created_at) VALUES (%s, %s, %s)",
        (channel, payload, _utcnow()),
    )


async def read_frames(channel: str, after_id: int, limit: int = 200) -> list[dict[str, Any]]:
    rows = await db.fetch_all(
        "SELECT id, payload FROM rmm_live_frames WHERE channel = %s AND id > %s ORDER BY id LIMIT %s",
        (channel, after_id, limit),
    )
    return [dict(row) for row in rows]


async def delete_frames(channel: str, up_to_id: int) -> None:
    await db.execute("DELETE FROM rmm_live_frames WHERE channel = %s AND id <= %s", (channel, up_to_id))


async def delete_channels(channels: Iterable[str]) -> None:
    for channel in channels:
        await db.execute("DELETE FROM rmm_live_frames WHERE channel = %s", (channel,))


async def sweep_frames(older_than: timedelta) -> None:
    await db.execute("DELETE FROM rmm_live_frames WHERE created_at < %s", (_utcnow() - older_than,))
