"""Regression tests: per-account login lockout and constant-work login."""

from __future__ import annotations

from datetime import datetime

import pyotp
import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.api.routes import auth as auth_routes
from app.schemas.auth import LoginRequest
from app.security.session import SessionData


def _request(ip: str = "10.0.0.1") -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/auth/login",
            "headers": [(b"user-agent", b"pytest")],
            "client": (ip, 1234),
            "server": ("testserver", 80),
            "scheme": "http",
        }
    )


class _Repo:
    """In-memory login_rate_limits plus the TOTP bits login needs."""

    def __init__(self, devices=None) -> None:
        self.counts: dict[str, int] = {}
        self.devices = devices or []

    async def register_login_attempt(self, identifier, *, window_seconds, max_attempts):
        self.counts[identifier] = self.counts.get(identifier, 0) + 1
        return identifier.startswith("account:") or self.counts[identifier] <= max_attempts

    async def get_login_attempt_count(self, identifier, *, window_seconds):
        return self.counts.get(identifier, 0)

    async def clear_login_attempts(self, identifier):
        self.counts.pop(identifier, None)

    async def get_totp_authenticators(self, user_id):
        return self.devices

    async def claim_totp_step(self, authenticator_id, step):
        return True


class _Users:
    def __init__(self) -> None:
        self.user = {
            "id": 42, "email": "victim@example.com", "password_hash": "stored",
            "company_id": 1, "is_active": 1, "is_super_admin": 0,
            "first_name": "V", "last_name": "U",
        }

    async def get_user_by_email(self, email):
        return dict(self.user) if email.lower() == self.user["email"] else None

    async def record_login(self, user_id, when):
        return dict(self.user)


class _Sessions:
    async def create_session(self, user_id, request, *, active_company_id=None):
        now = datetime(2026, 1, 1)
        return SessionData(
            id=1, user_id=user_id, session_token="t", csrf_token="c", created_at=now,
            expires_at=now, last_seen_at=now, ip_address=None, user_agent=None,
        )

    def apply_session_cookies(self, response, session, request):
        return None


@pytest.fixture
def env(monkeypatch):
    repo = _Repo()
    users = _Users()
    checked: list[str] = []

    def fake_verify(password, hashed):
        checked.append(hashed)
        return hashed == "stored" and password == "correct-password"

    async def noop(*args, **kwargs):
        return None

    async def company(user):
        return 1

    monkeypatch.setattr(auth_routes, "auth_repo", repo)
    monkeypatch.setattr(auth_routes, "user_repo", users)
    monkeypatch.setattr(auth_routes, "session_manager", _Sessions())
    monkeypatch.setattr(auth_routes, "verify_password", fake_verify)
    monkeypatch.setattr(auth_routes, "get_dummy_password_hash", lambda: "dummy")
    monkeypatch.setattr(auth_routes, "_determine_active_company_id", company)
    monkeypatch.setattr(auth_routes.audit_service, "record", noop)
    return repo, users, checked


async def _login(email, password, ip="10.0.0.1", totp=None):
    return await auth_routes.login(
        LoginRequest(email=email, password=password, totp_code=totp), _request(ip), None
    )


@pytest.mark.anyio("asyncio")
async def test_unknown_email_still_verifies_against_dummy_hash(env):
    _, _, checked = env
    with pytest.raises(HTTPException) as exc:
        await _login("nobody@example.com", "whatever-password")
    assert exc.value.status_code == 401
    assert exc.value.detail == "Invalid credentials"
    assert checked == ["dummy"]


@pytest.mark.anyio("asyncio")
async def test_account_locks_after_failures_from_many_addresses(env):
    repo, _, _ = env
    for attempt in range(auth_routes.ACCOUNT_LOCKOUT_ATTEMPTS):
        with pytest.raises(HTTPException):
            await _login("victim@example.com", "wrong-password", ip=f"10.0.1.{attempt}")

    # Correct password from a fresh address is now refused with the same
    # generic error until the window passes.
    with pytest.raises(HTTPException) as exc:
        await _login("victim@example.com", "correct-password", ip="10.9.9.9")
    assert exc.value.status_code == 401
    assert exc.value.detail == "Invalid credentials"

    repo.counts.pop("account:victim@example.com")
    response = await _login("victim@example.com", "correct-password", ip="10.9.9.9")
    assert response.status_code == 200


@pytest.mark.anyio("asyncio")
async def test_unknown_accounts_lock_the_same_way(env):
    for attempt in range(auth_routes.ACCOUNT_LOCKOUT_ATTEMPTS):
        with pytest.raises(HTTPException):
            await _login("ghost@example.com", "wrong-password", ip=f"10.0.2.{attempt}")
    repo, _, _ = env
    assert repo.counts["account:ghost@example.com"] == auth_routes.ACCOUNT_LOCKOUT_ATTEMPTS


@pytest.mark.anyio("asyncio")
async def test_success_clears_account_counter(env):
    repo, _, _ = env
    with pytest.raises(HTTPException):
        await _login("Victim@Example.com", "wrong-password")
    assert repo.counts["account:victim@example.com"] == 1
    await _login("victim@example.com", "correct-password", ip="10.0.0.2")
    assert "account:victim@example.com" not in repo.counts


@pytest.mark.anyio("asyncio")
async def test_wrong_totp_codes_count_toward_lockout(env, monkeypatch):
    repo, _, _ = env
    secret = pyotp.random_base32()
    repo.devices = [{"id": 1, "name": "Phone", "secret": secret, "last_used_step": None}]
    good = pyotp.TOTP(secret).now()
    bad = "123456" if good != "123456" else "654321"
    with pytest.raises(HTTPException) as exc:
        await _login("victim@example.com", "correct-password", totp=bad)
    assert exc.value.detail == "Invalid TOTP code"
    assert repo.counts["account:victim@example.com"] == 1
