from __future__ import annotations

import json
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.api.routes import auth as auth_routes
from app.schemas.auth import (
    PasskeyBeginRegistrationRequest,
    PasskeyCredentialRequest,
    PasskeyDeleteRequest,
    PasskeyFinishRegistrationRequest,
)
from app.security.session import SessionData
from app.services import passkeys as passkeys_service


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _request(path: str, *, cookie: str | None = None) -> Request:
    headers = [(b"accept", b"application/json"), (b"user-agent", b"pytest")]
    if cookie:
        headers.append((b"cookie", cookie.encode("utf-8")))
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": path,
            "headers": headers,
            "client": ("127.0.0.1", 12345),
            "server": ("testserver", 80),
            "scheme": "http",
        }
    )


def _session() -> SessionData:
    now = datetime.utcnow()
    return SessionData(
        id=12,
        user_id=42,
        session_token="session-token",
        csrf_token="csrf-token",
        created_at=now,
        expires_at=now + timedelta(hours=1),
        last_seen_at=now,
        ip_address="127.0.0.1",
        user_agent="pytest",
    )


@pytest.mark.anyio
async def test_finish_passkey_authentication_creates_session(monkeypatch):
    recorded = {}

    async def fake_get_passkey_challenge(challenge_id):
        return {"challenge_id": challenge_id, "challenge": "expected-challenge"}

    async def fake_consume_passkey_challenge(**kwargs):
        recorded["consumed"] = kwargs
        return True

    async def fake_get_passkey_by_credential_id(credential_id):
        return {
            "id": 7,
            "user_id": 42,
            "credential_id": credential_id,
            "public_key": passkeys_service.bytes_to_base64url(b"stored-public-key"),
            "sign_count": 3,
            "credential_backed_up": 0,
        }

    async def fake_get_user_by_id(user_id):
        return {
            "id": user_id,
            "email": "user@example.com",
            "password_hash": "hash",
            "company_id": 8,
            "is_active": 1,
            "is_super_admin": 0,
        }

    async def fake_get_totp_authenticators(user_id):
        return [{"id": 1, "name": "Authenticator", "secret": "unused"}]

    async def fake_record_login(user_id, logged_in_at):
        recorded["record_login"] = (user_id, logged_in_at)
        return {
            "id": user_id,
            "email": "user@example.com",
            "password_hash": "hash",
            "company_id": 8,
            "is_active": 1,
            "is_super_admin": 0,
            "last_login_at": logged_in_at,
        }

    async def fake_update_passkey_after_authentication(**kwargs):
        recorded["updated_passkey"] = kwargs

    async def fake_create_session(user_id, request, *, active_company_id=None):
        return SessionData(
            id=90,
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

    monkeypatch.setattr(auth_routes.auth_repo, "get_passkey_challenge", fake_get_passkey_challenge)
    monkeypatch.setattr(auth_routes.auth_repo, "consume_passkey_challenge", fake_consume_passkey_challenge)
    monkeypatch.setattr(auth_routes.auth_repo, "get_passkey_by_credential_id", fake_get_passkey_by_credential_id)
    monkeypatch.setattr(auth_routes.auth_repo, "get_totp_authenticators", fake_get_totp_authenticators)
    monkeypatch.setattr(auth_routes.auth_repo, "update_passkey_after_authentication", fake_update_passkey_after_authentication)
    monkeypatch.setattr(auth_routes.user_repo, "get_user_by_id", fake_get_user_by_id)
    monkeypatch.setattr(auth_routes.user_repo, "record_login", fake_record_login)
    monkeypatch.setattr(auth_routes.session_manager, "create_session", fake_create_session)
    monkeypatch.setattr(auth_routes.session_manager, "apply_session_cookies", lambda response, session, request: None)
    monkeypatch.setattr(auth_routes, "_determine_active_company_id", lambda user: _async_return(user.get("company_id")))
    monkeypatch.setattr(
        auth_routes.passkeys_service,
        "verify_authentication",
        lambda **kwargs: SimpleNamespace(
            new_sign_count=5,
            credential_device_type=SimpleNamespace(value="multi_device"),
            credential_backed_up=True,
        ),
    )
    monkeypatch.setattr(auth_routes.audit_service, "log_action", _async_noop)

    request = _request(
        "/auth/passkeys/authenticate/verify",
        cookie="myportal_session_passkey_login=browser-binding",
    )
    response = await auth_routes.finish_passkey_authentication(
        PasskeyCredentialRequest(challenge_id="challenge-1", credential={"id": "credential-1"}),
        request,
        None,
    )

    payload = json.loads(response.body)
    assert response.status_code == 200
    assert payload["user"]["id"] == 42
    assert payload["requires_totp_enrollment"] is False
    assert recorded["updated_passkey"]["sign_count"] == 5
    assert recorded["record_login"][0] == 42


@pytest.mark.anyio
async def test_finish_passkey_authentication_rejects_ineligible_user(monkeypatch):
    async def fake_get_passkey_challenge(challenge_id):
        return {"challenge_id": challenge_id, "challenge": "expected-challenge"}

    async def fake_consume_passkey_challenge(**kwargs):
        return True

    async def fake_get_passkey_by_credential_id(credential_id):
        return {
            "id": 7,
            "user_id": 42,
            "credential_id": credential_id,
            "public_key": passkeys_service.bytes_to_base64url(b"stored-public-key"),
            "sign_count": 0,
        }

    async def fake_get_user_by_id(user_id):
        return {"id": user_id, "email": "user@example.com", "is_active": 0}

    monkeypatch.setattr(auth_routes.auth_repo, "get_passkey_challenge", fake_get_passkey_challenge)
    monkeypatch.setattr(auth_routes.auth_repo, "consume_passkey_challenge", fake_consume_passkey_challenge)
    monkeypatch.setattr(auth_routes.auth_repo, "get_passkey_by_credential_id", fake_get_passkey_by_credential_id)
    monkeypatch.setattr(auth_routes.user_repo, "get_user_by_id", fake_get_user_by_id)
    monkeypatch.setattr(auth_routes.audit_service, "log_action", _async_noop)

    request = _request(
        "/auth/passkeys/authenticate/verify",
        cookie="myportal_session_passkey_login=browser-binding",
    )
    with pytest.raises(HTTPException) as exc_info:
        await auth_routes.finish_passkey_authentication(
            PasskeyCredentialRequest(challenge_id="challenge-1", credential={"id": "credential-1"}),
            request,
            None,
        )

    assert exc_info.value.status_code == 403
    assert exc_info.value.detail == "Passkey sign-in is not available for this account."


@pytest.mark.anyio
async def test_finish_passkey_registration_rejects_duplicate_credential(monkeypatch):
    async def fake_get_passkey_challenge(challenge_id):
        return {"challenge_id": challenge_id, "challenge": "expected-challenge"}

    async def fake_consume_passkey_challenge(**kwargs):
        return True

    async def fake_get_passkey_by_credential_id(credential_id):
        return {"id": 91, "user_id": 42, "credential_id": credential_id, "display_name": "Existing key"}

    monkeypatch.setattr(auth_routes.auth_repo, "get_passkey_challenge", fake_get_passkey_challenge)
    monkeypatch.setattr(auth_routes.auth_repo, "consume_passkey_challenge", fake_consume_passkey_challenge)
    monkeypatch.setattr(auth_routes.auth_repo, "get_passkey_by_credential_id", fake_get_passkey_by_credential_id)
    monkeypatch.setattr(
        auth_routes.passkeys_service,
        "verify_registration",
        lambda **kwargs: SimpleNamespace(
            credential_public_key=b"public-key",
            sign_count=0,
            aaguid="aaguid",
            credential_device_type=SimpleNamespace(value="single_device"),
            credential_backed_up=False,
        ),
    )

    with pytest.raises(HTTPException) as exc_info:
        await auth_routes.finish_passkey_registration(
            PasskeyFinishRegistrationRequest(
                challenge_id="challenge-1",
                name="Work laptop",
                credential={"id": "credential-1", "response": {"transports": ["internal"]}},
            ),
            _request("/auth/passkeys/register/verify"),
            _session(),
            {"id": 42, "email": "user@example.com", "password_hash": "hash"},
            None,
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == "This passkey is already registered"


@pytest.mark.anyio
async def test_delete_passkey_requires_current_password(monkeypatch):
    deleted = {"called": False}

    async def fake_get_passkey_by_id(user_id, passkey_id):
        return {"id": passkey_id, "user_id": user_id, "credential_id": "credential-1", "display_name": "Key"}

    async def fake_delete_passkey(user_id, passkey_id):
        deleted["called"] = True

    monkeypatch.setattr(auth_routes.auth_repo, "get_passkey_by_id", fake_get_passkey_by_id)
    monkeypatch.setattr(auth_routes.auth_repo, "delete_passkey", fake_delete_passkey)
    monkeypatch.setattr(auth_routes, "verify_password", lambda provided, stored: False)

    with pytest.raises(HTTPException) as exc_info:
        await auth_routes.delete_passkey(
            5,
            PasskeyDeleteRequest(current_password="wrong-password"),
            _request("/auth/passkeys/5"),
            {"id": 42, "email": "user@example.com", "password_hash": "stored-hash"},
        )

    assert exc_info.value.status_code == 400
    assert exc_info.value.detail == "Current password is incorrect"
    assert deleted["called"] is False


def test_registration_options_derive_rp_from_portal_url():
    settings = auth_routes.get_settings().__class__(
        SESSION_SECRET="secret",
        TOTP_ENCRYPTION_KEY="totp-secret",
        PORTAL_URL="https://portal.example.com",
        PASSKEY_ALLOWED_ORIGINS="",
    )

    assert passkeys_service.relying_party_id(settings) == "portal.example.com"
    assert passkeys_service.allowed_origins(settings) == ["https://portal.example.com"]


async def _async_noop(**kwargs):
    return None


async def _async_return(value):
    return value
