"""New subscriptions default to the "Subscription" store category."""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest
from starlette.requests import Request

from app import main
from app.features.shop import handlers as shop_handlers
from app.repositories import shop as shop_repo
from app.repositories import subscription_categories as subscription_categories_repo


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


async def _receive() -> dict[str, Any]:
    return {"type": "http.request", "body": b"", "more_body": False}


@pytest.fixture
def create_product(monkeypatch):
    monkeypatch.setattr(
        main, "_require_super_admin_page", AsyncMock(return_value=({"id": 42}, None))
    )
    monkeypatch.setattr(
        subscription_categories_repo,
        "get_category",
        AsyncMock(return_value={"id": 5, "name": "Microsoft 365"}),
    )
    mock = AsyncMock(return_value={"id": 1, "sku": "SKU-1", "vendor_sku": "VENDOR-1"})
    monkeypatch.setattr(shop_repo, "create_product", mock)
    return mock


async def _create(*, category_id: str | None = "", subscription_category_id: str | None = "5"):
    request = Request(
        {"type": "http", "method": "POST", "path": "/shop/admin/product", "headers": []},
        _receive,
    )
    return await shop_handlers.admin_create_shop_product(
        request,
        name="Microsoft 365 Business Basic",
        sku="SKU-1",
        vendor_sku="VENDOR-1",
        microsoft_sku=None,
        description=None,
        invoice_description=None,
        product_link=None,
        price="0",
        stock="1",
        vip_price=None,
        category_id=category_id,
        image=None,
        features=None,
        cross_sell_product_ids=None,
        upsell_product_ids=None,
        subscription_category_id=subscription_category_id,
        commitment_type=None,
        payment_frequency=None,
        price_monthly_commitment="10.00",
        price_annual_monthly_payment=None,
        price_annual_annual_payment=None,
        voice_monitor_calls_per_day=None,
    )


@pytest.mark.anyio("asyncio")
async def test_new_subscription_defaults_to_subscription_store_category(
    monkeypatch, create_product
):
    lookup = AsyncMock(return_value={"id": 77, "name": "Subscription"})
    monkeypatch.setattr(shop_repo, "get_category_by_name", lookup)

    await _create()

    lookup.assert_awaited_once_with("Subscription")
    assert create_product.await_args.kwargs["category_id"] == 77


@pytest.mark.anyio("asyncio")
async def test_new_subscription_uses_existing_subscriptions_category(
    monkeypatch, create_product
):
    monkeypatch.setattr(
        shop_repo,
        "get_category_by_name",
        AsyncMock(side_effect=[None, {"id": 12, "name": "Subscriptions"}]),
    )
    create_category = AsyncMock(return_value=99)
    monkeypatch.setattr(shop_repo, "create_category", create_category)

    await _create()

    assert create_product.await_args.kwargs["category_id"] == 12
    create_category.assert_not_awaited()


@pytest.mark.anyio("asyncio")
async def test_new_subscription_creates_subscription_category_when_missing(
    monkeypatch, create_product
):
    monkeypatch.setattr(shop_repo, "get_category_by_name", AsyncMock(return_value=None))
    create_category = AsyncMock(return_value=99)
    monkeypatch.setattr(shop_repo, "create_category", create_category)

    await _create()

    create_category.assert_awaited_once_with("Subscription")
    assert create_product.await_args.kwargs["category_id"] == 99


@pytest.mark.anyio("asyncio")
async def test_explicit_store_category_is_kept(monkeypatch, create_product):
    monkeypatch.setattr(
        shop_repo, "get_category", AsyncMock(return_value={"id": 3, "name": "Cloud"})
    )
    lookup = AsyncMock()
    monkeypatch.setattr(shop_repo, "get_category_by_name", lookup)

    await _create(category_id="3")

    assert create_product.await_args.kwargs["category_id"] == 3
    lookup.assert_not_awaited()


@pytest.mark.anyio("asyncio")
async def test_regular_product_without_category_is_unchanged(monkeypatch, create_product):
    lookup = AsyncMock()
    monkeypatch.setattr(shop_repo, "get_category_by_name", lookup)

    await _create(subscription_category_id=None)

    assert create_product.await_args.kwargs["category_id"] is None
    lookup.assert_not_awaited()
