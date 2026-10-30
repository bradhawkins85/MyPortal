"""Required-mode enforcement: password, passkey and reset are refused for SSO-required users."""

from __future__ import annotations

import json
from datetime import datetime, timedelta

import pytest
from fastapi import HTTPException
from fastapi.responses import JSONResponse
from starlette.requests import Request

from app.api.routes import auth as auth_routes
from app.schemas.auth import (
    LoginRequest,
    PasskeyCredentialRequest,
    PasswordResetConfirm,
)
from app.security.session import SessionData
from app.services import sso_policy as _sso_policy

_REAL_POLICY = _sso_policy.password_login_allowed

REFUSAL = {"detail": "Your organisation requires Microsoft sign-in.", "sso_required": True}


def _request(path: str = "/auth/login") -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": path,
            "headers": [(b"user-agent", b"pytest")],
            "client": ("10.0.0.1", 1234),
            "server": ("testserver", 80),
            "scheme": "http",
        }
    )


class _Repo:
    def __init__(self) -> None:
        self.counts: dict[str, int] = {}
        self.devices: list[dict] = []
        self.reset_record: dict | None = None
        self.used_tokens: list[str] = []

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

    async def get_password_reset_token(self, token):
        return self.reset_record

    async def mark_password_reset_token_used(self, token):
        self.used_tokens.append(token)

    async def invalidate_password_reset_tokens_for_user(self, user_id):
        return None

    async def deactivate_sessions_for_user(self, user_id, **kwargs):
        return None


class _Users:
    def __init__(self) -> None:
        self.user = {
            "id": 42, "email": "victim@example.com", "password_hash": "stored",
            "company_id": 1, "is_active": 1, "is_super_admin": 0,
            "first_name": "V", "last_name": "U",
        }
        self.passwords_set: list[int] = []

    async def get_user_by_email(self, email):
        return dict(self.user) if email.lower() == self.user["email"] else None

    async def get_user_by_id(self, user_id):
        return dict(self.user) if user_id == self.user["id"] else None

    async def record_login(self, user_id, when):
        return dict(self.user)

    async def set_user_password(self, user_id, password):
        self.passwords_set.append(user_id)


class _Sessions:
    def __init__(self) -> None:
        self.created = 0

    async def create_session(self, user_id, request, *, active_company_id=None):
        self.created += 1
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
    sessions = _Sessions()
    audits: list[dict] = []
    policy = {"allowed": False}

    def fake_verify(password, hashed):
        return hashed == "stored" and password == "correct-password"

    async def record(**kwargs):
        audits.append(kwargs)

    async def company(user):
        return 1

    async def allowed(user):
        return policy["allowed"]

    monkeypatch.setattr(auth_routes, "auth_repo", repo)
    monkeypatch.setattr(auth_routes, "user_repo", users)
    monkeypatch.setattr(auth_routes, "session_manager", sessions)
    monkeypatch.setattr(auth_routes, "verify_password", fake_verify)
    monkeypatch.setattr(auth_routes, "get_dummy_password_hash", lambda: "dummy")
    monkeypatch.setattr(auth_routes, "_determine_active_company_id", company)
    monkeypatch.setattr(auth_routes.audit_service, "record", record)
    monkeypatch.setattr(auth_routes.sso_policy, "password_login_allowed", allowed)
    return repo, users, sessions, audits, policy


async def _login(password: str, email: str = "victim@example.com", totp=None):
    return await auth_routes.login(
        LoginRequest(email=email, password=password, totp_code=totp), _request(), None
    )


def _assert_refusal(response) -> None:
    assert isinstance(response, JSONResponse)
    assert response.status_code == 403
    assert json.loads(response.body) == REFUSAL
    assert "set-cookie" not in response.headers


def _refusal_audits(audits: list[dict]) -> list[dict]:
    return [a for a in audits if a["action"] == "auth.sso.fail"]


@pytest.mark.anyio("asyncio")
async def test_password_login_refused_after_correct_password(env):
    repo, _, sessions, audits, _ = env
    response = await _login("correct-password")
    _assert_refusal(response)
    assert sessions.created == 0
    assert repo.counts == {}
    (audit,) = _refusal_audits(audits)
    assert audit["user_id"] == 42
    assert audit["entity_type"] == "user"
    assert audit["entity_id"] == 42
    assert audit["metadata"] == {
        "reason": "password_login_refused",
        "authentication_method": "password",
        "outcome": "failure",
    }


