"""Tests for the Optional Accessories admin page."""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

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


@pytest.mark.anyio("asyncio")
async def test_list_pending_optional_accessories_excludes_dismissed(monkeypatch):
    """list_pending_optional_accessories only queries non-dismissed rows."""
    captured_sql = []

    async def fake_fetch_all(sql: str, params: Any = None):
        captured_sql.append(sql)
        return []

    monkeypatch.setattr(shop_repo.db, "fetch_all", fake_fetch_all)

    await shop_repo.list_pending_optional_accessories()

    assert captured_sql, "fetch_all should have been called"
    assert "dismissed = 0" in captured_sql[0]


@pytest.mark.anyio("asyncio")
async def test_list_dismissed_optional_accessories_filters_dismissed(monkeypatch):
    """list_dismissed_optional_accessories only queries dismissed rows."""
    captured_sql = []

    async def fake_fetch_all(sql: str, params: Any = None):
        captured_sql.append(sql)
        return []

    monkeypatch.setattr(shop_repo.db, "fetch_all", fake_fetch_all)

    result = await shop_repo.list_dismissed_optional_accessories()
    assert result == []
    assert captured_sql, "fetch_all should have been called"
    assert "dismissed = 1" in captured_sql[0]


@pytest.mark.anyio("asyncio")
async def test_dismiss_pending_optional_accessory_soft_deletes(monkeypatch):
    """dismiss_pending_optional_accessory updates dismissed flag instead of deleting."""
    captured: list[tuple[str, Any]] = []

    async def fake_execute(sql: str, params: Any = None):
        captured.append((sql, params))
        return 1

    monkeypatch.setattr(shop_repo.db, "execute", fake_execute)

    result = await shop_repo.dismiss_pending_optional_accessory(42)

    assert result is True
    assert captured, "execute should have been called"
    sql = captured[0][0].upper()
    assert "UPDATE" in sql
    assert "DELETE" not in sql
    assert "DISMISSED" in sql


@pytest.mark.anyio("asyncio")
async def test_bulk_dismiss_pending_optional_accessories_updates_all(monkeypatch):
    """bulk_dismiss_pending_optional_accessories updates all provided ids."""
    captured: list[tuple[str, Any]] = []

    async def fake_execute(sql: str, params: Any = None):
        captured.append((sql, params))
        return 3

    monkeypatch.setattr(shop_repo.db, "execute", fake_execute)

    result = await shop_repo.bulk_dismiss_pending_optional_accessories([1, 2, 3])

    assert result == 3
    assert captured, "execute should have been called"
    sql = captured[0][0].upper()
    assert "UPDATE" in sql
    assert "IN" in sql
    params = captured[0][1]
    assert set(params) == {1, 2, 3}


@pytest.mark.anyio("asyncio")
async def test_bulk_dismiss_empty_list_returns_zero(monkeypatch):
    """bulk_dismiss_pending_optional_accessories returns 0 for empty input."""
    execute_mock = AsyncMock(return_value=0)
    monkeypatch.setattr(shop_repo.db, "execute", execute_mock)

    result = await shop_repo.bulk_dismiss_pending_optional_accessories([])

    assert result == 0
    execute_mock.assert_not_called()


@pytest.mark.anyio("asyncio")
async def test_restore_dismissed_optional_accessory(monkeypatch):
    """restore_dismissed_optional_accessory resets dismissed flag."""
    captured: list[tuple[str, Any]] = []

    async def fake_execute(sql: str, params: Any = None):
        captured.append((sql, params))
        return 1

    monkeypatch.setattr(shop_repo.db, "execute", fake_execute)

    result = await shop_repo.restore_dismissed_optional_accessory(7)

    assert result is True
    assert captured, "execute should have been called"
    sql = captured[0][0].upper()
    assert "UPDATE" in sql
    assert "DISMISSED" in sql
    assert captured[0][1] == (7,)


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
        assert extra["show_dismissed"] is False
        return HTMLResponse(content="<html></html>")

    monkeypatch.setattr(main, "_render_template", fake_render)

    response = await main.admin_shop_optional_accessories_page(request)
    assert response.status_code == 200


