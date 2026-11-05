"""RMM live tools: session lifecycle, agent offers and the cross-worker relay."""
from __future__ import annotations

import asyncio
from pathlib import Path

import aiosqlite
import pytest

from app.repositories import rmm as rmm_repo
from app.repositories import rmm_live as live_repo
from app.services import rmm_live
from app.services.tray import hash_token
from tests.test_rmm_scripts import _enrol, anyio_backend, sqlite_db  # noqa: F401 - fixtures

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _patch_live_repo(sqlite_db, monkeypatch):
    """The live sessions repository talks to the shared ``db`` object; point
    it at the test's SQLite connection like the other RMM repositories."""

    monkeypatch.setattr(live_repo, "db", sqlite_db)
    yield


async def _capable_agent(sqlite_db, *, supported=True):
    _result, agent = await _enrol(sqlite_db)
    await rmm_repo.record_agent_checkin(agent["id"], {"agent_version": "2.0.0", "live_tools_supported": supported})
    return await rmm_repo.get_agent(agent["id"])


@pytest.mark.anyio
async def test_migration_544_present_and_idempotent():
    sql = (ROOT / "migrations/544_rmm_live_sessions.sql").read_text()
    assert "CREATE TABLE IF NOT EXISTS rmm_live_sessions" in sql
    assert "CREATE TABLE IF NOT EXISTS rmm_live_frames" in sql
    assert "live_tools_supported" in sql
    # The runner requires a phase header.
    assert "-- phase: expand" in sql


@pytest.mark.anyio
async def test_start_session_requires_a_capable_agent(sqlite_db):
    old = await _capable_agent(sqlite_db, supported=False)
    with pytest.raises(rmm_live.LiveToolsUnsupported):
        await rmm_live.start_session(agent=old, company_id=1, asset_id=10, requested_by_user_id=9)


@pytest.mark.anyio
async def test_session_lifecycle_and_browser_auth(sqlite_db):
    agent = await _capable_agent(sqlite_db)
    started = await rmm_live.start_session(agent=agent, company_id=1, asset_id=10, requested_by_user_id=9)
    assert started.session_id

    # The agent sees the session in its poll, then not again until re-offer.
    offered = await rmm_live.sessions_for_agent(agent)
    assert [item["id"] for item in offered] == [started.session_id]
    assert await rmm_live.sessions_for_agent(agent) == []

    # Only the matching agent may serve it, and only with the right token.
    assert await rmm_live.authorize_agent_session(started.session_id, agent)
    assert await rmm_live.authorize_agent_session(started.session_id, {"id": agent["id"] + 999}) is None
    assert await rmm_live.authorize_browser(started.session_id, started.browser_token)
    assert await rmm_live.authorize_browser(started.session_id, "wrong-token") is None

    await live_repo.mark_agent_connected(started.session_id)
    assert (await live_repo.get_session(started.session_id))["status"] == "connected"

    await rmm_live.close_session(started.session_id, "done")
    assert (await live_repo.get_session(started.session_id))["status"] == "closed"
    # A closed session authorises nobody.
    assert await rmm_live.authorize_browser(started.session_id, started.browser_token) is None


@pytest.mark.anyio
async def test_expired_sessions_are_closed(sqlite_db):
    agent = await _capable_agent(sqlite_db)
    started = await rmm_live.start_session(agent=agent, company_id=1, asset_id=10, requested_by_user_id=9)
    # Force expiry into the past.
    from datetime import datetime, timedelta, timezone
    past = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=1)
    await sqlite_db.execute("UPDATE rmm_live_sessions SET expires_at = %s WHERE id = %s", (past, started.session_id))
    await rmm_live.expire_sessions()
    assert (await live_repo.get_session(started.session_id))["status"] == "closed"
    assert await rmm_live.sessions_for_agent(agent) == []


@pytest.mark.anyio
async def test_database_relay_carries_frames_both_ways(sqlite_db):
    """With no Redis configured, frames stored by one worker are read by the
    other and deleted once consumed."""

    agent = await _capable_agent(sqlite_db)
    started = await rmm_live.start_session(agent=agent, company_id=1, asset_id=10, requested_by_user_id=9)
    session_id = started.session_id

    # Two relays standing in for the browser's worker and the agent's worker.
    browser_side = rmm_live.Relay(session_id)
    agent_side = rmm_live.Relay(session_id)
    assert browser_side._redis is None, "test expects the database relay"

    received: list[str] = []

    async def collect(payload: str) -> bool:
        received.append(payload)
        return len(received) < 1

    await browser_side.send("to_agent", {"op": "ping"})
    reader = asyncio.ensure_future(agent_side.receive("to_agent", collect))
    await asyncio.wait_for(reader, timeout=5)
    assert received == ['{"op": "ping"}']

    await browser_side.close()
    await agent_side.close()


@pytest.mark.anyio
async def test_agent_disconnect_reoffers_the_session(sqlite_db):
    agent = await _capable_agent(sqlite_db)
    started = await rmm_live.start_session(agent=agent, company_id=1, asset_id=10, requested_by_user_id=9)
    await live_repo.offer_requested(int(agent["id"]), reoffer_after=rmm_live.REOFFER_AFTER)
    await live_repo.mark_agent_connected(started.session_id)
    await live_repo.mark_agent_disconnected(started.session_id)
    session = await live_repo.get_session(started.session_id)
    assert session["status"] == "requested" and session["offered_at"] is None
    assert [item["id"] for item in await rmm_live.sessions_for_agent(agent)] == [started.session_id]
