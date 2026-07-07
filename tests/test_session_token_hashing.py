from __future__ import annotations

from datetime import datetime, timedelta
import sys
from types import SimpleNamespace

import pytest
from starlette.requests import Request

sys.modules.setdefault(
    "magic",
    SimpleNamespace(
        Magic=lambda *args, **kwargs: None,
        from_buffer=lambda *args, **kwargs: "application/octet-stream",
    ),
)

from app.repositories import auth as auth_repo
from app.security.session import SessionManager


@pytest.mark.anyio
async def test_create_session_returns_raw_cookie_token(monkeypatch):
    manager = SessionManager()
    hashed_token = "h" * 64
    raw_token = "r" * 64
    now = datetime.utcnow()

    monkeypatch.setattr("app.security.session.secrets_token", lambda: raw_token)

    async def fake_create_session(**kwargs):
        return {
            "id": 1,
            "user_id": kwargs["user_id"],
            "session_token": hashed_token,
            "csrf_token": kwargs["csrf_token"],
            "created_at": now,
            "expires_at": now + timedelta(hours=12),
            "last_seen_at": now,
            "ip_address": kwargs["ip_address"],
            "user_agent": kwargs["user_agent"],
            "is_active": 1,
            "active_company_id": kwargs["active_company_id"],
        }

    monkeypatch.setattr("app.security.session.auth_repo.create_session", fake_create_session)

    request = Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/auth/login",
            "headers": [(b"user-agent", b"pytest")],
            "client": ("127.0.0.1", 12345),
            "server": ("testserver", 80),
            "scheme": "http",
        }
    )

    session = await manager.create_session(2, request, active_company_id=1)

    assert session.session_token == raw_token
    assert session.session_token != hashed_token


@pytest.mark.anyio
async def test_get_session_by_token_accepts_raw_hash_fallback(monkeypatch):
    calls = []

    async def fake_fetch_one(sql, params):
        calls.append((sql, params))
        return {"id": 1}

    monkeypatch.setattr(auth_repo.db, "fetch_one", fake_fetch_one)

    row = await auth_repo.get_session_by_token("cookie-token")

    assert row == {"id": 1}
    sql, params = calls[0]
    assert "session_token = %s OR session_token = %s" in sql
    assert params == (auth_repo._hash_session_token("cookie-token"), "cookie-token")
