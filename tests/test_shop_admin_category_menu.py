"""Admin category navigation counts the same products as the catalogue."""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from jinja2 import Environment, FileSystemLoader, select_autoescape
from starlette.requests import Request

from app.features.shop import handlers
from app.repositories import companies, license_sku_friendly_names, shop, subscription_categories


@pytest.fixture
def categories():
    return [
        {"id": 1, "name": "Computers", "parent_id": None},
        {"id": 2, "name": "Laptops", "parent_id": 1},
        {"id": 3, "name": "Gaming", "parent_id": 2},
        {"id": 4, "name": "Empty branch", "parent_id": 1},
        {"id": 5, "name": "Empty root", "parent_id": None},
    ]


def test_menu_counts_descendants_and_omits_empty_branches(categories):
    products = [
        {"category_id": 1}, {"category_id": 3}, {"category_id": 3},
        {"category_id": None},
    ]
    menu = handlers._admin_category_menu(categories, products)
    assert [(c["id"], c["product_count"], c["depth"]) for c in menu] == [
        (1, 3, 0), (2, 2, 1), (3, 2, 2),
    ]
    assert all("product_count" not in c for c in categories)
    assert handlers._admin_category_menu(categories, []) == []


@pytest.mark.asyncio
@pytest.mark.parametrize("show_archived,search", [(False, ""), (True, "laptop")])
async def test_admin_menu_matches_catalogue_filters(monkeypatch, categories, show_archived, search):
    main = SimpleNamespace(
        _require_super_admin_page=AsyncMock(return_value=({"id": 1}, None)),
        _render_template=AsyncMock(),
    )
    monkeypatch.setattr(handlers, "_main", lambda: main)
    monkeypatch.setattr(shop, "list_all_categories_flat", AsyncMock(return_value=categories))
    product = {"id": 10, "category_id": 3, "price": 20, "buy_price": 10}
    uncategorised = {"id": 11, "category_id": None, "price": 20, "buy_price": 10}
    fetch_products = AsyncMock(return_value=[product, uncategorised])
    monkeypatch.setattr(shop, "list_products_summary", fetch_products)
    monkeypatch.setattr(companies, "list_companies", AsyncMock(return_value=[]))
    monkeypatch.setattr(subscription_categories, "list_categories", AsyncMock(return_value=[]))
    monkeypatch.setattr(license_sku_friendly_names, "list_mappings", AsyncMock(return_value=[]))
    request = Request({"type": "http", "path": "/admin/shop", "query_string": b""})

    await handlers.admin_shop_page(request, show_archived=show_archived, search=search, category=1)

    filters = fetch_products.call_args.args[0]
    assert filters.include_archived is show_archived
    assert filters.search_term == (search or None)
    assert filters.include_out_of_stock is True
    context = main._render_template.call_args.kwargs["extra"]
    assert context["products"] == [product]
    assert context["total_count"] == 1
    assert context["catalogue_count"] == 2
    assert len(context["categories"]) == 5  # Empty categories remain available for editing.
    menu = context["filter_categories"]
    assert [(c["id"], c["product_count"]) for c in menu] == [(1, 1), (2, 1), (3, 1)]
    assert "category=3" in menu[2]["url"]
    if show_archived:
        assert "showArchived=1" in menu[2]["url"]
        assert "search=laptop" in menu[2]["url"]

    environment = Environment(loader=FileSystemLoader("app/templates"), autoescape=select_autoescape())
    rendered = environment.get_template("admin/partials/shop_category_menu.html").render(context)
    assert "Empty branch" not in rendered
    assert "Empty root" not in rendered
    assert 'title="1 product">1</span>' in rendered
    assert f'href="{menu[0]["url"].replace("&", "&amp;")}" aria-current="page"' in rendered
    assert 'title="2 products">2</span>' in rendered
