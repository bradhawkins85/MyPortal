"""Tests for the Optional Accessories admin page."""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest
from starlette.requests import Request
from starlette.responses import HTMLResponse

from app import main
from app.repositories import shop as shop_repo


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


async def _dummy_receive() -> dict[str, Any]:
    return {"type": "http.request", "body": b"", "more_body": False}


def _make_request(path: str = "/admin/shop/optional-accessories") -> Request:
    scope = {
        "type": "http",
        "method": "GET",
        "path": path,
        "headers": [],
    }
    return Request(scope, _dummy_receive)


# ---------------------------------------------------------------------------
# Repository unit tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio("asyncio")
async def test_list_optional_accessory_products_returns_empty_when_no_cross_sells(
    monkeypatch,
):
    """When no cross-sell rows exist, the function returns an empty list."""

    async def fake_fetch_all(sql: str, params: Any = None):
        return []

    monkeypatch.setattr(shop_repo.db, "fetch_all", fake_fetch_all)

    result = await shop_repo.list_optional_accessory_products()
    assert result == []


@pytest.mark.anyio("asyncio")
async def test_list_optional_accessory_products_normalises_rows(monkeypatch):
    """Returned rows are normalised with expected fields."""
    from decimal import Decimal

    rows = [
        {
            "id": 5,
            "name": "USB-C Cable",
            "sku": "USBC-001",
            "vendor_sku": "V-USBC",
            "image_url": None,
            "price": Decimal("9.99"),
            "vip_price": None,
            "stock": 10,
            "archived": 0,
            "category_id": 2,
            "category_name": "Accessories",
            "parent_product_names": "Laptop Pro, Tablet X",
            "parent_product_ids": "1,3",
        }
    ]

    async def fake_fetch_all(sql: str, params: Any = None):
        return rows

    monkeypatch.setattr(shop_repo.db, "fetch_all", fake_fetch_all)

    result = await shop_repo.list_optional_accessory_products()

    assert len(result) == 1
    product = result[0]
    assert product["id"] == 5
    assert product["name"] == "USB-C Cable"
    assert product["sku"] == "USBC-001"
    assert product["vendor_sku"] == "V-USBC"
    assert product["category_name"] == "Accessories"
    assert product["parent_product_names"] == "Laptop Pro, Tablet X"
    assert product["parent_product_ids"] == [1, 3]
    assert product["stock"] == 10
    assert not product["archived"]


@pytest.mark.anyio("asyncio")
async def test_list_optional_accessory_products_handles_null_parent_names(monkeypatch):
    """NULL parent_product_names is normalised to an empty string."""

    rows = [
        {
            "id": 7,
            "name": "Power Adapter",
            "sku": "PWR-001",
            "vendor_sku": None,
            "image_url": None,
            "price": 19.99,
            "vip_price": None,
            "stock": 0,
            "archived": 0,
            "category_id": None,
            "category_name": None,
            "parent_product_names": None,
            "parent_product_ids": None,
        }
    ]

    async def fake_fetch_all(sql: str, params: Any = None):
        return rows

    monkeypatch.setattr(shop_repo.db, "fetch_all", fake_fetch_all)

    result = await shop_repo.list_optional_accessory_products()
    assert len(result) == 1
    product = result[0]
    assert product["parent_product_names"] == ""
    assert product["parent_product_ids"] == []
    assert product["category_name"] is None


# ---------------------------------------------------------------------------
# Route unit tests
# ---------------------------------------------------------------------------


@pytest.mark.anyio("asyncio")
async def test_admin_optional_accessories_page_renders(monkeypatch):
    """The optional accessories admin page renders without errors."""
    request = _make_request()

    current_user = {"id": 1, "is_super_admin": True}
    monkeypatch.setattr(
        main,
        "_require_super_admin_page",
        AsyncMock(return_value=(current_user, None)),
    )

    accessories = [
        {
            "id": 1,
            "sku": "ACC-001",
            "product_name": "USB-C Cable",
            "category_name": "Accessories",
            "rrp": 9.99,
            "image_url": None,
            "manufacturer": None,
            "referenced_by_skus": "LAPTOP-001",
            "discovered_at": None,
        }
    ]
    monkeypatch.setattr(
        main.shop_repo,
        "list_pending_optional_accessories",
        AsyncMock(return_value=accessories),
    )

    async def fake_render(template, request, user, extra=None):
        assert template == "admin/shop_optional_accessories.html"
        assert extra is not None
        assert extra["title"] == "Optional accessories"
        assert extra["accessories"] == accessories
        return HTMLResponse(content="<html></html>")

    monkeypatch.setattr(main, "_render_template", fake_render)

    response = await main.admin_shop_optional_accessories_page(request)
    assert response.status_code == 200


@pytest.mark.anyio("asyncio")
async def test_admin_optional_accessories_page_redirects_non_admin(monkeypatch):
    """Non-admin users are redirected away from the optional accessories page."""
    from starlette.responses import RedirectResponse

    request = _make_request()

    redirect = RedirectResponse(url="/login", status_code=303)
    monkeypatch.setattr(
        main,
        "_require_super_admin_page",
        AsyncMock(return_value=(None, redirect)),
    )

    list_mock = AsyncMock()
    monkeypatch.setattr(
        main.shop_repo, "list_pending_optional_accessories", list_mock
    )

    response = await main.admin_shop_optional_accessories_page(request)

    assert response.status_code == 303
    list_mock.assert_not_called()


# ---------------------------------------------------------------------------
# Template content tests
# ---------------------------------------------------------------------------


def test_optional_accessories_template_exists():
    """The template file for optional accessories must exist."""
    from pathlib import Path

    template = Path("app/templates/admin/shop_optional_accessories.html")
    assert template.exists(), "Template file is missing"


def test_shop_tools_menu_contains_optional_accessories_link():
    """The Shop Tools dropdown in admin/shop.html must link to optional accessories."""
    from pathlib import Path

    template = Path("app/templates/admin/shop.html").read_text()
    assert "/admin/shop/optional-accessories" in template
    assert "Optional accessories" in template
