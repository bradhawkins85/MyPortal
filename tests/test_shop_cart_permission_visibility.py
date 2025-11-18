"""Test that shop pages hide cart-related buttons for users without cart permissions."""
from datetime import datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock

import pytest
from starlette.requests import Request

from app import main
from app.security.session import SessionData


async def _dummy_receive() -> dict[str, object]:
    return {"type": "http.request", "body": b"", "more_body": False}


def _make_request(path: str = "/shop") -> Request:
    scope = {"type": "http", "method": "GET", "path": path, "headers": []}
    return Request(scope, _dummy_receive)


def _create_session(user_id: int, company_id: int) -> SessionData:
    return SessionData(
        id=1,
        user_id=user_id,
        session_token="token",
        csrf_token="csrf",
        created_at=datetime.utcnow(),
        expires_at=datetime.utcnow() + timedelta(hours=1),
        last_seen_at=datetime.utcnow(),
        ip_address="127.0.0.1",
        user_agent="pytest",
        active_company_id=company_id,
    )


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio("asyncio")
async def test_shop_page_without_cart_permission(monkeypatch):
    """Test that shop page doesn't show 'Cart access required' message."""
    request = _make_request("/shop")
    user = {"id": 9, "company_id": 5, "is_super_admin": False}
    company = {"id": 5, "name": "Test Company", "is_vip": 0}
    membership = {"can_access_shop": 1, "can_access_cart": 0, "staff_permission": 0}  # No cart permission
    session = _create_session(user["id"], 5)

    async def fake_load_session(req):
        req.state.session = session
        req.state.active_company_id = session.active_company_id
        return session

    monkeypatch.setattr(main.session_manager, "load_session", fake_load_session)
    monkeypatch.setattr(main, "_require_authenticated_user", AsyncMock(return_value=(user, None)))
    monkeypatch.setattr(main.user_company_repo, "get_user_company", AsyncMock(return_value=membership))
    monkeypatch.setattr(main.company_repo, "get_company_by_id", AsyncMock(return_value=company))
    monkeypatch.setattr(main.shop_repo, "list_categories", AsyncMock(return_value=[]))
    monkeypatch.setattr(main.shop_repo, "list_products", AsyncMock(return_value=[
        {
            "id": 1,
            "name": "Test Product",
            "sku": "TEST-001",
            "price": 100.00,
            "stock": 10,
            "is_package": False,
        }
    ]))
    monkeypatch.setattr(main.subscriptions_repo, "get_active_subscription_product_ids", AsyncMock(return_value=[]))
    monkeypatch.setattr(main.company_access, "list_accessible_companies", AsyncMock(return_value=[{"company_id": 5, "company_name": "Test Company"}]))
    monkeypatch.setattr(main.cart_repo, "summarise_cart", AsyncMock(return_value={"item_count": 0, "total_quantity": 0, "subtotal": Decimal("0")}))
    monkeypatch.setattr(main.notifications_repo, "count_notifications", AsyncMock(return_value=0))
    monkeypatch.setattr(main.membership_repo, "user_has_permission", AsyncMock(return_value=False))
    monkeypatch.setattr(main.modules_service, "list_modules", AsyncMock(return_value=[]))

    # Mock _render_template to capture context
    captured_context = {}

    async def mock_render_template(template_name, request, user, extra=None):
        context = await main._build_base_context(request, user, extra=extra)
        captured_context.update(context)
        return f"Rendered: {template_name}"

    monkeypatch.setattr(main, "_render_template", mock_render_template)

    response = await main.shop_page(request)

    # Verify cart_allowed is False in context
    assert "can_access_cart" in captured_context
    assert captured_context["can_access_cart"] is False
    
    # The template should not show "Cart access required" - this is a behavioral test
    # The actual verification happens in the template rendering, but we ensure
    # the permission flag is correctly set to False