@pytest.mark.anyio("asyncio")
async def test_admin_optional_accessories_page_shows_dismissed(monkeypatch):
    """Passing show=dismissed renders the dismissed accessories list."""
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/admin/shop/optional-accessories",
        "query_string": b"show=dismissed",
        "headers": [],
    }
    request = Request(scope, _dummy_receive)

    current_user = {"id": 1, "is_super_admin": True}
    monkeypatch.setattr(
        main,
        "_require_super_admin_page",
        AsyncMock(return_value=(current_user, None)),
    )

    dismissed = [
        {
            "id": 2,
            "sku": "ACC-002",
            "product_name": "Dismissed Widget",
            "category_name": None,
            "rrp": None,
            "image_url": None,
            "manufacturer": None,
            "referenced_by_skus": "",
            "discovered_at": None,
            "dismissed_at": None,
        }
    ]
    list_dismissed_mock = AsyncMock(return_value=dismissed)
    list_pending_mock = AsyncMock(return_value=[])
    monkeypatch.setattr(main.shop_repo, "list_dismissed_optional_accessories", list_dismissed_mock)
    monkeypatch.setattr(main.shop_repo, "list_pending_optional_accessories", list_pending_mock)

    async def fake_render(template, request, user, extra=None):
        assert extra["show_dismissed"] is True
        assert extra["accessories"] == dismissed
        return HTMLResponse(content="<html></html>")

    monkeypatch.setattr(main, "_render_template", fake_render)

    response = await main.admin_shop_optional_accessories_page(request, show="dismissed")
    assert response.status_code == 200
    list_dismissed_mock.assert_called_once()
    list_pending_mock.assert_not_called()


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


@pytest.mark.anyio("asyncio")
async def test_admin_bulk_dismiss_optional_accessories(monkeypatch):
    """Bulk dismiss route calls bulk_dismiss with parsed IDs and redirects."""
    from starlette.datastructures import FormData, ImmutableMultiDict

    scope = {
        "type": "http",
        "method": "POST",
        "path": "/admin/shop/optional-accessories/bulk-dismiss",
        "headers": [],
    }
    request = Request(scope, _dummy_receive)

    current_user = {"id": 1, "is_super_admin": True}
    monkeypatch.setattr(
        main,
        "_require_super_admin_page",
        AsyncMock(return_value=(current_user, None)),
    )

    bulk_dismiss_mock = AsyncMock(return_value=2)
    monkeypatch.setattr(main.shop_repo, "bulk_dismiss_pending_optional_accessories", bulk_dismiss_mock)

    async def fake_form():
        data = ImmutableMultiDict([("accessory_ids", "5"), ("accessory_ids", "7")])
        return FormData(data)

    monkeypatch.setattr(request, "form", fake_form)

    response = await main.admin_bulk_dismiss_optional_accessories(request)

    assert response.status_code == 303
    assert response.headers["location"] == "/admin/shop/optional-accessories"
    bulk_dismiss_mock.assert_called_once_with([5, 7])


@pytest.mark.anyio("asyncio")
async def test_admin_restore_optional_accessory(monkeypatch):
    """Restore route calls restore and redirects to dismissed view."""
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/admin/shop/optional-accessories/3/restore",
        "headers": [],
    }
    request = Request(scope, _dummy_receive)

    current_user = {"id": 1, "is_super_admin": True}
    monkeypatch.setattr(
        main,
        "_require_super_admin_page",
        AsyncMock(return_value=(current_user, None)),
    )

    restore_mock = AsyncMock(return_value=True)
    monkeypatch.setattr(main.shop_repo, "restore_dismissed_optional_accessory", restore_mock)

    response = await main.admin_restore_optional_accessory(request, accessory_id=3)

    assert response.status_code == 303
    assert "show=dismissed" in response.headers["location"]
    restore_mock.assert_called_once_with(3)


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


def test_template_contains_bulk_dismiss_form():
    """Template includes a bulk dismiss form with checkboxes."""
    from pathlib import Path

    template = Path("app/templates/admin/shop_optional_accessories.html").read_text()
    assert "bulk-dismiss" in template
    assert "accessory_ids" in template
    assert "select-all-accessories" in template


def test_template_contains_dismissed_view_toggle():
    """Template includes links to switch between pending and dismissed views."""
    from pathlib import Path

    template = Path("app/templates/admin/shop_optional_accessories.html").read_text()
    assert "show=dismissed" in template
    assert "View dismissed" in template
    assert "View pending" in template


def test_template_contains_restore_action():
    """Template includes Restore button for dismissed items."""
    from pathlib import Path

    template = Path("app/templates/admin/shop_optional_accessories.html").read_text()
    assert "/restore" in template
    assert "Restore" in template

