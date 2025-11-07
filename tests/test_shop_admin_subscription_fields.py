"""Tests for subscription category and term days fields in product management."""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi import status
from starlette.requests import Request

from app import main


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


async def _dummy_receive() -> dict[str, Any]:
    return {"type": "http.request", "body": b"", "more_body": False}


def _make_request(path: str = "/shop/admin/product") -> Request:
    scope = {
        "type": "http",
        "method": "POST",
        "path": path,
        "headers": [],
    }
    return Request(scope, _dummy_receive)


@pytest.mark.anyio("asyncio")
async def test_admin_create_shop_product_with_subscription_fields(monkeypatch):
    """Test creating a product with subscription category and term days."""
    request = _make_request()

    current_user = {"id": 42}
    monkeypatch.setattr(
        main,
        "_require_super_admin_page",
        AsyncMock(return_value=(current_user, None)),
    )

    # Mock subscription category validation
    subscription_category = {"id": 5, "name": "Microsoft 365"}
    monkeypatch.setattr(
        main.subscription_categories_repo,
        "get_category",
        AsyncMock(return_value=subscription_category),
    )

    create_mock = AsyncMock(return_value={"id": 1, "sku": "SKU-1", "vendor_sku": "VENDOR-1"})
    monkeypatch.setattr(main.shop_repo, "create_product", create_mock)

    response = await main.admin_create_shop_product(
        request,
        name="Microsoft 365 Business Basic",
        sku="SKU-1",
        vendor_sku="VENDOR-1",
        price="19.95",
        stock="100",
        vip_price="",
        category_id="",
        image=None,
        cross_sell_product_ids=None,
        upsell_product_ids=None,
        subscription_category_id="5",
        term_days="365",
    )

    assert response.status_code == status.HTTP_303_SEE_OTHER
    assert response.headers["location"] == "/admin/shop"

    create_mock.assert_awaited_once()
    call_kwargs = create_mock.await_args.kwargs
    assert call_kwargs["subscription_category_id"] == 5
    assert call_kwargs["term_days"] == 365


@pytest.mark.anyio("asyncio")
async def test_admin_update_shop_product_with_subscription_fields(monkeypatch):
    """Test updating a product with subscription category and term days."""
    request = _make_request("/shop/admin/product/1")

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

    # Mock subscription category validation
    subscription_category = {"id": 3, "name": "Adobe Creative Cloud"}
    monkeypatch.setattr(
        main.subscription_categories_repo,
        "get_category",
        AsyncMock(return_value=subscription_category),
    )

    update_mock = AsyncMock(return_value={"id": 1, "image_url": None})
    monkeypatch.setattr(main.shop_repo, "update_product", update_mock)

    resolver_mock = AsyncMock(return_value=None)
    monkeypatch.setattr(main, "_resolve_related_product_id_by_sku", resolver_mock)

    response = await main.admin_update_shop_product(
        request,
        product_id=1,
        name="Adobe Photoshop",
        sku="SKU-PHOTO",
        vendor_sku="VENDOR-PHOTO",
        description="Photo editing software",
        price="29.99",
        stock="50",
        vip_price="",
        category_id="",
        image=None,
        features=None,
        cross_sell_product_ids=None,
        upsell_product_ids=None,
        cross_sell_sku=None,
        upsell_sku=None,
        subscription_category_id="3",
        term_days="365",
    )

    assert response.status_code == status.HTTP_303_SEE_OTHER
    assert response.headers["location"] == "/admin/shop"

    update_mock.assert_awaited_once()
    call_args = update_mock.await_args
    # First positional arg is product_id
    assert call_args.args[0] == 1
    assert call_args.kwargs["subscription_category_id"] == 3
    assert call_args.kwargs["term_days"] == 365


@pytest.mark.anyio("asyncio")
async def test_admin_create_shop_product_defaults_term_days(monkeypatch):
    """Test that term_days defaults to 365 when not provided."""
    request = _make_request()

    current_user = {"id": 42}
    monkeypatch.setattr(
        main,
        "_require_super_admin_page",
        AsyncMock(return_value=(current_user, None)),
    )

    create_mock = AsyncMock(return_value={"id": 1, "sku": "SKU-1", "vendor_sku": "VENDOR-1"})
    monkeypatch.setattr(main.shop_repo, "create_product", create_mock)

    response = await main.admin_create_shop_product(
        request,
        name="Standard Product",
        sku="SKU-1",
        vendor_sku="VENDOR-1",
        price="19.95",
        stock="100",
        vip_price="",
        category_id="",
        image=None,
        cross_sell_product_ids=None,
        upsell_product_ids=None,
        subscription_category_id="",
        term_days="365",  # Default value
    )

    assert response.status_code == status.HTTP_303_SEE_OTHER

    create_mock.assert_awaited_once()
    call_kwargs = create_mock.await_args.kwargs
    assert call_kwargs["subscription_category_id"] is None
    assert call_kwargs["term_days"] == 365
