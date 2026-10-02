"""Regression tests: registration and password reset do not reveal accounts."""

from __future__ import annotations

import json
from contextlib import asynccontextmanager

import pytest
from fastapi import BackgroundTasks
from starlette.requests import Request

from app.api.routes import auth as auth_routes
from app.schemas.auth import PasswordResetRequest, RegistrationRequest


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/auth/register",
            "headers": [(b"user-agent", b"pytest")],
            "client": ("10.0.0.1", 1234),
            "server": ("testserver", 80),
            "scheme": "http",
        }
    )


def _payload(email: str = "sam@example.com") -> RegistrationRequest:
    return RegistrationRequest(email=email, password="a-long-password-123", accept_policies=True)


@pytest.fixture
def signup(monkeypatch):
    state = {"existing": None, "created": [], "user_count": 5}

    async def count_users():
        return state["user_count"]

    async def list_staff_by_email(email):
        return []

    async def get_company_by_email_domain(domain):
        return {"id": 3}

    async def get_user_by_email(email):
        return state["existing"]

    async def create_user(**kwargs):
        state["created"].append(kwargs)
        return {"id": 99, **kwargs}

    async def noop(*args, **kwargs):
        return None

    monkeypatch.setattr(auth_routes.user_repo, "count_users", count_users)
    monkeypatch.setattr(auth_routes.staff_repo, "list_staff_by_email", list_staff_by_email)
    monkeypatch.setattr(auth_routes.company_repo, "get_company_by_email_domain", get_company_by_email_domain)
    monkeypatch.setattr(auth_routes.user_repo, "get_user_by_email", get_user_by_email)
    monkeypatch.setattr(auth_routes.user_repo, "create_user", create_user)
    monkeypatch.setattr(auth_routes.user_repo, "update_user", noop)
    monkeypatch.setattr(auth_routes.user_company_repo, "assign_user_to_company", noop)
    monkeypatch.setattr(auth_routes.staff_access_service, "apply_pending_access_for_user", noop)
    monkeypatch.setattr(auth_routes.auth_repo, "create_account_verification_token", noop)
    return state


@pytest.mark.anyio("asyncio")
async def test_register_existing_email_matches_new_signup_response(signup):
    new_tasks = BackgroundTasks()
    new_response = await auth_routes.register(_payload(), _request(), new_tasks)

    signup["existing"] = {"id": 7, "email": "sam@example.com", "force_password_change": 0}
    existing_tasks = BackgroundTasks()
    existing_response = await auth_routes.register(_payload(), _request(), existing_tasks)

    assert existing_response.status_code == new_response.status_code == 202
    assert json.loads(existing_response.body) == json.loads(new_response.body)
    assert len(signup["created"]) == 1
    assert [t.func for t in existing_tasks.tasks] == [auth_routes._notify_existing_account_registration]


@pytest.mark.anyio("asyncio")
async def test_register_first_user_is_serialised_and_rechecked(monkeypatch, signup):
    signup["user_count"] = 0
    lock_calls = []

    @asynccontextmanager
    async def fake_lock():
        lock_calls.append("locked")
        # Another request created the first user while this one waited.
        signup["user_count"] = 1
        yield

    async def fail_first_user(*args, **kwargs):  # pragma: no cover - must not be reached
        raise AssertionError("only one request may bootstrap the super admin")

    monkeypatch.setattr(auth_routes.auth_repo, "first_user_registration_lock", fake_lock)
    monkeypatch.setattr(auth_routes, "_register_first_user", fail_first_user)

    response = await auth_routes.register(_payload(), _request(), BackgroundTasks())

    assert lock_calls == ["locked"]
    assert response.status_code == 202
    assert signup["created"][0]["is_super_admin"] is False


@pytest.mark.anyio("asyncio")
async def test_password_forgot_defers_lookup_to_background(monkeypatch):
    async def fail_lookup(email):  # pragma: no cover - must not run on the request path
        raise AssertionError("lookup must happen after the response")

    monkeypatch.setattr(auth_routes.user_repo, "get_user_by_email", fail_lookup)
    tasks = BackgroundTasks()

    response = await auth_routes.password_forgot(PasswordResetRequest(email="x@example.com"), tasks)

    assert response.status_code == 200
    assert json.loads(response.body)["detail"] == auth_routes.PASSWORD_RESET_REQUESTED_DETAIL
    assert [t.func for t in tasks.tasks] == [auth_routes._process_password_reset_request]
