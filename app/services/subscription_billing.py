"""Synchronise shop subscriptions with company recurring invoice items."""
from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping

from app.repositories import company_recurring_invoice_items as recurring_items_repo
from app.repositories import shop as shop_repo
from app.services import shop as shop_service


def recurring_quantity(item: Mapping[str, Any]) -> int:
    """Return a safe integer quantity from a manually maintained item."""
    raw = str(item.get("qty_expression") or "").strip()
    try:
        value = Decimal(raw)
    except InvalidOperation as exc:
        raise ValueError(
            f"Recurring invoice item {item.get('id')} has a non-numeric quantity expression"
        ) from exc
    if value < 0 or value != value.to_integral_value():
        raise ValueError(
            f"Recurring invoice item {item.get('id')} quantity must be a non-negative whole number"
        )
    return int(value)


async def find_existing_item(company_id: int, product: Mapping[str, Any]) -> dict[str, Any] | None:
    sku = str(product.get("sku") or "").strip()
    if not sku:
        raise ValueError("Subscription shop product SKU was not found")
    return await recurring_items_repo.get_recurring_invoice_item_by_product_code(company_id, sku)


async def sync_subscription_recurring_item(
    subscription: Mapping[str, Any],
    *,
    existing: Mapping[str, Any] | None = None,
    preserve_existing_schedule: bool = False,
    cancellation_date: date | None = None,
) -> dict[str, Any]:
    """Create or update the SKU-matched recurring item without resetting billing history."""
    company_id = int(subscription["customer_id"])
    product = await shop_repo.get_product_by_id(int(subscription["product_id"]))
    if not product:
        raise ValueError("Subscription shop product was not found")
    sku = str(product.get("sku") or "").strip()
    existing = existing or await find_existing_item(company_id, product)
    canceled = cancellation_date is not None or subscription.get("status") == "canceled"
    billing_plan = shop_service.get_subscription_billing_plan(product)
    values = {
        "product_code": sku,
        "description_template": str(product.get("invoice_description") or product.get("name") or sku),
        "qty_expression": str(max(0, int(subscription.get("quantity") or 0))),
        "price_override": float(Decimal(str(subscription.get("unit_price") or 0))),
        "active": not canceled and int(subscription.get("quantity") or 0) > 0,
    }
    if billing_plan:
        _commitment, payment_frequency = billing_plan
        values["billing_frequency"] = "yearly" if payment_frequency == "annual" else "monthly"
        values["clear_billing_interval"] = True
    if cancellation_date is not None:
        values["end_date"] = cancellation_date
    elif not (existing and preserve_existing_schedule):
        values["start_date"] = subscription.get("start_date")
        # Active auto-renewing subscriptions have no recurring-item end date.
        # The subscription term itself still records the current commitment.
        if subscription.get("auto_renew", True):
            values["clear_end_date"] = True
        else:
            values["end_date"] = subscription.get("end_date")
    if not existing:
        # Clear flags are update-only repository arguments; NULL is already the
        # default when a new recurring item is created.
        values.pop("clear_billing_interval", None)
        values.pop("clear_end_date", None)
    if existing:
        updated = await recurring_items_repo.update_recurring_invoice_item(int(existing["id"]), **values)
        if not updated:
            raise RuntimeError("Failed to update subscription recurring invoice item")
        return updated
    return await recurring_items_repo.create_recurring_invoice_item(company_id=company_id, **values)


async def deactivate_subscription_recurring_item(
    subscription: Mapping[str, Any], *, cancellation_date: date
) -> dict[str, Any] | None:
    """Deactivate an existing SKU-linked recurring item without creating one."""
    product = await shop_repo.get_product_by_id(int(subscription["product_id"]))
    if not product:
        raise ValueError("Subscription shop product was not found")
    existing = await find_existing_item(int(subscription["customer_id"]), product)
    if not existing:
        return None
    updated = await recurring_items_repo.update_recurring_invoice_item(
        int(existing["id"]),
        active=False,
        end_date=cancellation_date,
    )
    if not updated:
        raise RuntimeError("Failed to deactivate subscription recurring invoice item")
    return updated
