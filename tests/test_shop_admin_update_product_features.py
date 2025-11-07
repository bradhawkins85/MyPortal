from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException, status
from starlette.requests import Request

from app import main


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


async def _dummy_receive() -> dict[str, Any]:
    return {"type": "http.request", "body": b"", "more_body": False}


def _make_request(path: str = "/shop/admin/product/1") -> Request:
    scope = {
        "type": "http",
        "method": "POST",
        "path": path,
        "headers": [],
    }
    return Request(scope, _dummy_receive)


@pytest.mark.anyio("asyncio")
async def test_admin_update_shop_product_updates_features(monkeypatch):
    request = _make_request()

    current_user = {"id": 42}
    monkeypatch.setattr(
        main,
        "_require_super_admin_page",
        AsyncMock(return_value=(current_user, None)),
    )

    monkeypatch.setattr(
        main.shop_repo,
        "get_product_by_id",
        AsyncMock(return_value={"id": 1, "image_url": None}),
    )

    update_mock = AsyncMock(return_value={"image_url": None})
    monkeypatch.setattr(main.shop_repo, "update_product", update_mock)

    replace_mock = AsyncMock()
    monkeypatch.setattr(main.shop_repo, "replace_product_features", replace_mock)

    resolver_mock = AsyncMock(return_value=None)
    monkeypatch.setattr(main, "_resolve_related_product_id_by_sku", resolver_mock)

    response = await main.admin_update_shop_product(
        request,
        product_id=1,
        name="Widget",
        sku="SKU-1",
        vendor_sku="VENDOR-1",
        description="Specs",
        price="19.95",
        stock="5",
        vip_price="",
        category_id="",
        image=None,
        cross_sell_sku=None,
        upsell_sku=None,
        subscription_category_id="",
        term_days="365",
        features=json.dumps(
            [
                {"name": "Colour", "value": "Blue"},
                {"name": "Capacity", "value": "256GB"},
            ]
        ),
    )

    assert response.status_code == status.HTTP_303_SEE_OTHER
    assert response.headers["location"] == "/admin/shop"

    replace_mock.assert_awaited_once()
    awaited_args = replace_mock.await_args.args
    assert awaited_args[0] == 1
    assert awaited_args[1] == [
        {"name": "Colour", "value": "Blue", "position": 0},
        {"name": "Capacity", "value": "256GB", "position": 1},
    ]


@pytest.mark.anyio("asyncio")
async def test_admin_update_shop_product_invalid_feature_json(monkeypatch):
    request = _make_request()

    monkeypatch.setattr(
        main,
        "_require_super_admin_page",
        AsyncMock(return_value=({"id": 1}, None)),
    )
    monkeypatch.setattr(
        main.shop_repo,
        "get_product_by_id",
        AsyncMock(return_value={"id": 1}),
    )

    with pytest.raises(HTTPException) as exc:
        await main.admin_update_shop_product(
            request,
            product_id=1,
            name="Widget",
            sku="SKU",
            vendor_sku="VENDOR",
            description="Specs",
            price="10",
            stock="1",
            vip_price="",
            category_id="",
            image=None,
            cross_sell_sku=None,
            upsell_sku=None,
            subscription_category_id="",
            term_days="365",
            features="not-json",
        )

    assert exc.value.status_code == status.HTTP_400_BAD_REQUEST


@pytest.mark.anyio("asyncio")
async def test_admin_update_shop_product_rejects_blank_feature_name(monkeypatch):
    request = _make_request()

    monkeypatch.setattr(
        main,
        "_require_super_admin_page",
        AsyncMock(return_value=({"id": 1}, None)),
    )
    monkeypatch.setattr(
        main.shop_repo,
        "get_product_by_id",
        AsyncMock(return_value={"id": 1}),
    )

    with pytest.raises(HTTPException) as exc:
        await main.admin_update_shop_product(
            request,
            product_id=1,
            name="Widget",
            sku="SKU",
            vendor_sku="VENDOR",
            description="Specs",
            price="10",
            stock="1",
            vip_price="",
            category_id="",
            image=None,
            cross_sell_sku=None,
            upsell_sku=None,
            subscription_category_id="",
            term_days="365",
            features=json.dumps([
                {"name": " ", "value": "example"},
            ]),
        )

    assert exc.value.status_code == status.HTTP_400_BAD_REQUEST
    assert "Feature name" in exc.value.detail
