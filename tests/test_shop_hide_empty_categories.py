"""Regression: /shop hides categories whose products are all filtered out."""
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


CATEGORIES = [
    {"id": 1, "name": "Laptops", "children": []},
    {"id": 2, "name": "Unpriced", "children": []},
    {"id": 3, "name": "Below DBP", "children": []},
    {
        "id": 4,
        "name": "Accessories",
        "children": [
            {"id": 5, "name": "Cables", "children": []},
            {"id": 6, "name": "Empty cables", "children": []},
        ],
    },
    {"id": 7, "name": "No products", "children": []},
]

PRODUCTS = [
    {"id": 10, "name": "Laptop", "price": "1000.00", "vip_price": None, "category_id": 1},
    {"id": 11, "name": "Free thing", "price": "0", "vip_price": None, "category_id": 2},
    {"id": 12, "name": "Cheap thing", "price": "5.00", "vip_price": None, "category_id": 3},
    {"id": 13, "name": "USB-C cable", "price": "20.00", "vip_price": None, "category_id": 5},
    {"id": 14, "name": "Unpriced cable", "price": None, "vip_price": None, "category_id": 6},
]


def _patch(monkeypatch, captured: dict[str, object]) -> None:
    monkeypatch.setattr(
        main,
        "_load_company_section_context",
        AsyncMock(return_value=({"id": 9}, {}, {"id": 5, "is_vip": 0}, 5, None)),
    )
    monkeypatch.setattr(main.shop_repo, "list_categories", AsyncMock(return_value=CATEGORIES))

    async def fake_list_products_summary(filters):
        if filters.category_ids:
            return [p for p in PRODUCTS if p["category_id"] in filters.category_ids]
        return list(PRODUCTS)

    monkeypatch.setattr(main.shop_repo, "list_products_summary", fake_list_products_summary)
    monkeypatch.setattr(
        main.shop_repo,
        "get_category_descendants",
        AsyncMock(side_effect=lambda category_id: [category_id]),
    )
    monkeypatch.setattr(
        main.shop_service,
        "is_price_below_dbp_threshold",
        lambda product, is_vip=False: product.get("id") == 12,
    )
    monkeypatch.setattr(
        main.subscriptions_repo, "get_active_subscription_product_ids", AsyncMock(return_value=[])
    )
    monkeypatch.setattr(subscription_categories_repo, "list_categories", AsyncMock(return_value=[]))

    async def mock_render_template(template_name, request, user, extra=None):
        captured.update(extra or {})
        return "ok"

    monkeypatch.setattr(main, "_render_template", mock_render_template)


def _ids(cats):
    return [(c["id"], _ids(c.get("children") or [])) for c in cats]


@pytest.mark.anyio("asyncio")
async def test_shop_landing_hides_categories_without_sellable_products(monkeypatch):
    captured: dict[str, object] = {}
    _patch(monkeypatch, captured)

    assert await shop_handlers.shop_page(_make_request()) == "ok"

    assert _ids(captured["categories"]) == [(1, []), (4, [(5, [])])]
    assert [card["id"] for card in captured["category_cards"]] == [1, 4]


@pytest.mark.anyio("asyncio")
async def test_shop_category_view_nav_hides_empty_categories(monkeypatch):
    captured: dict[str, object] = {}
    _patch(monkeypatch, captured)

    assert await shop_handlers.shop_page(_make_request(), category="1") == "ok"

    assert _ids(captured["categories"]) == [(1, []), (4, [(5, [])])]
    assert [p["id"] for p in captured["products"]] == [10]
