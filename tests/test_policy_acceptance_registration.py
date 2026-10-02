"""Tests for the required policy-agreement checkbox on registration.

Covers:
- Registration is rejected (400) when ``accept_policies`` is false or missing.
- Registration succeeds (202/201) when ``accept_policies`` is true.
- The accepted policy version and timestamp are stored on the user.
- An audit entry is written on successful acceptance.
"""

from __future__ import annotations

import pytest
from fastapi import BackgroundTasks, HTTPException
from starlette.requests import Request

from app.api.routes import auth as auth_routes
from app.core.legal import LEGAL_POLICIES_UPDATED
from app.schemas.auth import RegistrationRequest


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


def _payload(email: str = "sam@example.com", accept_policies: bool = True) -> RegistrationRequest:
    return RegistrationRequest(email=email, password="a-long-password-123", accept_policies=accept_policies)


@pytest.fixture
def mock_registration(monkeypatch):
    state = {"existing": None, "created": [], "user_count": 5, "audit_calls": [], "update_calls": []}

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

    async def update_user(user_id, **kwargs):
        state["update_calls"].append({"user_id": user_id, **kwargs})
        return {"id": user_id, **kwargs}

    async def noop(*args, **kwargs):
        return None

    async def audit_record(**kwargs):
        state["audit_calls"].append(kwargs)

    monkeypatch.setattr(auth_routes.user_repo, "count_users", count_users)
    monkeypatch.setattr(auth_routes.staff_repo, "list_staff_by_email", list_staff_by_email)
    monkeypatch.setattr(auth_routes.company_repo, "get_company_by_email_domain", get_company_by_email_domain)
    monkeypatch.setattr(auth_routes.user_repo, "get_user_by_email", get_user_by_email)
    monkeypatch.setattr(auth_routes.user_repo, "create_user", create_user)
    monkeypatch.setattr(auth_routes.user_repo, "update_user", update_user)
    monkeypatch.setattr(auth_routes.user_company_repo, "assign_user_to_company", noop)
    monkeypatch.setattr(auth_routes.staff_access_service, "apply_pending_access_for_user", noop)
    monkeypatch.setattr(auth_routes.auth_repo, "create_account_verification_token", noop)
    monkeypatch.setattr(auth_routes.audit_service, "record", audit_record)
    return state


# ---------------------------------------------------------------------------
# Rejected registration (accept_policies=False or missing)
# ---------------------------------------------------------------------------


@pytest.mark.anyio("asyncio")
async def test_register_rejected_when_accept_policies_false(mock_registration):
    """A 400 is raised when the user does not tick the policy checkbox."""
    payload = _payload(accept_policies=False)
    with pytest.raises(HTTPException) as exc_info:
        await auth_routes.register(payload, _request(), BackgroundTasks())
    assert exc_info.value.status_code == 400
    assert "agree" in exc_info.value.detail.lower()
    assert mock_registration["created"] == []


@pytest.mark.anyio("asyncio")
async def test_register_rejected_when_accept_policies_missing(mock_registration):
    """A 400 is raised when the field is absent (defaults to False)."""
    payload = RegistrationRequest(email="sam@example.com", password="a-long-password-123")
    assert payload.accept_policies is False
    with pytest.raises(HTTPException) as exc_info:
        await auth_routes.register(payload, _request(), BackgroundTasks())
    assert exc_info.value.status_code == 400
    assert mock_registration["created"] == []


# ---------------------------------------------------------------------------
# Successful registration (accept_policies=True)
# ---------------------------------------------------------------------------


@pytest.mark.anyio("asyncio")
async def test_register_stores_policy_acceptance(mock_registration):
    """On success the version and timestamp are persisted and an audit row is written."""
    response = await auth_routes.register(_payload(), _request(), BackgroundTasks())
    assert response.status_code == 202

    assert len(mock_registration["created"]) == 1

    policy_updates = [
        c for c in mock_registration["update_calls"]
        if "policies_accepted_version" in c
    ]
    assert len(policy_updates) == 1
    assert policy_updates[0]["policies_accepted_version"] == LEGAL_POLICIES_UPDATED
    assert policy_updates[0]["user_id"] == 99
    assert policy_updates[0]["policies_accepted_at"] is not None

    assert len(mock_registration["audit_calls"]) == 1
    audit = mock_registration["audit_calls"][0]
    assert audit["action"] == "auth.register.policy_accepted"
    assert audit["metadata"]["policies_version"] == LEGAL_POLICIES_UPDATED
    assert audit["user_id"] == 99


@pytest.mark.anyio("asyncio")
async def test_register_existing_email_still_requires_policies(mock_registration):
    """Even when the email already has an account the policy check runs first."""
    mock_registration["existing"] = {"id": 7, "email": "sam@example.com", "force_password_change": 0}
    payload = _payload(accept_policies=False)
    with pytest.raises(HTTPException) as exc_info:
        await auth_routes.register(payload, _request(), BackgroundTasks())
    assert exc_info.value.status_code == 400
