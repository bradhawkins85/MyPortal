from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from app.repositories import shop as shop_repo


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio("asyncio")
async def test_list_products_includes_recommendations(monkeypatch):
    async def fake_fetch_all(sql: str, params: tuple[Any, ...] | None = None):
        return [
            {
                "id": 1,
                "name": "Example",
                "sku": "SKU-1",
                "vendor_sku": "VSKU-1",
                "description": "",
                "image_url": None,
                "price": Decimal("19.99"),
                "vip_price": None,
                "stock": 5,
                "category_id": 3,
                "archived": 0,
            }
        ]

    async def fake_attach(products: list[dict[str, Any]]):
        return products

    async def fake_populate(products: list[dict[str, Any]]):
        for product in products:
            product["cross_sell_product_ids"] = [11, 12]
            product["upsell_product_ids"] = [21]

    monkeypatch.setattr(shop_repo.db, "fetch_all", fake_fetch_all)
    monkeypatch.setattr(shop_repo, "_attach_features_to_products", fake_attach)
    monkeypatch.setattr(shop_repo, "_populate_product_recommendations", fake_populate)

    products = await shop_repo.list_products(shop_repo.ProductFilters(include_archived=True))
    assert products
    product = products[0]
    assert product["cross_sell_product_ids"] == [11, 12]
    assert product["upsell_product_ids"] == [21]


@pytest.mark.anyio("asyncio")
async def test_list_products_by_ids_includes_recommendations(monkeypatch):
    async def fake_fetch_all(sql: str, params: tuple[Any, ...] | None = None):
        return [
            {
                "id": 2,
                "name": "Accessory",
                "sku": "SKU-2",
                "vendor_sku": "VSKU-2",
                "description": "",
                "image_url": None,
                "price": Decimal("9.99"),
                "vip_price": None,
                "stock": 2,
                "category_id": 3,
                "archived": 0,
            }
        ]

    async def fake_attach(products: list[dict[str, Any]]):
        return products

    async def fake_populate(products: list[dict[str, Any]]):
        for product in products:
            product["cross_sell_product_ids"] = [1]
            product["upsell_product_ids"] = [3]

    monkeypatch.setattr(shop_repo.db, "fetch_all", fake_fetch_all)
    monkeypatch.setattr(shop_repo, "_attach_features_to_products", fake_attach)
    monkeypatch.setattr(shop_repo, "_populate_product_recommendations", fake_populate)

    products = await shop_repo.list_products_by_ids([2])
    assert products
    product = products[0]
    assert product["cross_sell_product_ids"] == [1]
    assert product["upsell_product_ids"] == [3]
