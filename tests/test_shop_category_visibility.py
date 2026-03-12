"""Tests for shop category visibility filtering (hide categories without available products)."""
import asyncio

from app.repositories import shop as shop_repo


def test_get_category_ids_with_available_products_basic(monkeypatch):
    """Returns category IDs that have at least one non-archived, in-stock product."""

    async def fake_fetch_all(query, params=None):
        return [
            {"category_id": 1},
            {"category_id": 3},
        ]

    monkeypatch.setattr(shop_repo.db, "fetch_all", fake_fetch_all)

    result = asyncio.run(
        shop_repo.get_category_ids_with_available_products(company_id=None, include_out_of_stock=False)
    )

    assert result == {1, 3}


def test_get_category_ids_with_available_products_empty(monkeypatch):
    """Returns empty set when no products are available."""

    async def fake_fetch_all(query, params=None):
        return []

    monkeypatch.setattr(shop_repo.db, "fetch_all", fake_fetch_all)

    result = asyncio.run(
        shop_repo.get_category_ids_with_available_products()
    )

    assert result == set()


def test_get_category_ids_with_available_products_includes_stock_filter(monkeypatch):
    """SQL query includes stock > 0 filter when include_out_of_stock is False."""
    captured_query = {}

    async def fake_fetch_all(query, params=None):
        captured_query["sql"] = query
        captured_query["params"] = params
        return []

    monkeypatch.setattr(shop_repo.db, "fetch_all", fake_fetch_all)

    asyncio.run(
        shop_repo.get_category_ids_with_available_products(include_out_of_stock=False)
    )

    assert "stock > 0" in captured_query["sql"]


def test_get_category_ids_with_available_products_omits_stock_filter_when_show_out_of_stock(monkeypatch):
    """SQL query does NOT include stock > 0 filter when include_out_of_stock is True."""
    captured_query = {}

    async def fake_fetch_all(query, params=None):
        captured_query["sql"] = query
        return []

    monkeypatch.setattr(shop_repo.db, "fetch_all", fake_fetch_all)

    asyncio.run(
        shop_repo.get_category_ids_with_available_products(include_out_of_stock=True)
    )

    assert "stock > 0" not in captured_query["sql"]


def test_get_category_ids_with_available_products_includes_company_exclusion_filter(monkeypatch):
    """SQL query includes company exclusion join and filter when company_id is provided."""
    captured_query = {}

    async def fake_fetch_all(query, params=None):
        captured_query["sql"] = query
        captured_query["params"] = params
        return []

    monkeypatch.setattr(shop_repo.db, "fetch_all", fake_fetch_all)

    asyncio.run(
        shop_repo.get_category_ids_with_available_products(company_id=42)
    )

    assert "shop_product_exclusions" in captured_query["sql"]
    assert "e.product_id IS NULL" in captured_query["sql"]
    assert captured_query["params"] == (42,)


# ---------------------------------------------------------------------------
# Tests for the _filter_categories helper used in the /shop route handler.
# We replicate the same logic here to test it in isolation.
# ---------------------------------------------------------------------------


def _filter_categories(cats, available_ids):
    """Mirror of the filtering helper defined inside the shop_page route."""
    result = []
    for cat in cats:
        filtered_children = _filter_categories(cat.get("children", []), available_ids)
        if cat["id"] in available_ids or filtered_children:
            result.append({**cat, "children": filtered_children})
    return result


def test_filter_categories_hides_category_without_products():
    """A category with no available products is removed from the list."""
    categories = [
        {"id": 1, "name": "Electronics", "children": []},
        {"id": 2, "name": "Empty Category", "children": []},
    ]
    available_ids = {1}
    result = _filter_categories(categories, available_ids)
    assert len(result) == 1
    assert result[0]["id"] == 1


def test_filter_categories_keeps_parent_with_available_child():
    """A parent category that has no direct products is kept if a child has products."""
    categories = [
        {
            "id": 1,
            "name": "Electronics",
            "children": [
                {"id": 2, "name": "Laptops", "children": []},
                {"id": 3, "name": "Empty Sub", "children": []},
            ],
        }
    ]
    available_ids = {2}
    result = _filter_categories(categories, available_ids)

    assert len(result) == 1
    assert result[0]["id"] == 1
    assert len(result[0]["children"]) == 1
    assert result[0]["children"][0]["id"] == 2


def test_filter_categories_removes_parent_when_all_children_empty():
    """A parent category is hidden if neither it nor any child has available products."""
    categories = [
        {
            "id": 1,
            "name": "Electronics",
            "children": [
                {"id": 2, "name": "Empty Sub", "children": []},
            ],
        }
    ]
    available_ids = set()
    result = _filter_categories(categories, available_ids)

    assert result == []


def test_filter_categories_preserves_nested_hierarchy():
    """Deeply nested categories are correctly filtered."""
    categories = [
        {
            "id": 1,
            "name": "Level 1",
            "children": [
                {
                    "id": 2,
                    "name": "Level 2 (has products)",
                    "children": [
                        {"id": 4, "name": "Level 3", "children": []},
                    ],
                },
                {
                    "id": 3,
                    "name": "Level 2 (empty)",
                    "children": [],
                },
            ],
        }
    ]
    available_ids = {4}
    result = _filter_categories(categories, available_ids)

    # Level 1 kept because Level 2 -> Level 3 has products
    assert len(result) == 1
    assert result[0]["id"] == 1
    # Level 2 (has products) kept, Level 2 (empty) removed
    assert len(result[0]["children"]) == 1
    assert result[0]["children"][0]["id"] == 2
    # Level 3 kept because it has products
    assert len(result[0]["children"][0]["children"]) == 1
    assert result[0]["children"][0]["children"][0]["id"] == 4


def test_filter_categories_empty_input():
    """Empty input returns empty output."""
    assert _filter_categories([], {1, 2, 3}) == []
