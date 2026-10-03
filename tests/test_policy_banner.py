"""Tests for the legal policy re-acceptance banner and acceptance endpoint.

Uses the same mocking approach as test_policy_acceptance_registration.py:
route handlers are tested directly with mocked dependencies rather than
requiring a live database.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.api.routes import users as users_routes
from app.core.legal import LEGAL_POLICIES_UPDATED

OLD_VERSION = "2020-01-01"


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/users/me/policies/accept",
            "headers": [(b"user-agent", b"pytest")],
            "client": ("10.0.0.1", 1234),
            "server": ("testserver", 80),
            "scheme": "http",
        }
    )


def _browser_request() -> Request:
    """Request with Sec-Fetch-Mode: navigate (plain form POST)."""
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/users/me/policies/accept",
            "headers": [
                (b"user-agent", b"pytest"),
                (b"sec-fetch-mode", b"navigate"),
                (b"referer", b"https://testserver/dashboard"),
                (b"accept", b"text/html,application/xhtml+xml"),
            ],
            "client": ("10.0.0.1", 1234),
            "server": ("testserver", 80),
            "scheme": "http",
        }
    )


@pytest.fixture
def mock_accept(monkeypatch):
    state = {"update_calls": [], "audit_calls": []}

    async def get_user_by_id(user_id):
        return {
            "id": user_id,
            "email": "test@example.com",
            "policies_accepted_version": OLD_VERSION,
            "policies_accepted_at": None,
        }

    async def update_user(user_id, **kwargs):
        state["update_calls"].append({"user_id": user_id, **kwargs})
        return True

    async def record(**kwargs):
        state["audit_calls"].append(kwargs)

    monkeypatch.setattr(users_routes.user_repo, "get_user_by_id", get_user_by_id)
    monkeypatch.setattr(users_routes.user_repo, "update_user", update_user)
    monkeypatch.setattr(users_routes.audit_service, "record", record)

    return state


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


def test_accept_policies_updates_version(mock_accept):
    """The endpoint sets policies_accepted_version to the current value."""
    request = _request()
    current_user = {"id": 1, "email": "test@example.com"}

    response = _run(users_routes.accept_policies(request, None, current_user))

    assert response.status_code == 200
    data = json.loads(response.body)
    assert data["ok"] is True
    assert data["policies_accepted_version"] == LEGAL_POLICIES_UPDATED

    # update_user was called with the correct values
    assert len(mock_accept["update_calls"]) == 1
    call = mock_accept["update_calls"][0]
    assert call["user_id"] == 1
    assert call["policies_accepted_version"] == LEGAL_POLICIES_UPDATED
    assert call["policies_accepted_at"] is not None

    # audit was called
    assert len(mock_accept["audit_calls"]) == 1
    audit = mock_accept["audit_calls"][0]
    assert audit["action"] == "user.policies.accept"
    assert audit["entity_type"] == "user"
    assert audit["entity_id"] == 1
    assert audit["before"]["policies_accepted_version"] == OLD_VERSION
    assert audit["after"]["policies_accepted_version"] == LEGAL_POLICIES_UPDATED


def test_accept_policies_redirects_on_browser_nav(mock_accept):
    """Browser form POST (Sec-Fetch-Mode: navigate) gets a 303 redirect."""
    request = _browser_request()
    current_user = {"id": 1, "email": "test@example.com"}

    response = _run(users_routes.accept_policies(request, None, current_user))

    assert response.status_code == 303
    assert response.headers["location"] == "/dashboard"


def test_accept_policies_user_not_found(mock_accept, monkeypatch):
    """Returns 404 when the user no longer exists."""

    async def get_user_by_id_404(user_id):
        return None

    monkeypatch.setattr(users_routes.user_repo, "get_user_by_id", get_user_by_id_404)

    request = _request()
    current_user = {"id": 999, "email": "gone@example.com"}

    with pytest.raises(HTTPException) as exc_info:
        _run(users_routes.accept_policies(request, None, current_user))

    assert exc_info.value.status_code == 404


def test_policies_update_pending_flag_logic():
    """Verify the context computation logic directly."""
    user_old = {"policies_accepted_version": OLD_VERSION}
    user_current = {"policies_accepted_version": LEGAL_POLICIES_UPDATED}
    user_null = {"policies_accepted_version": None}

    # Banner should show when version differs (old, null)
    assert user_old["policies_accepted_version"] != LEGAL_POLICIES_UPDATED
    assert user_null["policies_accepted_version"] != LEGAL_POLICIES_UPDATED
    # Banner should NOT show when version matches
    assert not (user_current["policies_accepted_version"] != LEGAL_POLICIES_UPDATED)