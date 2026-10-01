from unittest.mock import AsyncMock

import pytest
from starlette.requests import Request

from app import main
from app.features.shop import handlers as shop_handlers
from app.repositories import subscription_categories as subscription_categories_repo


async def _dummy_receive() -> dict[str, object]:
    return {"type": "http.request", "body": b"", "more_body": False}


def _make_request(path: str = "/shop") -> Request:
    scope = {"type": "http", "method": "GET", "path": path, "headers": []}
    return Request(scope, _dummy_receive)


@pytest.mark.anyio("asyncio")
async def test_shop_page_shows_all_visible_products_without_pagination(monkeypatch):
    request = _make_request("/shop")
    user = {"id": 9, "company_id": 5}
    membership = {"can_access_shop": 1}
    company = {"id": 5, "is_vip": 0}

    monkeypatch.setattr(
        main,
        "_load_company_section_context",
        AsyncMock(return_value=(user, membership, company, 5, None)),
    )
    monkeypatch.setattr(main.shop_repo, "list_categories", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        main.shop_repo,
        "get_category_ids_with_available_products",
        AsyncMock(return_value=set()),
    )
    monkeypatch.setattr(
        main.shop_repo,
        "list_products_summary",
        AsyncMock(
            return_value=[
                {"id": 1, "name": "Visible", "price": "10.00", "vip_price": None},
                {"id": 2, "name": "Below DBP", "price": "12.00", "vip_price": None},
                {"id": 3, "name": "No Price", "price": "0", "vip_price": None},
                {"id": 4, "name": "Visible Two", "price": "15.00", "vip_price": None},
                {"id": 5, "name": "Visible Three", "price": "20.00", "vip_price": None},
            ]
        ),
    )
    monkeypatch.setattr(
        main.shop_service,
        "is_price_below_dbp_threshold",
        lambda product, is_vip=False: product.get("id") == 2,
    )
    monkeypatch.setattr(
        main.subscriptions_repo,
        "get_active_subscription_product_ids",
        AsyncMock(return_value=[]),
    )
    monkeypatch.setattr(subscription_categories_repo, "list_categories", AsyncMock(return_value=[]))

    captured_extra: dict[str, object] = {}

    async def mock_render_template(template_name, request, user, extra=None):
        captured_extra.update(extra or {})
        return "ok"

    monkeypatch.setattr(main, "_render_template", mock_render_template)

    response = await shop_handlers.shop_page(request, page=1, page_size=2)

    assert response == "ok"
    assert captured_extra["total_count"] == 3
    assert captured_extra["total_pages"] == 1
    assert captured_extra["page"] == 1
    assert captured_extra["page_size"] == 3
    assert [product["id"] for product in captured_extra["products"]] == [1, 4, 5]


@pytest.mark.anyio("asyncio")
async def test_shop_page_uses_subscription_prices_without_product_price(monkeypatch):
    request = _make_request("/shop")
    monkeypatch.setattr(main, "_load_company_section_context", AsyncMock(
        return_value=({"id": 9}, {}, {"id": 5, "is_vip": 0}, 5, None)))
    monkeypatch.setattr(main.shop_repo, "list_categories", AsyncMock(return_value=[]))
    monkeypatch.setattr(main.shop_repo, "get_category_ids_with_available_products",
                        AsyncMock(return_value=set()))
    monkeypatch.setattr(main.shop_repo, "list_products_summary", AsyncMock(return_value=[{
        "id": 7, "name": "Subscription", "price": 0,
        "subscription_category_id": 2,
        "price_monthly_commitment": "12.00",
        "price_annual_monthly_payment": "10.00",
        "price_annual_annual_payment": None,
    }]))
    monkeypatch.setattr(main.subscriptions_repo, "get_active_subscription_product_ids",
                        AsyncMock(return_value=[]))
    monkeypatch.setattr(subscription_categories_repo, "list_categories",
                        AsyncMock(return_value=[]))
    captured_extra = {}

    async def mock_render_template(template_name, request, user, extra=None):
        captured_extra.update(extra or {})
        return "ok"

    monkeypatch.setattr(main, "_render_template", mock_render_template)

    assert await shop_handlers.shop_page(request) == "ok"
    product = captured_extra["products"][0]
    assert product["has_multiple_subscription_prices"] is True
    assert [option["price"] for option in product["subscription_price_options"]] == [12.0, 10.0]
