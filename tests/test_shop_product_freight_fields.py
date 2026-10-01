from __future__ import annotations

import json
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app import main
from app.features.shop import handlers
from app.repositories import freight_rules as freight_rules_repo
from app.repositories import shop as shop_repo


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


async def _receive() -> dict[str, Any]:
    return {"type": "http.request", "body": b"", "more_body": False}


def _request(path: str) -> Request:
    return Request({"type": "http", "method": "GET", "path": path, "headers": [], "query_string": b""}, _receive)


_PRODUCT = {
    "id": 7,
    "price": Decimal("100.00"),
    "subscription_category_id": None,
    "item_size": None,
    "stock_nsw": 3,
    "stock_qld": 0,
    "stock_vic": 0,
    "stock_sa": 0,
    "stock_wa": 0,
    "weight": Decimal("1.00"),
    "length": Decimal("10.00"),
    "width": Decimal("10.00"),
    "height": Decimal("10.00"),
}

_RULES = [
    {
        "id": 1,
        "name": "Large items",
        "is_default": False,
        "conditions": [{"type": "item_size", "operator": "equals", "value": "large"}],
        "freight_amount": Decimal("45.00"),
    },
    {"id": 2, "name": "Standard", "is_default": True, "conditions": [], "freight_amount": Decimal("9.95")},
]


@pytest.fixture
def preview_env(monkeypatch):
    monkeypatch.setattr(main, "_require_super_admin_page", AsyncMock(return_value=({"id": 1}, None)))
    monkeypatch.setattr(shop_repo, "get_product_by_id", AsyncMock(return_value=dict(_PRODUCT)))
    monkeypatch.setattr(freight_rules_repo, "list_rules", AsyncMock(return_value=_RULES))


async def _preview(**params: Any) -> dict[str, Any]:
    defaults = {"item_size": None, "weight": None, "length": None, "width": None, "height": None, "price": None, "quantity": 1}
    defaults.update(params)
    response = await handlers.admin_shop_product_freight_preview_api(
        _request("/api/admin/shop/products/7/freight-preview"), 7, **defaults
    )
    return json.loads(response.body)


@pytest.mark.anyio("asyncio")
async def test_freight_preview_uses_unsaved_item_size(preview_env):
    automatic = await _preview(weight="1", length="10", width="10", height="10")
    large = await _preview(item_size="large", weight="1", length="10", width="10", height="10")

    assert Decimal(str(automatic["freight_total"])) == Decimal("9.95")
    assert automatic["item_size"] == "small"
    assert automatic["item_size_is_automatic"] is True
    assert Decimal(str(large["freight_total"])) == Decimal("45.00")
    assert large["active_rule_count"] == 2


@pytest.mark.anyio("asyncio")
async def test_freight_preview_digital_delivery_is_free(preview_env):
    preview = await _preview(item_size="digital")

    assert preview["freight_exempt"] is True
    assert Decimal(str(preview["freight_total"])) == Decimal("0")


@pytest.mark.anyio("asyncio")
async def test_freight_preview_rejects_invalid_values(preview_env):
    with pytest.raises(HTTPException) as size_error:
        await _preview(item_size="gigantic")
    assert size_error.value.status_code == 400

    with pytest.raises(HTTPException) as weight_error:
        await _preview(weight="-1")
    assert weight_error.value.status_code == 400


def test_parse_product_freight_fields_normalises_values():
    parsed = handlers._parse_product_freight_fields(
        item_size="Digital", weight="2.345", length="", width=None, height="30"
    )

    assert parsed == {
        "item_size": "digital",
        "weight": Decimal("2.35"),
        "length": None,
        "width": None,
        "height": Decimal("30.00"),
    }
    assert handlers._parse_product_freight_fields(
        item_size="", weight="", length="", width="", height=""
    )["item_size"] is None


class _FakeCursor:
    def __init__(self, calls: list[tuple[str, tuple[Any, ...]]]):
        self.calls = calls

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def execute(self, sql: str, params: tuple[Any, ...]) -> None:
        self.calls.append((sql, params))


class _FakeConn:
    def __init__(self, calls):
        self.calls = calls

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def cursor(self, *_args):
        return _FakeCursor(self.calls)


async def _run_update(monkeypatch, **extra: Any) -> tuple[str, tuple[Any, ...]]:
    calls: list[tuple[str, tuple[Any, ...]]] = []
    monkeypatch.setattr(shop_repo.db, "acquire", lambda: _FakeConn(calls))
    monkeypatch.setattr(shop_repo, "replace_product_recommendations", AsyncMock())
    monkeypatch.setattr(shop_repo, "get_product_by_id", AsyncMock(return_value={"id": 7}))
    await shop_repo.update_product(
        7,
        name="Widget",
        sku="W-1",
        vendor_sku="V-1",
        description=None,
        price=Decimal("10.00"),
        stock=1,
        vip_price=None,
        category_id=None,
        image_url=None,
        cross_sell_product_ids=None,
        upsell_product_ids=None,
        **extra,
    )
    assert len(calls) == 1
    return calls[0]


@pytest.mark.anyio("asyncio")
async def test_update_product_writes_freight_fields_only_when_supplied(monkeypatch):
    sql, params = await _run_update(monkeypatch)
    assert "item_size" not in sql
    assert sql.count("%s") == len(params)

    freight = {
        "item_size": "digital",
        "weight": Decimal("1.50"),
        "length": None,
        "width": Decimal("20.00"),
        "height": Decimal("5.00"),
    }
    sql, params = await _run_update(monkeypatch, freight=freight)
    for column in ("item_size", "weight", "length", "width", "height"):
        assert f"{column} = %s" in sql
    assert sql.count("%s") == len(params)
    assert params[:5] == ("digital", Decimal("1.50"), None, Decimal("20.00"), Decimal("5.00"))
    assert params[-1] == 7
