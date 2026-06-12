from datetime import datetime, timedelta

import asyncio

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.api.dependencies import auth as auth_dependencies
from app.security.session import SessionData


def _request(path: str) -> Request:
    return Request({"type": "http", "method": "GET", "path": path, "headers": []})


def _session() -> SessionData:
    now = datetime.utcnow()
    return SessionData(
        id=1,
        user_id=42,
        session_token="session-token",
        csrf_token="csrf-token",
        created_at=now,
        expires_at=now + timedelta(hours=1),
        last_seen_at=now,
        ip_address="127.0.0.1",
        user_agent="pytest",
    )


def test_get_current_user_blocks_non_enrolment_api_without_totp(monkeypatch):
    async def fake_get_user_by_id(user_id):
        return {"id": user_id, "email": "user@example.com"}

    async def fake_has_totp(user_id):
        return False

    monkeypatch.setattr(auth_dependencies.user_repo, "get_user_by_id", fake_get_user_by_id)
    monkeypatch.setattr(auth_dependencies.auth_repo, "user_has_totp_authenticator", fake_has_totp)

    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(auth_dependencies.get_current_user(_request("/api/tickets"), _session()))

    assert exc_info.value.status_code == 403
    assert exc_info.value.headers == {"X-MyPortal-2FA-Enrolment-Required": "true"}
    assert "Two-factor authentication enrolment is required" in exc_info.value.detail


def test_get_current_user_allows_totp_setup_api_without_totp(monkeypatch):
    async def fake_get_user_by_id(user_id):
        return {"id": user_id, "email": "user@example.com"}

    async def fail_if_checked(user_id):  # pragma: no cover - assertion helper
        raise AssertionError("TOTP enrolment endpoints must remain reachable")

    monkeypatch.setattr(auth_dependencies.user_repo, "get_user_by_id", fake_get_user_by_id)
    monkeypatch.setattr(auth_dependencies.auth_repo, "user_has_totp_authenticator", fail_if_checked)

    user = asyncio.run(auth_dependencies.get_current_user(_request("/auth/totp/setup"), _session()))

    assert user["id"] == 42
