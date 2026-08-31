import asyncio
from datetime import date, datetime
from decimal import Decimal

import pytest

from app.services import subscription_billing
from app.services.subscription_billing import recurring_quantity


def test_recurring_quantity_accepts_whole_numeric_expression():
    assert recurring_quantity({"id": 1, "qty_expression": "12.00"}) == 12


@pytest.mark.parametrize("expression", ["licenses", "2.5", "-1"])
def test_recurring_quantity_rejects_unsafe_expression(expression):
    with pytest.raises(ValueError):
        recurring_quantity({"id": 1, "qty_expression": expression})


def test_adopted_item_keeps_schedule_and_billing_history(monkeypatch):
    captured = {}
    existing = {
        "id": 9,
        "start_date": date(2026, 1, 1),
        "end_date": date(2026, 12, 31),
        "last_billed_at": datetime(2026, 8, 1),
    }

    async def product(_product_id):
        return {"id": 3, "sku": "SUB-1", "name": "Existing plan"}

    async def update(item_id, **values):
        captured["item_id"] = item_id
        captured.update(values)
        return {**existing, **values}

    monkeypatch.setattr(subscription_billing.shop_repo, "get_product_by_id", product)
    monkeypatch.setattr(
        subscription_billing.recurring_items_repo,
        "update_recurring_invoice_item",
        update,
    )

    asyncio.run(subscription_billing.sync_subscription_recurring_item(
        {
            "customer_id": 4,
            "product_id": 3,
            "quantity": 10,
            "unit_price": Decimal("27.50"),
            "status": "active",
            "start_date": date(2026, 8, 31),
            "end_date": date(2027, 8, 30),
        },
        existing=existing,
        preserve_existing_schedule=True,
    ))

    assert captured["item_id"] == 9
    assert captured["qty_expression"] == "10"
    assert captured["price_override"] == 27.5
    assert "start_date" not in captured
    assert "end_date" not in captured
    assert "last_billed_at" not in captured
