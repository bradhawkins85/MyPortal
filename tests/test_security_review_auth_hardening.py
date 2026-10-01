"""Regression tests for the account and session hardening from the security review."""

from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.api.dependencies import auth as auth_dependencies
from app.api.routes import auth as auth_routes
from app.api.routes import staff as staff_routes
from app.api.routes import users as users_routes
from app.schemas.auth import PasswordResetConfirm, TOTPDeleteRequest
from app.schemas.users import UserUpdate
from app.security.passwords import hash_password
from app.security.session import SessionData


def _session(user_id: int = 7) -> SessionData:
    now = datetime.utcnow()
    return SessionData(
        id=3,
        user_id=user_id,
        session_token="raw",
        csrf_token="csrf",
        created_at=now,
        expires_at=now + timedelta(hours=1),
        last_seen_at=now,
        ip_address=None,
        user_agent=None,
    )


def _request(path: str = "/api/tickets"):
    return SimpleNamespace(url=SimpleNamespace(path=path), state=SimpleNamespace())


@pytest.mark.anyio("asyncio")
async def test_get_current_user_rejects_deactivated_account(monkeypatch):
    revoked = []

    async def fake_get_user(user_id):
        return {"id": user_id, "is_active": 0}

    async def fake_revoke(session):
        revoked.append(session.id)

    monkeypatch.setattr(auth_dependencies.user_repo, "get_user_by_id", fake_get_user)
    monkeypatch.setattr(auth_dependencies.session_manager, "revoke_session", fake_revoke)

    with pytest.raises(HTTPException) as exc:
        await auth_dependencies.get_current_user(_request(), _session())

    assert exc.value.status_code == 401
    assert revoked == [3]


@pytest.mark.anyio("asyncio")
async def test_get_optional_user_treats_deactivated_account_as_anonymous(monkeypatch):
    session = _session()

    async def fake_load_session(request, *, allow_inactive=False):
        return session

    async def fake_get_user(user_id):
        return {"id": user_id, "is_active": False}

    async def fake_revoke(_session):
        return None

    monkeypatch.setattr(auth_dependencies.session_manager, "load_session", fake_load_session)
    monkeypatch.setattr(auth_dependencies.session_manager, "revoke_session", fake_revoke)
    monkeypatch.setattr(auth_dependencies.user_repo, "get_user_by_id", fake_get_user)

    assert await auth_dependencies.get_optional_user(_request()) is None


@pytest.mark.anyio("asyncio")
async def test_get_optional_user_requires_two_factor_enrolment(monkeypatch):
    session = _session()

    async def fake_load_session(request, *, allow_inactive=False):
        return session

    async def fake_get_user(user_id):
        return {"id": user_id, "is_active": 1}

    async def fake_has_totp(user_id):
        return False

    monkeypatch.setattr(auth_dependencies.session_manager, "load_session", fake_load_session)
    monkeypatch.setattr(auth_dependencies.user_repo, "get_user_by_id", fake_get_user)
    monkeypatch.setattr(auth_dependencies.auth_repo, "user_has_totp_authenticator", fake_has_totp)

    assert await auth_dependencies.get_optional_user(_request("/api/staff/1")) is None


@pytest.mark.anyio("asyncio")
async def test_get_staff_requires_helpdesk_permission(monkeypatch):
    async def fake_has_permission(user_id, permission):
        return False

    async def fail_lookup(staff_id):  # pragma: no cover - must not be reached
        raise AssertionError("staff record should not be loaded")

    monkeypatch.setattr(staff_routes.membership_repo, "user_has_permission", fake_has_permission)
    monkeypatch.setattr(staff_routes.staff_repo, "get_staff_by_id", fail_lookup)

    with pytest.raises(HTTPException) as exc:
        await staff_routes.get_staff(
            staff_id=5,
            _=None,
            current_user={"id": 9, "is_super_admin": False},
            api_key_record=None,
        )

    assert exc.value.status_code == 403


@pytest.mark.anyio("asyncio")
async def test_password_reset_revokes_sessions_and_other_tokens(monkeypatch):
    calls = {}

    async def fake_get_token(token):
        return {"user_id": 11, "used": 0, "expires_at": datetime.utcnow() + timedelta(minutes=5)}

    async def fake_set_password(user_id, password):
        calls["password"] = user_id

    async def fake_mark_used(token):
        calls["used"] = token

    async def fake_invalidate(user_id):
        calls["invalidate"] = user_id

    async def fake_deactivate(user_id, *, except_session_id=None):
        calls["deactivate"] = (user_id, except_session_id)

    async def fake_record(**kwargs):
        return None

    monkeypatch.setattr(auth_routes.auth_repo, "get_password_reset_token", fake_get_token)
    monkeypatch.setattr(auth_routes.user_repo, "set_user_password", fake_set_password)
    monkeypatch.setattr(auth_routes.auth_repo, "mark_password_reset_token_used", fake_mark_used)
    monkeypatch.setattr(auth_routes.auth_repo, "invalidate_password_reset_tokens_for_user", fake_invalidate)
    monkeypatch.setattr(auth_routes.auth_repo, "deactivate_sessions_for_user", fake_deactivate)
    monkeypatch.setattr(auth_routes.audit_service, "record", fake_record)

    await auth_routes.password_reset(
        PasswordResetConfirm(token="t", password="a-long-new-password"),
        request=None,
        _=None,
    )

    assert calls["invalidate"] == 11
    assert calls["deactivate"] == (11, None)


@pytest.mark.anyio("asyncio")
async def test_delete_totp_requires_current_password(monkeypatch):
    async def fail_count(user_id):  # pragma: no cover - must not be reached
        raise AssertionError("password check must run first")

    monkeypatch.setattr(auth_routes.auth_repo, "count_totp_authenticators", fail_count)
    user = {"id": 4, "password_hash": hash_password("right-password-123")}

    with pytest.raises(HTTPException) as exc:
        await auth_routes.delete_totp(
            authenticator_id=1,
            payload=TOTPDeleteRequest(current_password="wrong-password"),
            request=None,
            current_user=user,
        )

    assert exc.value.status_code == 400


@pytest.mark.anyio("asyncio")
async def test_non_admin_cannot_change_own_company(monkeypatch):
    captured = {}

    async def fake_get_user(user_id):
        return {"id": user_id, "company_id": 1}

    async def fake_update_user(user_id, **data):
        captured.update(data)
        return {"id": user_id, "company_id": 1}

    async def fake_record(**kwargs):
        return None

    monkeypatch.setattr(users_routes.user_repo, "get_user_by_id", fake_get_user)
    monkeypatch.setattr(users_routes.user_repo, "update_user", fake_update_user)
    monkeypatch.setattr(users_routes.audit_service, "record", fake_record)

    await users_routes.update_user(
        user_id=5,
        payload=UserUpdate(company_id=2, first_name="Sam"),
        request=None,
        _=None,
        current_user={"id": 5, "is_super_admin": False},
    )

    assert "company_id" not in captured
    assert captured.get("first_name") == "Sam"


def test_password_reset_rate_limit_key_ignores_query_email():
    from app.main import _password_reset_key

    def make(query_email: str):
        return SimpleNamespace(
            query_params={"email": query_email},
            client=SimpleNamespace(host="198.51.100.4"),
            headers={},
        )

    assert _password_reset_key(make("a@example.com")) == _password_reset_key(make("b@example.com"))
