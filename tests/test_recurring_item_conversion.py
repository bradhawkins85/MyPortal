import asyncio
from datetime import date, datetime
from decimal import Decimal

import pytest

from app.services import recurring_item_conversion as conversion
from app.services import subscription_billing

PRODUCT = {
    "id": 3,
    "sku": "M365-BP",
    "name": "Microsoft 365 Business Premium",
    "subscription_category_id": 7,
    "commitment_type": "monthly",
    "payment_frequency": "monthly",
    "price": Decimal("30.00"),
    "price_monthly_commitment": Decimal("33.00"),
}


def _item(**overrides):
    item = {
        "id": 11,
        "company_id": 4,
        "product_code": "m365-bp ",
        "description_template": "M365 Business Premium for {company_name}",
        "qty_expression": "12",
        "price_override": 29.5,
        "active": 1,
        "billing_frequency": "monthly",
        "billing_interval": None,
        "start_date": date(2025, 3, 15),
        "end_date": None,
        "last_billed_at": datetime(2026, 11, 15, 2, 0),
    }
    item.update(overrides)
    return item


def test_add_months_clamps_to_month_end():
    assert conversion.add_months(date(2026, 1, 31), 1) == date(2026, 2, 28)
    assert conversion.add_months(date(2026, 11, 15), -12) == date(2025, 11, 15)


@pytest.mark.parametrize(
    ("item", "months", "expected"),
    [
        (_item(), 1, date(2026, 12, 15)),
        (_item(last_billed_at=datetime(2026, 10, 20)), 1, date(2026, 11, 20)),
        (_item(billing_frequency="yearly", last_billed_at=datetime(2026, 3, 15)), 12, date(2027, 3, 15)),
        (_item(last_billed_at=None, start_date=date(2027, 1, 1)), 1, date(2027, 1, 1)),
        (_item(last_billed_at=None, start_date=None), 1, date(2026, 11, 20)),
    ],
)
def test_next_charge_date(item, months, expected):
    assert conversion.calculate_next_charge_date(item, months, today=date(2026, 11, 20)) == expected


def test_overdue_item_rolls_forward_to_the_first_unbilled_period():
    item = _item(last_billed_at=datetime(2026, 8, 15))
    # Sep, Oct, Nov 15 have passed; first boundary on/after today.
    assert conversion.calculate_next_charge_date(item, 1, today=date(2026, 11, 16)) == date(2026, 12, 15)


def test_monthly_plan_term_ends_day_before_next_charge():
    plan = conversion.build_plan(_item(), PRODUCT, today=date(2026, 11, 20))
    assert plan.next_charge_date == date(2026, 12, 15)
    assert plan.term_start_date == date(2026, 11, 15)
    assert plan.term_end_date == date(2026, 12, 14)
    assert plan.description == "M365 Business Premium for {company_name}"
    assert plan.quantity == 12
    assert plan.unit_price == Decimal("29.5")
    assert plan.payment_frequency == "monthly"


def test_annual_commitment_paid_monthly_follows_start_anniversary():
    product = {**PRODUCT, "commitment_type": "annual", "payment_frequency": "monthly"}
    plan = conversion.build_plan(_item(), product, today=date(2026, 11, 20))
    assert plan.next_charge_date == date(2026, 12, 15)
    assert plan.term_start_date == date(2026, 3, 15)
    assert plan.term_end_date == date(2027, 3, 14)


def test_annual_plan_uses_product_price_without_override():
    product = {**PRODUCT, "commitment_type": "annual", "payment_frequency": "annual",
               "price_annual_annual_payment": Decimal("360.00")}
    item = _item(price_override=None, billing_frequency="yearly", last_billed_at=datetime(2026, 3, 15))
    plan = conversion.build_plan(item, product, today=date(2026, 11, 20))
    assert plan.next_charge_date == date(2027, 3, 15)
    assert plan.term_start_date == date(2026, 3, 15)
    assert plan.term_end_date == date(2027, 3, 14)
    assert plan.payment_frequency == "annual"


