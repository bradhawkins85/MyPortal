"""Tests for shop product search query construction."""

import asyncio

from app.repositories import shop as shop_repo


def test_list_products_uses_fulltext_for_long_search_terms(monkeypatch):
    """Long terms should use MATCH ... AGAINST in BOOLEAN MODE."""
    captured: dict[str, object] = {}

    async def fake_fetch_all(query, params=None):
        if "query" not in captured:
            captured["query"] = query
            captured["params"] = params
        return []

    async def fake_attach_features(products):
        return products

    async def fake_populate_recommendations(products):
        return None

    monkeypatch.setattr(shop_repo.db, "fetch_all", fake_fetch_all)
    monkeypatch.setattr(shop_repo, "_attach_features_to_products", fake_attach_features)
    monkeypatch.setattr(shop_repo, "_populate_product_recommendations", fake_populate_recommendations)

    filters = shop_repo.ProductFilters(search_term="wireless mouse")
    asyncio.run(shop_repo.list_products(filters))

    assert "MATCH (p.name, p.sku, p.vendor_sku) AGAINST (%s IN BOOLEAN MODE)" in str(captured["query"])
    assert "+wireless* +mouse*" in str(captured["params"])


def test_list_products_uses_prefix_like_for_short_search_terms(monkeypatch):
    """Short terms should use indexable prefix LIKE lookups."""
    captured: dict[str, object] = {}

    async def fake_fetch_all(query, params=None):
        if "query" not in captured:
            captured["query"] = query
            captured["params"] = params
        return []

    async def fake_attach_features(products):
        return products

    async def fake_populate_recommendations(products):
        return None

    monkeypatch.setattr(shop_repo.db, "fetch_all", fake_fetch_all)
    monkeypatch.setattr(shop_repo, "_attach_features_to_products", fake_attach_features)
    monkeypatch.setattr(shop_repo, "_populate_product_recommendations", fake_populate_recommendations)

    filters = shop_repo.ProductFilters(search_term="ab")
    asyncio.run(shop_repo.list_products(filters))

    assert "MATCH (p.name, p.sku, p.vendor_sku) AGAINST (%s IN BOOLEAN MODE)" not in str(captured["query"])
    assert "(p.name LIKE %s OR p.sku LIKE %s OR p.vendor_sku LIKE %s)" in str(captured["query"])
    assert "%ab%" not in str(captured["params"])
    assert "ab%" in str(captured["params"])


def test_prepare_product_search_term_falls_back_to_prefix_like_without_fulltext_tokens():
    """Terms that sanitize below fulltext min length should still avoid broad scans."""
    mode, value = shop_repo._prepare_product_search_term("a-")

    assert mode == "prefix"
    assert value == "a-%"


def test_list_products_summary_applies_search_before_pagination(monkeypatch):
    """Admin summaries should push search into SQL before LIMIT/OFFSET."""
    captured: dict[str, object] = {}

    async def fake_fetch_all(query, params=None):
        captured["query"] = query
        captured["params"] = params
        return []

    monkeypatch.setattr(shop_repo.db, "fetch_all", fake_fetch_all)

    filters = shop_repo.ProductFilters(
        search_term="page two product",
        include_out_of_stock=True,
        limit=50,
        offset=0,
    )
    asyncio.run(shop_repo.list_products_summary(filters))

    query = str(captured["query"])
    assert "MATCH (p.name, p.sku, p.vendor_sku) AGAINST (%s IN BOOLEAN MODE)" in query
    assert query.index("MATCH") < query.index("LIMIT")
    assert "+page* +two* +product*" in str(captured["params"])


def test_count_products_uses_same_search_filter_as_summary(monkeypatch):
    """Pagination totals should count only products matching the search term."""
    captured: dict[str, object] = {}

    async def fake_fetch_one(query, params=None):
        captured["query"] = query
        captured["params"] = params
        return {"total_count": 1}

    monkeypatch.setattr(shop_repo.db, "fetch_one", fake_fetch_one)

    filters = shop_repo.ProductFilters(search_term="needle", include_out_of_stock=True)
    total = asyncio.run(shop_repo.count_products(filters))

    assert total == 1
    assert "MATCH (p.name, p.sku, p.vendor_sku) AGAINST (%s IN BOOLEAN MODE)" in str(captured["query"])
    assert "+needle*" in str(captured["params"])
