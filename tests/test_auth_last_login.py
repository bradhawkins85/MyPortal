from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import pytest
from starlette.requests import Request

from app.api.routes import auth as auth_routes
from app.schemas.auth import LoginRequest
from app.security.session import SessionData


@pytest.fixture
def anyio_backend():
    return "asyncio"


class _DummyAuthRepo:
    def __init__(self) -> None:
        self.cleared_identifier: str | None = None

    async def register_login_attempt(self, identifier: str, *, window_seconds: int, max_attempts: int) -> bool:
        return True

    async def clear_login_attempts(self, identifier: str) -> None:
        self.cleared_identifier = identifier

    async def get_totp_authenticators(self, user_id: int):
        return []


class _DummyUserRepo:
    def __init__(self) -> None:
        self.recorded_login: tuple[int, datetime] | None = None
        self.user = {
            "id": 42,
            "email": "admin@example.com",
            "password_hash": "hash",
            "company_id": 7,
            "is_active": 1,
            "is_super_admin": 1,
            "first_name": "Admin",
            "last_name": "User",
        }

    async def get_user_by_email(self, email: str):
        return dict(self.user) if email == self.user["email"] else None

    async def record_login(self, user_id: int, logged_in_at: datetime):
        self.recorded_login = (user_id, logged_in_at)
        updated = dict(self.user)
        updated["last_login_at"] = logged_in_at
        return updated


class _DummySessionManager:
    async def create_session(self, user_id: int, request: Request, *, active_company_id: int | None = None):
        return SessionData(
            id=99,
            user_id=user_id,
            session_token="token",
            csrf_token="csrf",
            created_at=datetime(2026, 1, 1),
            expires_at=datetime(2026, 1, 1, 12),
            last_seen_at=datetime(2026, 1, 1),
            ip_address="127.0.0.1",
            user_agent="pytest",
            active_company_id=active_company_id,
        )

    def apply_session_cookies(self, response, session, request):
        response.set_cookie("session", session.session_token)


@pytest.mark.anyio
async def test_login_records_last_login_timestamp(monkeypatch):
    auth_repo = _DummyAuthRepo()
    user_repo = _DummyUserRepo()
    monkeypatch.setattr(auth_routes, "auth_repo", auth_repo)
    monkeypatch.setattr(auth_routes, "user_repo", user_repo)
    monkeypatch.setattr(auth_routes, "verify_password", lambda password, password_hash: True)
    monkeypatch.setattr(auth_routes, "session_manager", _DummySessionManager())
    monkeypatch.setattr(auth_routes, "_determine_active_company_id", lambda user: _async_return(user.get("company_id")))

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

    response = await auth_routes.login(
        LoginRequest(email="admin@example.com", password="correct horse battery staple"),
        request,
        None,
    )

    assert response.status_code == 200
    assert user_repo.recorded_login is not None
    user_id, logged_in_at = user_repo.recorded_login
    assert user_id == 42
    assert isinstance(logged_in_at, datetime)
    assert auth_repo.cleared_identifier == "admin@example.com:127.0.0.1"


async def _async_return(value):
    return value
