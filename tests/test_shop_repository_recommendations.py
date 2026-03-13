from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest

from app.repositories import shop as shop_repo


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio("asyncio")
async def test_get_product_ids_by_skus_matches_vendor_sku(monkeypatch):
    """get_product_ids_by_skus resolves IDs via vendor_sku when sku does not match.

    Products may be created with a custom internal sku but retain the vendor's
    StockCode as vendor_sku.  opt_accessori references use the vendor's StockCode
    so the lookup must check both columns.
    """
    captured: list[tuple] = []

    async def fake_fetch_all(sql: str, params: tuple | None = None):
        captured.append((sql, params))
        # Simulate a product whose sku differs from the vendor SKU but whose
        # vendor_sku matches the requested value.
        return [{"id": 42}]

    monkeypatch.setattr(shop_repo.db, "fetch_all", fake_fetch_all)

    ids = await shop_repo.get_product_ids_by_skus(["NHU-POE-24-12WG"])

    assert ids == [42]
    assert len(captured) == 1
    sql_used, params_used = captured[0]
    # Both sku and vendor_sku must be in the WHERE clause
    assert "vendor_sku" in sql_used.lower()
    assert "sku" in sql_used.lower()
    # The SKU should appear twice in the params (once for sku, once for vendor_sku)
    assert params_used is not None
    assert params_used.count("NHU-POE-24-12WG") == 2


@pytest.mark.anyio("asyncio")
async def test_get_product_ids_by_skus_deduplicates_when_both_columns_match(monkeypatch):
    """When sku and vendor_sku both match, only one ID is returned (DISTINCT)."""

    async def fake_fetch_all(sql: str, params: tuple | None = None):
        # DB returns a single DISTINCT row even when both columns match
        return [{"id": 7}]

    monkeypatch.setattr(shop_repo.db, "fetch_all", fake_fetch_all)

    ids = await shop_repo.get_product_ids_by_skus(["ABC123"])

    assert ids == [7]


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
