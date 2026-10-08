"""Tests for the order-placed confirmation page.

``POST /cart/place-order`` now redirects to ``/cart/order-placed`` on success.
These tests exercise the new ``order_placed_page`` handler directly with a
mocked ``_main`` so they do not depend on the full application lifespan (DB
pool, scheduler, background workers). The success-path redirect produced by
``place_order`` is covered by ``tests/test_cart_shipping_address.py``.
"""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


def _mock_request(url_for_value: str = "/shop") -> MagicMock:
    from starlette.requests import Request

    mock_request = MagicMock(spec=Request)
    mock_request.url_for = MagicMock(return_value=url_for_value)
    return mock_request


def _mock_session() -> MagicMock:
    mock_session = MagicMock()
    mock_session.id = 123
    return mock_session


def _ctx() -> tuple:
    return ({"id": 1}, {"id": 1}, {"id": 1, "name": "Co", "is_vip": 0}, 1, None)


@pytest.mark.asyncio
async def test_order_placed_page_renders_confirmation_context():
    """order_placed_page renders the confirmation template with the order
    number, the shop redirect target and the countdown values, and is never
    cached."""
    from app.features.cart.routes import order_placed_page

    captured: dict = {}

    async def fake_render(template_name, request_obj, user_obj, *, extra):
        captured["template_name"] = template_name
        captured["extra"] = extra
        from fastapi.responses import HTMLResponse

        return HTMLResponse("ok")

    with patch("app.features.cart.routes._main") as mock_main:
        mock_main.return_value._load_company_section_context = AsyncMock(return_value=_ctx())
        mock_main.return_value.session_manager.load_session = AsyncMock(return_value=_mock_session())
        mock_main.return_value._render_template = AsyncMock(side_effect=fake_render)

        response = await order_placed_page(_mock_request("/shop"), order_number="ORD123")

    assert captured["template_name"] == "shop/order_placed.html"
    extra = captured["extra"]
    assert extra["order_number"] == "ORD123"
    assert extra["redirect_url"] == "/shop"
    assert extra["redirect_delay_ms"] == 5000
    assert extra["countdown_seconds"] == 5
    assert response.headers["Cache-Control"] == "no-store, no-cache, must-revalidate"
    assert response.headers["Pragma"] == "no-cache"


@pytest.mark.asyncio
async def test_order_placed_page_without_order_number():
    """When the orderNumber query param is absent the page still renders, with
    a None order number so the template can hide the number line."""
    from app.features.cart.routes import order_placed_page

    captured: dict = {}

    async def fake_render(template_name, request_obj, user_obj, *, extra):
        captured["extra"] = extra
        from fastapi.responses import HTMLResponse

        return HTMLResponse("ok")

    with patch("app.features.cart.routes._main") as mock_main:
        mock_main.return_value._load_company_section_context = AsyncMock(return_value=_ctx())
        mock_main.return_value.session_manager.load_session = AsyncMock(return_value=_mock_session())
        mock_main.return_value._render_template = AsyncMock(side_effect=fake_render)

        await order_placed_page(_mock_request("/shop"), order_number=None)

    assert captured["extra"]["order_number"] is None
    assert captured["extra"]["redirect_url"] == "/shop"


@pytest.mark.asyncio
async def test_order_placed_page_requires_active_session():
    """Visitors without an active session are redirected to /login instead of
    seeing the confirmation page."""
    from app.features.cart.routes import order_placed_page
    from fastapi import status

    with patch("app.features.cart.routes._main") as mock_main:
        mock_main.return_value._load_company_section_context = AsyncMock(return_value=_ctx())
        mock_main.return_value.session_manager.load_session = AsyncMock(return_value=None)

        response = await order_placed_page(_mock_request("/shop"), order_number="ORD123")

    assert response.status_code == status.HTTP_303_SEE_OTHER
    assert response.headers["location"] == "/login"
