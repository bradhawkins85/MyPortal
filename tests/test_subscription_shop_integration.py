from decimal import Decimal
from datetime import date, datetime

import asyncio

from app.services import subscription_shop_integration as integration


def test_create_subscriptions_from_order_applies_vip_pricing(monkeypatch):
    order_items = [{"product_id": 1, "quantity": 2, "is_vip": 1}]
    products = [
        {
            "id": 1,
            "sku": "SUB-1",
            "subscription_category_id": 10,
            "commitment_type": None,
            "payment_frequency": None,
            "price": "100.00",
            "vip_price": "50.00",
        }
    ]

    created_payload: dict = {}

    async def fake_list_order_items(order_number, company_id):
        return order_items

    async def fake_list_products_by_ids(product_ids, company_id):
        return products

    async def fake_create_subscription(**kwargs):
        created_payload.update(kwargs)
        return {"id": 123, **kwargs}

    async def fake_find_existing_item(company_id, product):
        return None

    async def fake_sync(subscription, **kwargs):
        return {"id": 1}

    monkeypatch.setattr(integration.shop_repo, "list_order_items", fake_list_order_items)
    monkeypatch.setattr(integration.shop_repo, "list_products_by_ids", fake_list_products_by_ids)
    monkeypatch.setattr(integration.subscriptions_repo, "create_subscription", fake_create_subscription)
    monkeypatch.setattr(integration.subscription_billing, "find_existing_item", fake_find_existing_item)
    monkeypatch.setattr(integration.subscription_billing, "sync_subscription_recurring_item", fake_sync)

    created = asyncio.run(
        integration.create_subscriptions_from_order(
            order_number="ORD-1",
            company_id=99,
            user_id=42,
        )
    )

    assert len(created) == 1
    assert created_payload["quantity"] == 2
    assert created_payload["unit_price"] == Decimal("50.00")


def test_create_subscriptions_adopts_existing_recurring_item(monkeypatch):
    existing = {
        "id": 77,
        "qty_expression": "8",
        "start_date": date(2026, 1, 1),
        "end_date": date(2026, 12, 31),
        "last_billed_at": datetime(2026, 8, 1),
    }
    created_payload = {}
    synced = {}

    async def order_items(*_args):
        return [{"product_id": 1, "quantity": 2, "is_vip": 0}]

    async def products(*_args, **_kwargs):
        return [{"id": 1, "sku": "SUB-1", "name": "Plan", "subscription_category_id": 10,
                 "commitment_type": "annual", "price": "30.00", "vip_price": None}]

    async def find(*_args):
        return existing

    async def create(**values):
        created_payload.update(values)
        return {"id": "sub-1", **values}

    async def sync(subscription, **values):
        synced.update(values)
        return existing

    monkeypatch.setattr(integration.shop_repo, "list_order_items", order_items)
    monkeypatch.setattr(integration.shop_repo, "list_products_by_ids", products)
    monkeypatch.setattr(integration.subscription_billing, "find_existing_item", find)
    monkeypatch.setattr(integration.subscription_billing, "sync_subscription_recurring_item", sync)
    monkeypatch.setattr(integration.subscriptions_repo, "create_subscription", create)

    created = asyncio.run(integration.create_subscriptions_from_order(
        order_number="ORD-2", company_id=99, user_id=42
    ))

    assert len(created) == 1
    assert created_payload["quantity"] == 10
    assert created_payload["start_date"] == existing["start_date"]
    assert created_payload["end_date"] == existing["end_date"]
    assert synced == {"existing": existing, "preserve_existing_schedule": True}
