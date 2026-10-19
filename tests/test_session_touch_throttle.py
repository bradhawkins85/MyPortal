"""Loading a session only rewrites last_seen_at/expires_at once per interval."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from starlette.requests import Request

from app.security.session import SESSION_TOUCH_INTERVAL, SessionManager


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [(b"cookie", b"myportal_session=raw-token")],
            "client": ("127.0.0.1", 1234),
            "server": ("testserver", 80),
            "scheme": "http",
        }
    )


@pytest.fixture
def manager():
    mgr = SessionManager()
    mgr.session_cookie_name = "myportal_session"
    return mgr


def _record(manager: SessionManager, *, last_seen_ago: timedelta, created_ago=timedelta(hours=1)) -> dict:
    now = datetime.utcnow()
    last_seen_at = now - last_seen_ago
    return {
        "id": 5,
        "user_id": 9,
        "session_token": "digest",
        "csrf_token": "csrf",
        "created_at": now - created_ago,
        "expires_at": last_seen_at + manager.session_ttl,
        "last_seen_at": last_seen_at,
        "ip_address": None,
        "user_agent": None,
        "is_active": 1,
    }


async def _load(monkeypatch, manager, record):
    updates: list[dict] = []

    async def fake_get(token):
        return record

    async def fake_update(session_id, **kwargs):
        updates.append(kwargs)

    monkeypatch.setattr("app.security.session.auth_repo.get_session_by_token", fake_get)
    monkeypatch.setattr("app.security.session.auth_repo.update_session", fake_update)
    return await manager.load_session(_request()), updates


@pytest.mark.anyio("asyncio")
async def test_recently_touched_session_skips_the_write(monkeypatch, manager):
    record = _record(manager, last_seen_ago=timedelta(seconds=5))

    session, updates = await _load(monkeypatch, manager, record)

    assert session is not None
    assert updates == []
    assert session.expires_at == record["expires_at"]
    assert session.last_seen_at == record["last_seen_at"]


@pytest.mark.anyio("asyncio")
async def test_session_is_touched_once_the_interval_passes(monkeypatch, manager):
    record = _record(manager, last_seen_ago=SESSION_TOUCH_INTERVAL + timedelta(seconds=1))
    before = datetime.utcnow()

    session, updates = await _load(monkeypatch, manager, record)

    assert len(updates) == 1
    assert updates[0]["last_seen_at"] >= before
    assert updates[0]["expires_at"] >= before + manager.session_ttl
    assert session.expires_at == updates[0]["expires_at"]


@pytest.mark.anyio("asyncio")
async def test_stored_expiry_past_the_absolute_cap_is_rewritten(monkeypatch, manager):
    # The stored expiry is later than the absolute lifetime allows, so it must
    # be corrected even though the session was seen moments ago.
    record = _record(
        manager,
        last_seen_ago=timedelta(seconds=5),
        created_ago=manager.session_absolute_ttl - timedelta(hours=1),
    )

    session, updates = await _load(monkeypatch, manager, record)

    cap = record["created_at"] + manager.session_absolute_ttl
    assert updates and updates[0]["expires_at"] == cap
    assert session.expires_at == cap


@pytest.mark.anyio("asyncio")
async def test_rounded_up_stored_timestamps_still_count_as_fresh(monkeypatch, manager):
    # DATETIME(0) columns can round a write up to the next second, leaving the
    # stored values fractionally in the future.
    record = _record(manager, last_seen_ago=-timedelta(milliseconds=400))

    session, updates = await _load(monkeypatch, manager, record)

    assert session is not None
    assert updates == []
