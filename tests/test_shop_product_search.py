"""Tests for shop product search query construction."""

import asyncio
from pathlib import Path

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


def test_list_products_summary_defaults_do_not_reference_out_of_stock_flag(monkeypatch):
    """Admin product summaries should not crash or filter stock by default."""
    captured: dict[str, object] = {}

    async def fake_fetch_all(query, params=None):
        captured["query"] = query
        captured["params"] = params
        return []

    monkeypatch.setattr(shop_repo.db, "fetch_all", fake_fetch_all)

    filters = shop_repo.ProductFilters(include_archived=False)
    products = asyncio.run(shop_repo.list_products_summary(filters))

    assert products == []
    assert "p.stock > 0" not in str(captured["query"])


def test_list_products_summary_honors_in_stock_only(monkeypatch):
    """Customer product summaries should filter stock when requested."""
    captured: dict[str, object] = {}

    async def fake_fetch_all(query, params=None):
        captured["query"] = query
        captured["params"] = params
        return []

    monkeypatch.setattr(shop_repo.db, "fetch_all", fake_fetch_all)

    filters = shop_repo.ProductFilters(include_archived=False, in_stock_only=True)
    products = asyncio.run(shop_repo.list_products_summary(filters))

    assert products == []
    assert "p.stock > 0" in str(captured["query"])


def test_count_products_honors_in_stock_only(monkeypatch):
    """Product counts should use the shared in-stock filter field."""
    captured: dict[str, object] = {}

    async def fake_fetch_one(query, params=None):
        captured["query"] = query
        captured["params"] = params
        return {"total_count": 0}

    monkeypatch.setattr(shop_repo.db, "fetch_one", fake_fetch_one)

    total = asyncio.run(
        shop_repo.count_products(shop_repo.ProductFilters(in_stock_only=True))
    )

    assert total == 0
    assert "p.stock > 0" in str(captured["query"])


def test_product_filter_queries_use_shared_stock_filter_property():
    """ProductFilter-driven queries should not reference a missing local stock flag."""
    source = Path("app/repositories/shop.py").read_text()

    query_section = source.split("async def list_products(filters: ProductFilters)", 1)[1]
    query_section = query_section.split("async def list_featured_products_for_company", 1)[0]

    assert "if not include_out_of_stock:" not in query_section
    assert query_section.count("if filters.require_in_stock:") == 3
