from decimal import Decimal

import asyncio

from app.services import subscription_shop_integration as integration


def test_create_subscriptions_from_order_applies_vip_pricing(monkeypatch):
    order_items = [{"product_id": 1, "quantity": 2, "is_vip": 1}]
    products = [
        {
            "id": 1,
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

    monkeypatch.setattr(integration.shop_repo, "list_order_items", fake_list_order_items)
    monkeypatch.setattr(integration.shop_repo, "list_products_by_ids", fake_list_products_by_ids)
    monkeypatch.setattr(integration.subscriptions_repo, "create_subscription", fake_create_subscription)

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
