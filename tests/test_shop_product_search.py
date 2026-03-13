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


def test_list_products_uses_like_for_short_search_terms(monkeypatch):
    """Short terms should retain LIKE fallback behavior."""
    captured: dict[str, object] = {}

    async def fake_fetch_all(query, params=None):
        if "query" not in captured:
            captured["query"] = query
            captured["params"] = params
            return []
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
    assert "%ab%" in str(captured["params"])
