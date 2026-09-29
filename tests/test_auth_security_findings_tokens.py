"""Regression tests: reset/verification tokens are stored as digests, and
account deactivation retires outstanding signup verification links."""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from app.repositories import auth as auth_repo


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


@pytest.mark.anyio("asyncio")
@pytest.mark.parametrize(
    "create, table",
    [
        (auth_repo.create_password_reset_token, "password_tokens"),
        (auth_repo.create_account_verification_token, "account_verification_tokens"),
    ],
)
async def test_tokens_are_stored_as_sha256_digests(monkeypatch, create, table):
    calls: list[tuple[str, tuple]] = []

    async def fake_execute(sql, params=None):
        calls.append((sql, params))

    monkeypatch.setattr(auth_repo.db, "execute", fake_execute)

    await create(user_id=3, token="raw-secret", expires_at=datetime.utcnow() + timedelta(hours=1))

    sql, params = calls[0]
    assert table in sql
    assert "token_hashed" in sql
    assert params[0] == _digest("raw-secret")
    assert "raw-secret" not in params


@pytest.mark.anyio("asyncio")
@pytest.mark.parametrize(
    "lookup",
    [auth_repo.get_password_reset_token, auth_repo.get_account_verification_token],
)
async def test_token_lookup_matches_digest_and_only_legacy_plaintext(monkeypatch, lookup):
    captured: dict[str, object] = {}

    async def fake_fetch_one(sql, params=None):
        captured["sql"] = sql
        captured["params"] = params
        return None

    monkeypatch.setattr(auth_repo.db, "fetch_one", fake_fetch_one)

    await lookup("presented")

    sql = " ".join(str(captured["sql"]).split())
    assert "(token = %s AND token_hashed = 1) OR (token = %s AND token_hashed = 0)" in sql
    assert captured["params"] == (_digest("presented"), "presented")


@pytest.mark.anyio("asyncio")
async def test_mark_used_targets_digest(monkeypatch):
    calls: list[tuple[str, tuple]] = []

    async def fake_execute(sql, params=None):
        calls.append((sql, params))

    monkeypatch.setattr(auth_repo.db, "execute", fake_execute)

    await auth_repo.mark_password_reset_token_used("abc")
    await auth_repo.mark_account_verification_token_used("abc")

    for sql, params in calls:
        assert "token_hashed = 1" in sql
        assert params == (_digest("abc"), "abc")


@pytest.mark.anyio("asyncio")
async def test_admin_deactivate_invalidates_verification_tokens(monkeypatch):
    from app import main as main_module

    calls: dict[str, object] = {}

    async def fake_require_super_admin_page(request):
        return {"id": 1}, None

    async def fake_get_user_by_id(user_id):
        return {"id": user_id, "email": "u@example.com", "is_active": 1, "is_super_admin": 0}

    async def fake_update_user(user_id, **kwargs):
        calls["update"] = (user_id, kwargs)
        return {"id": user_id, **kwargs}

    async def fake_deactivate_sessions(user_id, **kwargs):
        calls["sessions"] = user_id

    async def fake_invalidate(user_id):
        calls["verification_tokens"] = user_id

    async def fake_record(**kwargs):
        return None

    monkeypatch.setattr(main_module, "_require_super_admin_page", fake_require_super_admin_page)
    monkeypatch.setattr(main_module.user_repo, "get_user_by_id", fake_get_user_by_id)
    monkeypatch.setattr(main_module.user_repo, "update_user", fake_update_user)
    monkeypatch.setattr(main_module.auth_repo, "deactivate_sessions_for_user", fake_deactivate_sessions)
    monkeypatch.setattr(
        main_module.auth_repo, "invalidate_account_verification_tokens_for_user", fake_invalidate
    )
    monkeypatch.setattr("app.services.audit.record", fake_record)
    monkeypatch.setattr(main_module, "flash_redirect", lambda *args, **kwargs: SimpleNamespace(args=args))

    await main_module.admin_users_action(SimpleNamespace(), 22, "deactivate")

    assert calls["update"] == (22, {"is_active": 0})
    assert calls["sessions"] == 22
    assert calls["verification_tokens"] == 22


@pytest.mark.anyio("asyncio")
async def test_verify_email_never_reactivates_verified_account(monkeypatch):
    from fastapi.responses import RedirectResponse

    from app.api.routes import auth as auth_routes

    async def fake_get_token(token):
        return {"user_id": 5, "used": 0, "expires_at": datetime.utcnow() + timedelta(hours=1)}

    async def fake_get_user_by_id(user_id):
        return {"id": user_id, "is_active": 0, "email_verified_at": datetime.utcnow()}

    async def fail_update_user(*args, **kwargs):  # pragma: no cover - must not be called
        raise AssertionError("a deactivated, verified account must not be re-enabled")

    monkeypatch.setattr(auth_routes.auth_repo, "get_account_verification_token", fake_get_token)
    monkeypatch.setattr(auth_routes.user_repo, "get_user_by_id", fake_get_user_by_id)
    monkeypatch.setattr(auth_routes.user_repo, "update_user", fail_update_user)

    response = await auth_routes.verify_email("token")
    assert isinstance(response, RedirectResponse)
