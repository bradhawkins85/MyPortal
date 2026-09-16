import asyncio
from datetime import date
from decimal import Decimal

from app.api.routes import subscriptions as routes


def test_create_existing_subscription_enters_normal_renewal_without_billing(monkeypatch):
    captured = {}

    async def product(_product_id):
        return {
            "id": 8,
            "subscription_category_id": 3,
            "commitment_type": "annual",
            "payment_frequency": "annual",
            "price_annual_annual_payment": Decimal("1200.00"),
        }

    async def existing(**_kwargs):
        return []

    async def create(**values):
        captured.update(values)
        return {
            "id": "external-subscription",
            **values,
            "prorated_price": None,
            "product_name": "External plan",
            "category_name": "Managed services",
            "created_at": None,
            "updated_at": None,
        }

    monkeypatch.setattr(routes.shop_repo, "get_product_by_id", product)
    monkeypatch.setattr(routes.subscriptions_repo, "list_subscriptions", existing)
    monkeypatch.setattr(routes.subscriptions_repo, "create_subscription", create)

    response = asyncio.run(routes.create_existing_subscription(
        routes.CreateExistingSubscriptionRequest(
            customerId=2,
            productId=8,
            startDate=date(2026, 1, 15),
            quantity=4,
        ),
        None,
        {"id": 1, "is_super_admin": True},
    ))

    assert response.id == "external-subscription"
    assert captured["start_date"] == date(2026, 1, 15)
    assert captured["end_date"] == date(2027, 1, 15)
    assert captured["status"] == "active"
    assert captured["auto_renew"] is True