def _patch_repos(monkeypatch, *, item, active_product_ids=frozenset(), product=PRODUCT):
    captured: dict = {}

    async def get_item(item_id):
        return item if item_id == item["id"] else None

    async def get_by_sku(sku, **_kwargs):
        return dict(product) if product and sku.strip().lower() == product["sku"].lower() else None

    async def active_ids(_company_id):
        return set(active_product_ids)

    async def create_subscription(**values):
        captured["subscription"] = values
        return {"id": "sub-1", **values}

    async def update_item(item_id, **values):
        captured["item_update"] = (item_id, values)
        return {**item, **values}

    monkeypatch.setattr(conversion.recurring_items_repo, "get_recurring_invoice_item", get_item)
    monkeypatch.setattr(conversion.recurring_items_repo, "update_recurring_invoice_item", update_item)
    monkeypatch.setattr(conversion.shop_repo, "get_product_by_sku", get_by_sku)
    monkeypatch.setattr(conversion.subscriptions_repo, "get_active_subscription_product_ids", active_ids)
    monkeypatch.setattr(conversion.subscriptions_repo, "create_subscription", create_subscription)
    return captured


def test_convert_creates_subscription_and_aligns_item(monkeypatch):
    item = _item()
    captured = _patch_repos(monkeypatch, item=item)

    subscription, updated = asyncio.run(
        conversion.convert_item(4, 11, user_id=1, today=date(2026, 11, 20))
    )

    sub = captured["subscription"]
    assert sub["customer_id"] == 4
    assert sub["product_id"] == 3
    assert sub["subscription_category_id"] == 7
    assert sub["start_date"] == date(2026, 11, 15)
    assert sub["end_date"] == date(2026, 12, 14)
    assert sub["quantity"] == 12
    assert sub["external_name"] == "M365 Business Premium for {company_name}"
    assert sub["billing_frequency"] == "monthly"
    assert sub["auto_renew"] is True
    item_id, values = captured["item_update"]
    assert item_id == 11
    assert values["start_date"] == date(2026, 12, 15)
    assert values["description_template"] == item["description_template"]
    assert values["billing_frequency"] == "monthly"
    assert values["clear_end_date"] is True
    assert subscription["id"] == "sub-1"
    assert updated["start_date"] == date(2026, 12, 15)


def test_convert_requires_quantity_for_variable_expressions(monkeypatch):
    _patch_repos(monkeypatch, item=_item(qty_expression="{active_users}"))
    with pytest.raises(conversion.ConversionError):
        asyncio.run(conversion.convert_item(4, 11, user_id=1, today=date(2026, 11, 20)))


def test_convert_accepts_quantity_and_next_charge_override(monkeypatch):
    captured = _patch_repos(monkeypatch, item=_item(qty_expression="{active_users}"))
    asyncio.run(conversion.convert_item(
        4, 11, user_id=1, today=date(2026, 11, 20),
        next_charge_date=date(2027, 1, 1), quantity=5,
    ))
    assert captured["subscription"]["quantity"] == 5
    assert captured["subscription"]["end_date"] == date(2026, 12, 31)
    assert captured["item_update"][1]["start_date"] == date(2027, 1, 1)


def test_convert_rejects_past_next_charge_date(monkeypatch):
    _patch_repos(monkeypatch, item=_item())
    with pytest.raises(conversion.ConversionError):
        asyncio.run(conversion.convert_item(
            4, 11, user_id=1, today=date(2026, 11, 20), next_charge_date=date(2026, 11, 1),
        ))


def test_item_from_another_company_is_not_found(monkeypatch):
    _patch_repos(monkeypatch, item=_item(company_id=99))
    with pytest.raises(LookupError):
        asyncio.run(conversion.preview_conversion(4, 11, today=date(2026, 11, 20)))


def test_items_with_active_subscription_or_no_match_are_not_convertible(monkeypatch):
    _patch_repos(monkeypatch, item=_item(), active_product_ids={3})
    with pytest.raises(conversion.ConversionError):
        asyncio.run(conversion.preview_conversion(4, 11, today=date(2026, 11, 20)))
    assert asyncio.run(conversion.list_convertible_items(4, [_item()])) == {}


def test_non_subscription_product_is_not_convertible(monkeypatch):
    _patch_repos(monkeypatch, item=_item(), product={**PRODUCT, "subscription_category_id": None})
    assert asyncio.run(conversion.list_convertible_items(4, [_item()])) == {}


