"""Shop subscriptions show an end date only once one is known."""
from datetime import date

import pytest

from app.services import subscription_billing

TERM_END = date(2027, 1, 31)


def _shop_subscription(**overrides):
    subscription = {
        "id": "sub-1",
        "customer_id": 10,
        "product_id": 1,
        "product_sku": "SKU-A",
        "quantity": 5,
        "status": "active",
        "auto_renew": True,
        "end_date": TERM_END,
    }
    subscription.update(overrides)
    return subscription


def test_auto_renewing_shop_subscription_has_no_end_date():
    assert subscription_billing.display_end_date(_shop_subscription()) is None


def test_partial_decrease_keeps_subscription_renewing():
    assert (
        subscription_billing.display_end_date(_shop_subscription(), pending_decrease=4)
        is None
    )


def test_decrease_to_zero_shows_term_end_date():
    assert (
        subscription_billing.display_end_date(_shop_subscription(), pending_decrease=5)
        == TERM_END
    )


def test_admin_end_date_on_recurring_item_is_shown():
    item = {"end_date": date(2026, 12, 15)}
    assert subscription_billing.display_end_date(
        _shop_subscription(), recurring_item=item
    ) == date(2026, 12, 15)


@pytest.mark.parametrize(
    "overrides",
    [{"auto_renew": False}, {"status": "canceled"}, {"status": "expired"}],
)
def test_non_renewing_shop_subscription_shows_end_date(overrides):
    assert (
        subscription_billing.display_end_date(_shop_subscription(**overrides))
        == TERM_END
    )


def test_external_subscription_keeps_recorded_end_date():
    subscription = _shop_subscription(product_id=None, product_sku=None)
    assert subscription_billing.display_end_date(subscription) == TERM_END


@pytest.mark.asyncio
async def test_resolve_matches_recurring_item_by_company_and_sku(monkeypatch):
    async def fake_list_items(company_ids):
        assert set(company_ids) == {10, 11}
        return [
            {"company_id": 11, "product_code": "SKU-A", "end_date": date(2026, 6, 1)},
            {"company_id": 10, "product_code": " sku-a", "end_date": None},
            {"company_id": 10, "product_code": "SKU-A", "end_date": date(2026, 1, 1)},
        ]

    monkeypatch.setattr(
        subscription_billing.recurring_items_repo,
        "list_items_for_company_ids",
        fake_list_items,
    )
    subscriptions = [
        _shop_subscription(),
        _shop_subscription(id="sub-2", customer_id=11),
        _shop_subscription(id="sub-3", product_sku=""),
    ]
    pending = {"sub-3": [{"change_type": "decrease", "quantity_change": 5}]}

    result = await subscription_billing.resolve_display_end_dates(subscriptions, pending)

    # The first match per company and SKU is the preferred (active, latest) item.
    assert result == {"sub-1": None, "sub-2": date(2026, 6, 1), "sub-3": TERM_END}
