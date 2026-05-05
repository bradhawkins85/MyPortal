"""Tests for per-company shop item visibility management."""
from __future__ import annotations

import asyncio

from app.repositories import shop as shop_repo


def test_list_products_with_exclusion_status_returns_all_products(monkeypatch):
    """Returns all non-archived products with is_hidden flag."""

    async def fake_fetch_all(query, params=None):
        return [
            {
                "id": 1,
                "name": "Product A",
                "sku": "SKU-A",
                "category_id": 10,
                "category_name": "Laptops",
                "is_hidden": 0,
            },
            {
                "id": 2,
                "name": "Product B",
                "sku": "SKU-B",
                "category_id": 10,
                "category_name": "Laptops",
                "is_hidden": 1,
            },
        ]

    monkeypatch.setattr(shop_repo.db, "fetch_all", fake_fetch_all)

    result = asyncio.run(
        shop_repo.list_products_with_exclusion_status_for_company(company_id=5)
    )

    assert len(result) == 2
    assert result[0]["id"] == 1
    assert result[0]["is_hidden"] is False
    assert result[1]["id"] == 2
    assert result[1]["is_hidden"] is True


def test_list_products_with_exclusion_status_passes_company_id(monkeypatch):
    """SQL query includes the company_id parameter for the exclusion join."""
    captured = {}

    async def fake_fetch_all(query, params=None):
        captured["query"] = query
        captured["params"] = params
        return []

    monkeypatch.setattr(shop_repo.db, "fetch_all", fake_fetch_all)

    asyncio.run(
        shop_repo.list_products_with_exclusion_status_for_company(company_id=42)
    )

    assert "shop_product_exclusions" in captured["query"]
    assert captured["params"] == (42,)


def test_list_products_with_exclusion_status_filters_archived(monkeypatch):
    """SQL query filters out archived products."""
    captured = {}

    async def fake_fetch_all(query, params=None):
        captured["query"] = query
        return []

    monkeypatch.setattr(shop_repo.db, "fetch_all", fake_fetch_all)

    asyncio.run(
        shop_repo.list_products_with_exclusion_status_for_company(company_id=1)
    )

    assert "archived = 0" in captured["query"]


def test_list_products_with_exclusion_status_empty(monkeypatch):
    """Returns empty list when no products exist."""

    async def fake_fetch_all(query, params=None):
        return []

    monkeypatch.setattr(shop_repo.db, "fetch_all", fake_fetch_all)

    result = asyncio.run(
        shop_repo.list_products_with_exclusion_status_for_company(company_id=99)
    )

    assert result == []


def test_company_visibility_template_has_shop_section():
    """Admin company edit template contains the shop visibility section."""
    from pathlib import Path

    template = Path("app/templates/admin/company_edit.html").read_text()

    assert "shop-visibility-panel" in template
    assert "shop-visibility-form" in template
    assert "shop-visibility-grid" in template
    assert "shop-visibility-loading" in template


def test_company_visibility_template_explains_semantics():
    """Company edit template explains that ticking hides item from company store."""
    from pathlib import Path

    template = Path("app/templates/admin/company_edit.html").read_text()

    assert "hide" in template.lower() or "hidden" in template.lower()


def test_shop_admin_visibility_modal_explains_semantics():
    """Admin shop visibility modal explains that ticking hides item from company store."""
    from pathlib import Path

    template = Path("app/templates/admin/shop.html").read_text()

    # The modal subtitle should explain that ticking = hiding
    assert "hide" in template.lower() or "hidden" in template.lower()
    assert "default" in template.lower()
