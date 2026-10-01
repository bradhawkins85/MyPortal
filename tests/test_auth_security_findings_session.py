"""Regression tests: sessions have an absolute lifetime measured from creation."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from starlette.requests import Request

from app.security.session import SessionManager


def _request(token: str = "raw-token") -> Request:
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [(b"cookie", f"myportal_session={token}".encode())],
            "client": ("127.0.0.1", 1234),
            "server": ("testserver", 80),
            "scheme": "http",
        }
    )


def _record(created_at: datetime) -> dict:
    now = datetime.utcnow()
    return {
        "id": 5,
        "user_id": 9,
        "session_token": "digest",
        "csrf_token": "csrf",
        "created_at": created_at,
        "expires_at": now + timedelta(hours=6),
        "last_seen_at": now - timedelta(minutes=1),
        "ip_address": None,
        "user_agent": None,
        "is_active": 1,
    }


@pytest.fixture
def manager(monkeypatch):
    mgr = SessionManager()
    mgr.session_cookie_name = "myportal_session"
    mgr.session_absolute_ttl = timedelta(hours=168)
    return mgr


@pytest.mark.anyio("asyncio")
async def test_session_past_absolute_lifetime_is_deactivated(monkeypatch, manager):
    updates: list[tuple[int, dict]] = []

    async def fake_get(token):
        return _record(datetime.utcnow() - timedelta(hours=169))

    async def fake_update(session_id, **kwargs):
        updates.append((session_id, kwargs))

    monkeypatch.setattr("app.security.session.auth_repo.get_session_by_token", fake_get)
    monkeypatch.setattr("app.security.session.auth_repo.update_session", fake_update)

    assert await manager.load_session(_request()) is None
    assert updates == [(5, {"is_active": False})]


@pytest.mark.anyio("asyncio")
async def test_sliding_expiry_never_extends_past_absolute_lifetime(monkeypatch, manager):
    created = datetime.utcnow() - timedelta(hours=165)
    updates: list[dict] = []

    async def fake_get(token):
        return _record(created)

    async def fake_update(session_id, **kwargs):
        updates.append(kwargs)

    monkeypatch.setattr("app.security.session.auth_repo.get_session_by_token", fake_get)
    monkeypatch.setattr("app.security.session.auth_repo.update_session", fake_update)

    session = await manager.load_session(_request())
    assert session is not None
    cap = created + timedelta(hours=168)
    assert session.expires_at == cap
    assert updates[-1]["expires_at"] == cap


@pytest.mark.anyio("asyncio")
async def test_fresh_session_still_slides_idle_expiry(monkeypatch, manager):
    async def fake_get(token):
        return _record(datetime.utcnow() - timedelta(hours=1))

    async def fake_update(session_id, **kwargs):
        return None

    monkeypatch.setattr("app.security.session.auth_repo.get_session_by_token", fake_get)
    monkeypatch.setattr("app.security.session.auth_repo.update_session", fake_update)

    before = datetime.utcnow()
    session = await manager.load_session(_request())
    assert session is not None
    assert session.expires_at >= before + manager.session_ttl


def test_absolute_ttl_setting_defaults_to_seven_days():
    from app.core.config import get_settings

    assert get_settings().session_absolute_ttl_hours == 168
    assert SessionManager().session_absolute_ttl == timedelta(days=7)