@pytest.mark.anyio("asyncio")
async def test_shop_page_with_cart_permission(monkeypatch):
    """Test that shop page shows cart functionality for users with cart permission."""
    request = _make_request("/shop")
    user = {"id": 9, "company_id": 5, "is_super_admin": False}
    company = {"id": 5, "name": "Test Company", "is_vip": 0}
    membership = {"can_access_shop": 1, "can_access_cart": 1, "staff_permission": 0}  # Has cart permission
    session = _create_session(user["id"], 5)

    async def fake_load_session(req):
        req.state.session = session
        req.state.active_company_id = session.active_company_id
        return session

    monkeypatch.setattr(main.session_manager, "load_session", fake_load_session)
    monkeypatch.setattr(main, "_require_authenticated_user", AsyncMock(return_value=(user, None)))
    monkeypatch.setattr(main.user_company_repo, "get_user_company", AsyncMock(return_value=membership))
    monkeypatch.setattr(main.company_repo, "get_company_by_id", AsyncMock(return_value=company))
    monkeypatch.setattr(main.shop_repo, "list_categories", AsyncMock(return_value=[]))
    monkeypatch.setattr(main.shop_repo, "list_products", AsyncMock(return_value=[]))
    monkeypatch.setattr(main.subscriptions_repo, "get_active_subscription_product_ids", AsyncMock(return_value=[]))
    monkeypatch.setattr(main.company_access, "list_accessible_companies", AsyncMock(return_value=[{"company_id": 5, "company_name": "Test Company"}]))
    monkeypatch.setattr(main.cart_repo, "summarise_cart", AsyncMock(return_value={"item_count": 0, "total_quantity": 0, "subtotal": Decimal("0")}))
    monkeypatch.setattr(main.notifications_repo, "count_notifications", AsyncMock(return_value=0))
    monkeypatch.setattr(main.membership_repo, "user_has_permission", AsyncMock(return_value=False))
    monkeypatch.setattr(main.modules_service, "list_modules", AsyncMock(return_value=[]))

    # Mock _render_template to capture context
    captured_context = {}

    async def mock_render_template(template_name, request, user, extra=None):
        context = await main._build_base_context(request, user, extra=extra)
        captured_context.update(context)
        return f"Rendered: {template_name}"

    monkeypatch.setattr(main, "_render_template", mock_render_template)

    response = await main.shop_page(request)

    # Verify cart_allowed is True in context
    assert "can_access_cart" in captured_context
    assert captured_context["can_access_cart"] is True


@pytest.mark.anyio("asyncio")
async def test_shop_packages_page_without_cart_permission(monkeypatch):
    """Test that shop packages page doesn't show 'Cart access required' message."""
    request = _make_request("/shop/packages")
    user = {"id": 9, "company_id": 5, "is_super_admin": False}
    company = {"id": 5, "name": "Test Company", "is_vip": 0}
    membership = {"can_access_shop": 1, "can_access_cart": 0, "staff_permission": 0}  # No cart permission
    session = _create_session(user["id"], 5)

    async def fake_load_session(req):
        req.state.session = session
        req.state.active_company_id = session.active_company_id
        return session

    monkeypatch.setattr(main.session_manager, "load_session", fake_load_session)
    monkeypatch.setattr(main, "_require_authenticated_user", AsyncMock(return_value=(user, None)))
    monkeypatch.setattr(main.user_company_repo, "get_user_company", AsyncMock(return_value=membership))
    monkeypatch.setattr(main.company_repo, "get_company_by_id", AsyncMock(return_value=company))
    monkeypatch.setattr(main.shop_packages_service, "load_company_packages", AsyncMock(return_value=[]))
    monkeypatch.setattr(main.company_access, "list_accessible_companies", AsyncMock(return_value=[{"company_id": 5, "company_name": "Test Company"}]))
    monkeypatch.setattr(main.cart_repo, "summarise_cart", AsyncMock(return_value={"item_count": 0, "total_quantity": 0, "subtotal": Decimal("0")}))
    monkeypatch.setattr(main.notifications_repo, "count_notifications", AsyncMock(return_value=0))
    monkeypatch.setattr(main.membership_repo, "user_has_permission", AsyncMock(return_value=False))
    monkeypatch.setattr(main.modules_service, "list_modules", AsyncMock(return_value=[]))

    # Mock _render_template to capture context
    captured_context = {}

    async def mock_render_template(template_name, request, user, extra=None):
        context = await main._build_base_context(request, user, extra=extra)
        captured_context.update(context)
        return f"Rendered: {template_name}"

    monkeypatch.setattr(main, "_render_template", mock_render_template)

    response = await main.shop_packages_page(request)

    # Verify cart_allowed is False in context
    assert "can_access_cart" in captured_context
    assert captured_context["can_access_cart"] is False