def test_list_convertible_items_maps_matches(monkeypatch):
    _patch_repos(monkeypatch, item=_item())
    other = _item(id=12, product_code="LABOUR")
    result = asyncio.run(conversion.list_convertible_items(4, [_item(), other]))
    assert result == {11: {"product_id": 3, "product_name": PRODUCT["name"]}}


def test_sync_keeps_converted_description(monkeypatch):
    captured = {}

    async def product(_product_id):
        return {"id": 3, "sku": "M365-BP", "name": "Product name"}

    async def update(item_id, **values):
        captured.update(values)
        return values

    monkeypatch.setattr(subscription_billing.shop_repo, "get_product_by_id", product)
    monkeypatch.setattr(subscription_billing.recurring_items_repo, "update_recurring_invoice_item", update)
    asyncio.run(subscription_billing.sync_subscription_recurring_item(
        {"customer_id": 4, "product_id": 3, "quantity": 2, "unit_price": Decimal("1"),
         "external_name": "Custom description", "status": "active"},
        existing={"id": 9},
        preserve_existing_schedule=True,
    ))
    assert captured["description_template"] == "Custom description"


def _plan(next_charge=date(2026, 12, 15)):
    return conversion.ConversionPlan(
        item_id=11, product_id=3, product_name="Plan", product_sku="SKU",
        subscription_category_id=7, description="Desc", quantity=2,
        unit_price=Decimal("10"), commitment="monthly", payment_frequency="monthly",
        payment_months=1, next_charge_date=next_charge,
        term_start_date=date(2026, 11, 15), term_end_date=date(2026, 12, 14),
    )


def test_preview_route_returns_plan(monkeypatch):
    from app.api.routes import companies as routes

    async def fake_preview(company_id, item_id, *, next_charge_date=None):
        assert (company_id, item_id) == (5, 11)
        return _plan()

    monkeypatch.setattr(conversion, "preview_conversion", fake_preview)
    body = asyncio.run(routes.preview_recurring_item_subscription_conversion(5, 11, None, None, {}))
    assert body["next_charge_date"] == "2026-12-15"
    assert body["term_end_date"] == "2026-12-14"


@pytest.mark.parametrize(
    ("error", "status_code"),
    [(LookupError("missing"), 404), (conversion.ConversionError("No match"), 409)],
)
def test_convert_route_maps_errors(monkeypatch, error, status_code):
    from fastapi import HTTPException

    from app.api.routes import companies as routes
    from app.schemas.company_recurring_invoice_items import RecurringItemConversionRequest

    async def fake_convert(*_args, **_kwargs):
        raise error

    monkeypatch.setattr(conversion, "convert_item", fake_convert)
    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(routes.convert_recurring_item_to_subscription(
            5, 11, RecurringItemConversionRequest(quantity=2), None, None, {"id": 1}
        ))
    assert excinfo.value.status_code == status_code


def test_convert_route_creates_subscription_and_audits(monkeypatch):
    from app.api.routes import companies as routes
    from app.schemas.company_recurring_invoice_items import RecurringItemConversionRequest

    audited = {}

    async def fake_convert(company_id, item_id, *, user_id, next_charge_date=None, quantity=None):
        assert (company_id, item_id, user_id) == (5, 11, 1)
        assert next_charge_date == date(2027, 1, 1)
        assert quantity == 3
        return (
            {"id": "sub-1", "start_date": date(2026, 12, 1), "end_date": date(2026, 12, 31)},
            {"id": item_id, "start_date": date(2027, 1, 1)},
        )

    async def fake_record(**kwargs):
        audited.update(kwargs)

    monkeypatch.setattr(conversion, "convert_item", fake_convert)
    monkeypatch.setattr(routes.audit_service, "record", fake_record)
    payload = RecurringItemConversionRequest(next_charge_date=date(2027, 1, 1), quantity=3)
    result = asyncio.run(routes.convert_recurring_item_to_subscription(5, 11, payload, None, None, {"id": 1}))
    assert result == {
        "subscription_id": "sub-1",
        "start_date": date(2026, 12, 1),
        "end_date": date(2026, 12, 31),
        "next_charge_date": date(2027, 1, 1),
    }
    assert audited["action"] == "company.recurring_item.convert_to_subscription"
    assert audited["entity_id"] == 11