@pytest.mark.anyio("asyncio")
async def test_wrong_password_still_generic_401_for_required_user(env):
    repo, _, sessions, audits, _ = env
    with pytest.raises(HTTPException) as exc:
        await _login("wrong-password")
    assert exc.value.status_code == 401
    assert exc.value.detail == "Invalid credentials"
    assert sessions.created == 0
    assert _refusal_audits(audits) == []


@pytest.mark.anyio("asyncio")
async def test_unknown_account_gets_same_401_as_wrong_password(env):
    with pytest.raises(HTTPException) as exc:
        await _login("correct-password", email="nobody@example.com")
    assert exc.value.status_code == 401
    assert exc.value.detail == "Invalid credentials"


@pytest.mark.anyio("asyncio")
async def test_refusal_does_not_precede_totp_check(env):
    repo, _, sessions, audits, _ = env
    repo.devices = [{"id": 1, "name": "phone", "secret": "x"}]
    with pytest.raises(HTTPException) as exc:
        await _login("correct-password")
    assert exc.value.status_code == 401
    assert exc.value.detail == "TOTP code required"
    assert _refusal_audits(audits) == []


@pytest.mark.anyio("asyncio")
async def test_disabled_account_keeps_its_own_error_before_refusal(env):
    _, users, _, audits, _ = env
    users.user["is_active"] = 0
    with pytest.raises(HTTPException) as exc:
        await _login("correct-password")
    assert exc.value.status_code == 403
    assert exc.value.detail != REFUSAL["detail"]
    assert _refusal_audits(audits) == []


@pytest.mark.anyio("asyncio")
async def test_allowed_user_still_logs_in(env):
    _, _, sessions, audits, policy = env
    policy["allowed"] = True
    response = await _login("correct-password")
    assert response.status_code == 200
    assert sessions.created == 1
    assert _refusal_audits(audits) == []


@pytest.mark.anyio("asyncio")
async def test_super_admin_password_login_allowed(env, monkeypatch):
    _, users, sessions, audits, _ = env
    users.user["is_super_admin"] = 1
    monkeypatch.setattr(auth_routes.sso_policy, "password_login_allowed", _REAL_POLICY)
    response = await _login("correct-password")
    assert response.status_code == 200
    assert sessions.created == 1
    assert _refusal_audits(audits) == []


class _PasskeyRepo(_Repo):
    def __init__(self) -> None:
        super().__init__()
        self.updated = 0

    async def get_passkey_challenge(self, challenge_id):
        return {"challenge": "abc"}

    async def consume_passkey_challenge(self, **kwargs):
        return True

    async def get_passkey_by_credential_id(self, credential_id):
        return {"id": 7, "user_id": 42, "credential_id": "cred", "public_key": "", "sign_count": 0}

    async def update_passkey_after_authentication(self, **kwargs):
        self.updated += 1


class _Verified:
    new_sign_count = 1
    credential_device_type = None
    credential_backed_up = False


async def _passkey_verify(monkeypatch, env):
    _, users, sessions, audits, policy = env
    repo = _PasskeyRepo()
    monkeypatch.setattr(auth_routes, "auth_repo", repo)
    monkeypatch.setattr(auth_routes, "_validated_passkey_login_cookie", lambda request: "binding")
    monkeypatch.setattr(auth_routes, "_get_passkey_credential_id", lambda credential: "cred")
    monkeypatch.setattr(auth_routes, "_passkey_request_origin", lambda request: "http://testserver")
    monkeypatch.setattr(auth_routes.passkeys_service, "browser_binding_hash", lambda b: "h")
    monkeypatch.setattr(auth_routes.passkeys_service, "credential_id_hash", lambda c: "h")
    monkeypatch.setattr(
        auth_routes.passkeys_service, "base64url_to_bytes_safe", lambda value: b""
    )
    monkeypatch.setattr(
        auth_routes.passkeys_service, "verify_authentication", lambda **kwargs: _Verified()
    )
    response = await auth_routes.finish_passkey_authentication(
        PasskeyCredentialRequest(challenge_id="c1", credential={"id": "cred"}),
        _request("/auth/passkeys/authenticate/verify"),
        None,
    )
    return response, repo


