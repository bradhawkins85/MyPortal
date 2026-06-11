from datetime import datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app import main
from app.features.companies import handlers as company_handlers
from app.security.session import SessionData


async def _dummy_receive() -> dict[str, object]:
    return {"type": "http.request", "body": b"", "more_body": False}


def _make_request(path: str = "/admin/companies") -> Request:
    scope = {"type": "http", "method": "GET", "path": path, "headers": []}
    return Request(scope, _dummy_receive)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio("asyncio")
async def test_base_context_hides_company_admin_when_menu_permission_is_no_access(monkeypatch):
    request = _make_request()
    session = SessionData(
        id=1,
        user_id=5,
        session_token="token",
        csrf_token="csrf",
        created_at=datetime.utcnow(),
        expires_at=datetime.utcnow() + timedelta(hours=1),
        last_seen_at=datetime.utcnow(),
        ip_address="127.0.0.1",
        user_agent="pytest",
        active_company_id=7,
    )

    async def fake_load_session(req):
        req.state.session = session
        req.state.active_company_id = session.active_company_id
        return session

    membership = {
        "company_id": 7,
        "is_admin": True,
        "staff_permission": 0,
        "menu_permissions": {"menu.admin.company": "none"},
    }

    monkeypatch.setattr(main.session_manager, "load_session", fake_load_session)
    monkeypatch.setattr(
        main.company_access,
        "list_accessible_companies",
        AsyncMock(return_value=[{"company_id": 7, "company_name": "Example"}]),
    )
    monkeypatch.setattr(main.user_company_repo, "get_user_company", AsyncMock(return_value=membership))
    monkeypatch.setattr(
        main.cart_repo,
        "summarise_cart",
        AsyncMock(return_value={"item_count": 0, "total_quantity": 0, "subtotal": Decimal("0")}),
    )
    monkeypatch.setattr(main.notifications_repo, "count_notifications", AsyncMock(return_value=0))

    user = {"id": 5, "email": "user@example.com", "is_super_admin": False}
    context = await main._build_base_context(request, user)

    assert context["menu_access"]["menu.admin.company"] == "none"
    assert context["can_access_admin_company"] is False
    assert context["is_company_admin"] is False


@pytest.mark.anyio("asyncio")
async def test_company_management_scope_denies_explicit_no_access_company_admin(monkeypatch):
    request = _make_request()
    user = {"id": 5, "email": "user@example.com", "is_super_admin": False}
    membership = {
        "company_id": 7,
        "is_admin": True,
        "staff_permission": 0,
        "menu_permissions": {"menu.admin.company": "none"},
    }

    from app.repositories import companies as company_repo
    from app.repositories import user_companies as user_company_repo

    monkeypatch.setattr(user_company_repo, "list_companies_for_user", AsyncMock(return_value=[membership]))
    monkeypatch.setattr(company_repo, "get_company_by_id", AsyncMock(return_value={"id": 7, "name": "Example"}))

    with pytest.raises(HTTPException) as exc_info:
        await company_handlers._get_company_management_scope(request, user)

    assert exc_info.value.status_code == 403
