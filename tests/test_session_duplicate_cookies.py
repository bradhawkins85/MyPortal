from __future__ import annotations

from datetime import datetime, timedelta
import sys
from types import SimpleNamespace

import pytest

sys.modules.setdefault(
    "magic",
    SimpleNamespace(
        Magic=lambda *args, **kwargs: None,
        from_buffer=lambda *args, **kwargs: "application/octet-stream",
    ),
)
from starlette.requests import Request

from app.security.session import SessionManager


@pytest.mark.anyio
async def test_load_session_tries_duplicate_cookie_values(monkeypatch):
    manager = SessionManager()
    now = datetime.utcnow()
    valid_record = {
        "id": 1,
        "user_id": 2,
        "session_token": "new-token",
        "csrf_token": "csrf-token",
        "created_at": now,
        "expires_at": now + timedelta(hours=1),
        "last_seen_at": now,
        "ip_address": "203.0.113.10",
        "user_agent": "pytest",
        "is_active": 1,
        "active_company_id": None,
    }
    looked_up: list[str] = []

    async def fake_get_session_by_token(token: str):
        looked_up.append(token)
        return valid_record if token == "new-token" else None

    async def fake_update_session(*_args, **_kwargs):
        return None

    monkeypatch.setattr("app.security.session.auth_repo.get_session_by_token", fake_get_session_by_token)
    monkeypatch.setattr("app.security.session.auth_repo.update_session", fake_update_session)

    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [
                (
                    b"cookie",
                    f"{manager.session_cookie_name}=new-token; {manager.session_cookie_name}=old-token".encode(),
                )
            ],
            "client": ("127.0.0.1", 12345),
            "server": ("testserver", 80),
            "scheme": "http",
        }
    )

    session = await manager.load_session(request)

    assert session is not None
    assert session.session_token == "new-token"
    assert looked_up == ["old-token", "new-token"]