@pytest.mark.anyio("asyncio")
async def test_passkey_login_refused_for_required_user(env, monkeypatch):
    _, _, sessions, audits, _ = env
    response, repo = await _passkey_verify(monkeypatch, env)
    _assert_refusal(response)
    assert sessions.created == 0
    assert repo.updated == 1
    (audit,) = _refusal_audits(audits)
    assert audit["user_id"] == 42
    assert audit["metadata"] == {
        "reason": "password_login_refused",
        "authentication_method": "passkey",
        "outcome": "failure",
    }


@pytest.mark.anyio("asyncio")
async def test_passkey_login_allowed_when_policy_allows(env, monkeypatch):
    _, _, sessions, audits, policy = env
    policy["allowed"] = True
    response, _ = await _passkey_verify(monkeypatch, env)
    assert response.status_code == 200
    assert sessions.created == 1
    assert _refusal_audits(audits) == []


@pytest.mark.anyio("asyncio")
async def test_password_reset_email_not_sent_for_required_user(env, monkeypatch):
    sent: list[dict] = []

    async def issue(user):
        sent.append(user)

    monkeypatch.setattr(auth_routes, "_issue_password_reset_email", issue)
    await auth_routes._process_password_reset_request("victim@example.com")
    assert sent == []


@pytest.mark.anyio("asyncio")
async def test_password_reset_email_sent_when_policy_allows(env, monkeypatch):
    sent: list[dict] = []

    async def issue(user):
        sent.append(user)

    monkeypatch.setattr(auth_routes, "_issue_password_reset_email", issue)
    env[4]["allowed"] = True
    await auth_routes._process_password_reset_request("victim@example.com")
    assert [u["id"] for u in sent] == [42]


@pytest.mark.anyio("asyncio")
async def test_password_reset_response_unchanged(env, monkeypatch):
    from fastapi import BackgroundTasks

    background = BackgroundTasks()
    response = await auth_routes.password_forgot(
        auth_routes.PasswordResetRequest(email="victim@example.com"), background, None
    )
    assert response.status_code == 200
    assert json.loads(response.body) == {"detail": auth_routes.PASSWORD_RESET_REQUESTED_DETAIL}


@pytest.mark.anyio("asyncio")
async def test_reset_token_redemption_refused_for_required_user(env):
    repo, users, _, audits, _ = env
    repo.reset_record = {
        "user_id": 42, "used": 0, "expires_at": datetime.utcnow() + timedelta(hours=1)
    }
    response = await auth_routes.password_reset(
        PasswordResetConfirm(token="tok", password="NewPassw0rd!234"),
        _request("/auth/password/reset"),
        None,
    )
    _assert_refusal(response)
    assert users.passwords_set == []
    assert repo.used_tokens == []
    (audit,) = _refusal_audits(audits)
    assert audit["metadata"]["authentication_method"] == "password_reset"


@pytest.mark.anyio("asyncio")
async def test_reset_token_redemption_allowed_when_policy_allows(env):
    repo, users, _, _, policy = env
    policy["allowed"] = True
    repo.reset_record = {
        "user_id": 42, "used": 0, "expires_at": datetime.utcnow() + timedelta(hours=1)
    }
    result = await auth_routes.password_reset(
        PasswordResetConfirm(token="tok", password="NewPassw0rd!234"),
        _request("/auth/password/reset"),
        None,
    )
    assert result.detail == "Password reset successful."
    assert users.passwords_set == [42]


@pytest.mark.anyio("asyncio")
async def test_reset_token_for_missing_user_is_rejected(env):
    repo, users, _, _, policy = env
    policy["allowed"] = True
    users.user["id"] = 999  # get_user_by_id(42) now returns None
    repo.reset_record = {
        "user_id": 42, "used": 0, "expires_at": datetime.utcnow() + timedelta(hours=1)
    }
    with pytest.raises(HTTPException) as exc:
        await auth_routes.password_reset(
            PasswordResetConfirm(token="tok", password="NewPassw0rd!234"),
            _request("/auth/password/reset"),
            None,
        )
    assert exc.value.status_code == 400
    assert exc.value.detail == "Invalid or expired token"
    assert users.passwords_set == []
    assert repo.used_tokens == []
