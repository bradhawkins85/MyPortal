"""Regression tests: an accepted TOTP code cannot be replayed."""

from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

import pyotp
import pytest
from fastapi import HTTPException

from app.api.routes import auth as auth_routes
from app.repositories import auth as auth_repo
from app.schemas.auth import TOTPVerifyRequest
from app.security.session import SessionData

SECRET = pyotp.random_base32()


def test_matching_step_rejects_steps_at_or_before_last_used():
    now = 1_700_000_000
    totp = pyotp.TOTP(SECRET)
    step = now // 30
    code = totp.generate_otp(step)

    assert auth_routes._matching_totp_step(SECRET, code, None, at=now) == step
    assert auth_routes._matching_totp_step(SECRET, code, step - 1, at=now) == step
    assert auth_routes._matching_totp_step(SECRET, code, step, at=now) is None
    assert auth_routes._matching_totp_step(SECRET, code, step + 1, at=now) is None
    # The previous step is still accepted for clock drift until it is used.
    previous = totp.generate_otp(step - 1)
    assert auth_routes._matching_totp_step(SECRET, previous, None, at=now) == step - 1
    assert auth_routes._matching_totp_step(SECRET, previous, step - 1, at=now) is None
    assert auth_routes._matching_totp_step(SECRET, "000000" if code != "000000" else "111111", None, at=now) is None


class _TotpRepo:
    """In-memory stand-in for the step-claim update."""

    def __init__(self) -> None:
        self.devices = [{"id": 1, "name": "Phone", "secret": SECRET, "last_used_step": None}]

    async def claim_totp_step(self, authenticator_id: int, step: int) -> bool:
        device = next(d for d in self.devices if d["id"] == authenticator_id)
        if device["last_used_step"] is not None and device["last_used_step"] >= step:
            return False
        device["last_used_step"] = step
        return True


@pytest.mark.anyio("asyncio")
async def test_login_totp_code_cannot_be_replayed(monkeypatch):
    repo = _TotpRepo()
    monkeypatch.setattr(auth_routes.auth_repo, "claim_totp_step", repo.claim_totp_step)
    code = pyotp.TOTP(SECRET).now()

    assert await auth_routes._verify_and_claim_totp(repo.devices, code) is True
    # A second use, whether from the stale device row or a fresh read, fails.
    assert await auth_routes._verify_and_claim_totp(repo.devices, code) is False
    stale = [dict(repo.devices[0], last_used_step=None)]
    assert await auth_routes._verify_and_claim_totp(stale, code) is False


@pytest.mark.anyio("asyncio")
async def test_claim_totp_step_is_conditional_update(monkeypatch):
    captured = {}

    async def fake_execute_rowcount(sql, params=None):
        captured["sql"] = " ".join(sql.split())
        captured["params"] = params
        return 0

    monkeypatch.setattr(auth_repo.db, "execute_rowcount", fake_execute_rowcount)

    assert await auth_repo.claim_totp_step(4, 100) is False
    assert "last_used_step IS NULL OR last_used_step < %s" in captured["sql"]
    assert captured["params"] == (100, 4, 100)


@pytest.mark.anyio("asyncio")
async def test_enrolment_records_the_step_it_used(monkeypatch):
    created = {}
    now = datetime.utcnow()
    session = SessionData(
        id=1, user_id=2, session_token="t", csrf_token="c", created_at=now,
        expires_at=now + timedelta(hours=1), last_seen_at=now, ip_address=None,
        user_agent=None, pending_totp_secret=SECRET,
    )

    async def fake_has_totp(user_id):
        return False

    async def fake_create(**kwargs):
        created.update(kwargs)
        return {"id": 9, "name": kwargs["name"]}

    async def fake_clear(session):
        return None

    async def fake_record(**kwargs):
        return None

    monkeypatch.setattr(auth_routes.auth_repo, "user_has_totp_authenticator", fake_has_totp)
    monkeypatch.setattr(auth_routes.auth_repo, "create_totp_authenticator", fake_create)
    monkeypatch.setattr(auth_routes.session_manager, "clear_pending_totp_secret", fake_clear)
    monkeypatch.setattr(auth_routes.audit_service, "record_create", fake_record)

    code = pyotp.TOTP(SECRET).now()
    await auth_routes.verify_totp(
        TOTPVerifyRequest(code=code, name="Phone"), SimpleNamespace(), session, {"id": 2}
    )

    assert isinstance(created["last_used_step"], int)
    assert pyotp.TOTP(SECRET).generate_otp(created["last_used_step"]) == code


@pytest.mark.anyio("asyncio")
async def test_enrolment_rejects_wrong_code(monkeypatch):
    now = datetime.utcnow()
    session = SessionData(
        id=1, user_id=2, session_token="t", csrf_token="c", created_at=now,
        expires_at=now + timedelta(hours=1), last_seen_at=now, ip_address=None,
        user_agent=None, pending_totp_secret=SECRET,
    )

    async def fake_has_totp(user_id):
        return False

    async def fake_record(**kwargs):
        return None

    monkeypatch.setattr(auth_routes.auth_repo, "user_has_totp_authenticator", fake_has_totp)
    monkeypatch.setattr(auth_routes.audit_service, "record", fake_record)
    good = pyotp.TOTP(SECRET).now()
    bad = "123456" if good != "123456" else "654321"

    with pytest.raises(HTTPException) as exc:
        await auth_routes.verify_totp(
            TOTPVerifyRequest(code=bad, name="Phone"), SimpleNamespace(), session, {"id": 2}
        )
    assert exc.value.status_code == 400
