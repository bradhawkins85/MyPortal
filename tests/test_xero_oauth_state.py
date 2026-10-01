"""Xero OAuth state must be time-limited and bound to the initiating admin."""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from app.api.routes import xero as xero_routes
from app.security.session import SessionData


def _session(user_id: int, session_id: int = 1) -> SessionData:
    now = datetime.utcnow()
    return SessionData(
        id=session_id,
        user_id=user_id,
        session_token="t",
        csrf_token="c",
        created_at=now,
        expires_at=now,
        last_seen_at=now,
        ip_address=None,
        user_agent=None,
    )


def _state(user_id: int, session_id: int = 1) -> str:
    return xero_routes._get_state_serializer().dumps({"user_id": user_id, "session_id": session_id})


def test_list_tenants_requires_super_admin():
    route = next(r for r in xero_routes.router.routes if getattr(r, "name", "") == "xero_list_tenants")
    calls = {dep.call for dep in route.dependant.dependencies}
    assert xero_routes.require_super_admin in calls


def test_state_serializer_enforces_max_age():
    from itsdangerous import SignatureExpired

    state = _state(1)
    with patch("itsdangerous.timed.time.time", return_value=4_000_000_000):
        with pytest.raises(SignatureExpired):
            xero_routes._get_state_serializer().loads(state, max_age=xero_routes._STATE_MAX_AGE_SECONDS)


@pytest.mark.anyio
async def test_callback_rejects_state_for_other_user():
    request = SimpleNamespace(query_params={})
    exchange = AsyncMock()
    with patch.object(xero_routes.session_manager, "load_session", AsyncMock(return_value=_session(2))), patch.object(
        xero_routes.user_repo, "get_user_by_id", AsyncMock(return_value={"id": 2, "is_super_admin": True})
    ), patch.object(xero_routes.modules_service, "get_xero_credentials", exchange):
        response = await xero_routes.xero_callback(request, code="abc", state=_state(1))  # type: ignore[arg-type]
    assert response.status_code == 303
    exchange.assert_not_called()


@pytest.mark.anyio
async def test_callback_requires_super_admin_session():
    request = SimpleNamespace(query_params={})
    with patch.object(xero_routes.session_manager, "load_session", AsyncMock(return_value=_session(1))), patch.object(
        xero_routes.user_repo, "get_user_by_id", AsyncMock(return_value={"id": 1, "is_super_admin": False})
    ):
        with pytest.raises(HTTPException) as exc_info:
            await xero_routes.xero_callback(request, code="abc", state=_state(1))  # type: ignore[arg-type]
    assert exc_info.value.status_code == 403
