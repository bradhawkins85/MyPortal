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


def _make_fake_db_pool(executed: list[tuple]):
    """Return a minimal fake DB pool that records SQL executions."""

    class FakeCursor:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def execute(self, sql: str, params: tuple | None = None):
            executed.append(("execute", sql, params))

        async def executemany(self, sql: str, params_seq):
            executed.append(("executemany", sql, list(params_seq)))

    class FakeConn:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        def cursor(self, *args, **kwargs):
            return FakeCursor()

        async def begin(self):
            pass

        async def commit(self):
            pass

        async def rollback(self):
            pass

    class FakePool:
        def acquire(self):
            return FakeConn()

    return FakePool()


@pytest.mark.anyio("asyncio")
async def test_replace_product_recommendations_auto_linked_only_deletes_auto_rows(monkeypatch):
    """When auto_linked=True, only rows with is_auto_linked=1 are deleted."""
    executed: list[tuple] = []
    monkeypatch.setattr(shop_repo, "db", _make_fake_db_pool(executed))

    await shop_repo.replace_product_recommendations(
        7,
        cross_sell_ids=[10, 11],
        auto_linked=True,
    )

    delete_stmts = [s for op, s, *_ in executed if op == "execute"]
    # The DELETE must be scoped to is_auto_linked = 1 (not a blanket delete)
    assert any("is_auto_linked" in s and "1" in s for s in delete_stmts), (
        "Expected DELETE to filter by is_auto_linked = 1 but got: " + str(delete_stmts)
    )
    # No blanket DELETE without the is_auto_linked filter
    assert not any(
        "DELETE FROM shop_product_cross_sells WHERE product_id" in s
        and "is_auto_linked" not in s
        for s in delete_stmts
    ), "Blanket DELETE on cross_sells must not run when auto_linked=True"


@pytest.mark.anyio("asyncio")
async def test_replace_product_recommendations_auto_linked_skips_tables_when_ids_not_provided(
    monkeypatch,
):
    """When auto_linked=True and IDs are None, the corresponding table is untouched."""
    executed: list[tuple] = []
    monkeypatch.setattr(shop_repo, "db", _make_fake_db_pool(executed))

    # Only cross_sell_ids provided; upsell_ids not provided (None)
    await shop_repo.replace_product_recommendations(
        7,
        cross_sell_ids=[10],
        auto_linked=True,
    )

    touched_tables = [s for _, s, *_ in executed]
    upsell_touched = any("upsell" in s for s in touched_tables)
    assert not upsell_touched, (
        "upsells table must not be touched when upsell_ids is not provided"
    )
