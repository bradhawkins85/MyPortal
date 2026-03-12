"""Tests that the /admin/shop route stores profit as float, not Decimal.

The ``calculate_profit`` helper returns ``Decimal | None``.  Values stored in
the product dict must be ``float`` (or ``None``) so that ``products | tojson``
in the template can serialise them without raising a ``TypeError``.
"""
from __future__ import annotations

import json
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from app import main


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _make_product(**kwargs: Any) -> dict[str, Any]:
    """Return a minimal normalised product dict."""
    defaults: dict[str, Any] = {
        "id": 1,
        "name": "Widget",
        "sku": "W-1",
        "vendor_sku": "V-1",
        "description": None,
        "price": 20.0,
        "vip_price": 15.0,
        "buy_price": 10.0,
        "category_id": None,
        "category_name": None,
        "archived": False,
        "stock": 5,
        "stock_nsw": 0,
        "stock_qld": 0,
        "stock_vic": 0,
        "stock_sa": 0,
        "stock_at": None,
        "commitment_type": None,
        "payment_frequency": None,
        "price_monthly_commitment": None,
        "price_annual_monthly_payment": None,
        "price_annual_annual_payment": None,
        "weight": None,
        "length": None,
        "width": None,
        "height": None,
        "image_url": None,
        "subscription_category_id": None,
        "term_days": None,
        "features": [],
        "cross_sell_products": [],
        "cross_sell_product_ids": [],
        "upsell_products": [],
        "upsell_product_ids": [],
    }
    defaults.update(kwargs)
    return defaults


async def _dummy_receive() -> dict[str, Any]:
    return {"type": "http.request", "body": b"", "more_body": False}


def _make_request(path: str = "/admin/shop") -> Any:
    from starlette.requests import Request

    scope = {
        "type": "http",
        "method": "GET",
        "path": path,
        "query_string": b"",
        "headers": [],
    }
    return Request(scope, _dummy_receive)


def _patch_shop_repos(monkeypatch: Any, product: dict[str, Any]) -> None:
    """Patch all repository calls required by admin_shop_page."""
    monkeypatch.setattr(
        main.shop_repo, "list_all_categories_flat", AsyncMock(return_value=[])
    )
    monkeypatch.setattr(
        main.shop_repo, "list_products", AsyncMock(return_value=[product])
    )
    monkeypatch.setattr(
        main.shop_repo, "list_product_restrictions", AsyncMock(return_value=[])
    )
    monkeypatch.setattr(
        main.company_repo, "list_companies", AsyncMock(return_value=[])
    )
    monkeypatch.setattr(
        main.subscription_categories_repo,
        "list_categories",
        AsyncMock(return_value=[]),
    )


def _make_capture_render(captured: dict[str, Any]) -> Any:
    """Return a fake _render_template that stores the extra context."""
    from starlette.responses import HTMLResponse

    async def fake_render(_tpl: str, _req: Any, _user: Any, *, extra: Any = None) -> Any:
        captured["extra"] = extra
        return HTMLResponse("<html></html>")

    return fake_render


@pytest.mark.anyio("asyncio")
async def test_admin_shop_page_profit_is_float(monkeypatch):
    """profit and vip_profit must be float (or None) — not Decimal."""
    request = _make_request()

    monkeypatch.setattr(
        main,
        "_require_super_admin_page",
        AsyncMock(return_value=({"id": 1}, None)),
    )

    _patch_shop_repos(monkeypatch, _make_product(price=20.0, vip_price=15.0, buy_price=10.0))

    captured: dict[str, Any] = {}
    monkeypatch.setattr(main, "_render_template", _make_capture_render(captured))

    await main.admin_shop_page(request)

    assert "extra" in captured, "Template was not rendered"
    products = captured["extra"]["products"]
    assert len(products) == 1
    p = products[0]

    # profit and vip_profit must be float or None — never Decimal
    assert isinstance(p["profit"], float), (
        f"profit should be float, got {type(p['profit'])}"
    )
    assert isinstance(p["vip_profit"], float), (
        f"vip_profit should be float, got {type(p['vip_profit'])}"
    )

    # Values must be JSON-serialisable (no Decimal)
    try:
        json.dumps(products)
    except TypeError as exc:
        pytest.fail(f"products list is not JSON-serialisable: {exc}")


@pytest.mark.anyio("asyncio")
async def test_admin_shop_page_profit_none_when_no_buy_price(monkeypatch):
    """When a product has no buy_price, profit and vip_profit should be None."""
    request = _make_request()

    monkeypatch.setattr(
        main,
        "_require_super_admin_page",
        AsyncMock(return_value=({"id": 1}, None)),
    )

    _patch_shop_repos(monkeypatch, _make_product(price=20.0, vip_price=15.0, buy_price=None))

    captured: dict[str, Any] = {}
    monkeypatch.setattr(main, "_render_template", _make_capture_render(captured))

    await main.admin_shop_page(request)

    p = captured["extra"]["products"][0]

    assert p["profit"] is None
    assert p["vip_profit"] is None

    # Still JSON-serialisable
    try:
        json.dumps(captured["extra"]["products"])
    except TypeError as exc:
        pytest.fail(f"products list is not JSON-serialisable: {exc}")
