"""Tests for description field in product creation."""
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
async def test_admin_create_shop_product_with_description(monkeypatch):
    """Test creating a product with a description."""
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
        name="Test Product",
        sku="SKU-1",
        vendor_sku="VENDOR-1",
        description="This is a test product description",
        price="19.95",
        stock="100",
        vip_price="",
        category_id="",
        image=None,
        cross_sell_product_ids=None,
        upsell_product_ids=None,
        subscription_category_id="",
        term_days="365",
    )

    assert response.status_code == status.HTTP_303_SEE_OTHER
    assert response.headers["location"] == "/admin/shop"

    create_mock.assert_awaited_once()
    call_kwargs = create_mock.await_args.kwargs
    assert call_kwargs["description"] == "This is a test product description"


@pytest.mark.anyio("asyncio")
async def test_admin_create_shop_product_without_description(monkeypatch):
    """Test creating a product without a description (should accept None)."""
    request = _make_request()

    current_user = {"id": 42}
    monkeypatch.setattr(
        main,
        "_require_super_admin_page",
        AsyncMock(return_value=(current_user, None)),
    )

    create_mock = AsyncMock(return_value={"id": 2, "sku": "SKU-2", "vendor_sku": "VENDOR-2"})
    monkeypatch.setattr(main.shop_repo, "create_product", create_mock)

    response = await main.admin_create_shop_product(
        request,
        name="Test Product",
        sku="SKU-2",
        vendor_sku="VENDOR-2",
        description=None,
        price="19.95",
        stock="100",
        vip_price="",
        category_id="",
        image=None,
        cross_sell_product_ids=None,
        upsell_product_ids=None,
        subscription_category_id="",
        term_days="365",
    )

    assert response.status_code == status.HTTP_303_SEE_OTHER

    create_mock.assert_awaited_once()
    call_kwargs = create_mock.await_args.kwargs
    assert call_kwargs["description"] is None


@pytest.mark.anyio("asyncio")
async def test_admin_create_shop_product_with_empty_description(monkeypatch):
    """Test creating a product with an empty string description (should convert to None)."""
    request = _make_request()

    current_user = {"id": 42}
    monkeypatch.setattr(
        main,
        "_require_super_admin_page",
        AsyncMock(return_value=(current_user, None)),
    )

    create_mock = AsyncMock(return_value={"id": 3, "sku": "SKU-3", "vendor_sku": "VENDOR-3"})
    monkeypatch.setattr(main.shop_repo, "create_product", create_mock)

    response = await main.admin_create_shop_product(
        request,
        name="Test Product",
        sku="SKU-3",
        vendor_sku="VENDOR-3",
        description="   ",  # Empty whitespace
        price="19.95",
        stock="100",
        vip_price="",
        category_id="",
        image=None,
        cross_sell_product_ids=None,
        upsell_product_ids=None,
        subscription_category_id="",
        term_days="365",
    )

    assert response.status_code == status.HTTP_303_SEE_OTHER

    create_mock.assert_awaited_once()
    call_kwargs = create_mock.await_args.kwargs
    # Empty whitespace should be converted to None
    assert call_kwargs["description"] is None
