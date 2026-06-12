from datetime import datetime, timedelta
import asyncio

import pytest
from fastapi import HTTPException, status
from fastapi.responses import RedirectResponse

from app.api.routes import auth as auth_routes


def test_verify_email_accepts_new_token_and_marks_user_verified(monkeypatch):
    async def run_test():
        token = " fresh-token \n"
        stored_token = "fresh-token"
        updates: dict[str, object] = {}
        used_tokens: list[str] = []

        async def fake_get_account_verification_token(received_token: str):
            assert received_token == stored_token
            return {
                "token": stored_token,
                "user_id": 42,
                "used": 0,
                "expires_at": datetime.utcnow() + timedelta(hours=1),
            }

        async def fake_get_user_by_id(user_id: int):
            assert user_id == 42
            return {"id": user_id, "email_verified_at": None}

        async def fake_update_user(user_id: int, **kwargs):
            updates.update(kwargs)
            return {"id": user_id, **kwargs}

        async def fake_mark_account_verification_token_used(received_token: str):
            used_tokens.append(received_token)

        monkeypatch.setattr(auth_routes.auth_repo, "get_account_verification_token", fake_get_account_verification_token)
        monkeypatch.setattr(auth_routes.user_repo, "get_user_by_id", fake_get_user_by_id)
        monkeypatch.setattr(auth_routes.user_repo, "update_user", fake_update_user)
        monkeypatch.setattr(auth_routes.auth_repo, "mark_account_verification_token_used", fake_mark_account_verification_token_used)

        response = await auth_routes.verify_email(token)

        assert isinstance(response, RedirectResponse)
        assert response.status_code == status.HTTP_303_SEE_OTHER
        assert response.headers["location"] == "/login?verified=1"
        assert updates["is_active"] == 1
        assert isinstance(updates["email_verified_at"], datetime)
        assert used_tokens == [stored_token]

    asyncio.run(run_test())


def test_verify_email_is_idempotent_after_scanner_uses_link(monkeypatch):
    async def run_test():
        async def fake_get_account_verification_token(token: str):
            return {
                "token": token,
                "user_id": 42,
                "used": 1,
                "expires_at": datetime.utcnow() + timedelta(hours=1),
            }

        async def fake_get_user_by_id(user_id: int):
            return {"id": user_id, "email_verified_at": datetime.utcnow(), "is_active": 1}

        async def fail_update_user(*args, **kwargs):  # pragma: no cover - should not be called
            raise AssertionError("verified users should not be updated again")

        async def fail_mark_used(*args, **kwargs):  # pragma: no cover - should not be called
            raise AssertionError("verified users should not have token marked again")

        monkeypatch.setattr(auth_routes.auth_repo, "get_account_verification_token", fake_get_account_verification_token)
        monkeypatch.setattr(auth_routes.user_repo, "get_user_by_id", fake_get_user_by_id)
        monkeypatch.setattr(auth_routes.user_repo, "update_user", fail_update_user)
        monkeypatch.setattr(auth_routes.auth_repo, "mark_account_verification_token_used", fail_mark_used)

        response = await auth_routes.verify_email("fresh-token")

        assert isinstance(response, RedirectResponse)
        assert response.status_code == status.HTTP_303_SEE_OTHER
        assert response.headers["location"] == "/login?verified=1"

    asyncio.run(run_test())


def test_verify_email_rejects_used_token_for_unverified_user(monkeypatch):
    async def run_test():
        async def fake_get_account_verification_token(token: str):
            return {
                "token": token,
                "user_id": 42,
                "used": 1,
                "expires_at": datetime.utcnow() + timedelta(hours=1),
            }

        async def fake_get_user_by_id(user_id: int):
            return {"id": user_id, "email_verified_at": None, "is_active": 0}

        monkeypatch.setattr(auth_routes.auth_repo, "get_account_verification_token", fake_get_account_verification_token)
        monkeypatch.setattr(auth_routes.user_repo, "get_user_by_id", fake_get_user_by_id)

        with pytest.raises(HTTPException) as exc_info:
            await auth_routes.verify_email("fresh-token")

        assert exc_info.value.status_code == status.HTTP_400_BAD_REQUEST
        assert exc_info.value.detail == "Invalid or expired verification link"

    asyncio.run(run_test())
