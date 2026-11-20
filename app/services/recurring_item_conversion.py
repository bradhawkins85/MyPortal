"""Convert a company recurring invoice item into a shop subscription.

A recurring item and a subscription are linked by the shop product SKU (the
recurring item's ``product_code``). Converting creates the subscription for
the matching shop subscription product, carries the item's description,
quantity and price across, and aligns the subscription term so the next
charge lands on the same date the recurring item would have billed next.
"""
from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Mapping, Sequence

from app.repositories import company_recurring_invoice_items as recurring_items_repo
from app.repositories import shop as shop_repo
from app.repositories import subscriptions as subscriptions_repo
from app.services import shop as shop_service
from app.services import subscription_billing


class ConversionError(ValueError):
    """Raised when a recurring item cannot be converted."""


_ITEM_FREQUENCY_MONTHS = {"monthly": 1, "quarterly": 3, "yearly": 12}


@dataclass(frozen=True)
class ConversionPlan:
    item_id: int
    product_id: int
    product_name: str
    product_sku: str
    subscription_category_id: int
    description: str
    quantity: int | None
    unit_price: Decimal
    commitment: str
    payment_frequency: str
    payment_months: int
    next_charge_date: date
    term_start_date: date
    term_end_date: date

    def as_dict(self) -> dict[str, Any]:
        return {
            "item_id": self.item_id,
            "product_id": self.product_id,
            "product_name": self.product_name,
            "product_sku": self.product_sku,
            "description": self.description,
            "quantity": self.quantity,
            "unit_price": float(self.unit_price),
            "commitment": self.commitment,
            "payment_frequency": self.payment_frequency,
            "next_charge_date": self.next_charge_date.isoformat(),
            "term_start_date": self.term_start_date.isoformat(),
            "term_end_date": self.term_end_date.isoformat(),
        }


def add_months(value: date, months: int) -> date:
    """Shift ``value`` by whole months, clamping to the month's last day."""
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def _as_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str) and value.strip():
        try:
            return datetime.fromisoformat(value.strip().replace("Z", "+00:00")).date()
        except ValueError:
            return None
    return None


def _item_quantity(item: Mapping[str, Any]) -> int | None:
    try:
        return subscription_billing.recurring_quantity(item)
    except ValueError:
        return None


def _billing_terms(
    product: Mapping[str, Any], item: Mapping[str, Any]
) -> tuple[str, str, int]:
    """Return (commitment, payment_frequency, payment_months)."""
    plan = shop_service.get_subscription_billing_plan(product)
    if plan:
        commitment, payment = plan
    else:
        commitment = str(product.get("commitment_type") or "").strip().lower()
        if commitment not in {"monthly", "annual"}:
            commitment = "annual"
        item_frequency = str(item.get("billing_frequency") or "").strip().lower()
        payment = "monthly" if item_frequency == "monthly" else (
            "monthly" if commitment == "monthly" else "annual"
        )
    payment_months = 1 if payment == "monthly" else 12
    return commitment, payment, payment_months


def calculate_next_charge_date(
    item: Mapping[str, Any], payment_months: int, *, today: date
) -> date:
    """Return the next date the item would bill, never earlier than ``today``.

    The last billed date is the strongest anchor (it is when the customer was
    actually charged); otherwise the item's start date is used. Items that
    have never been billed and have no future start date are due today.
    """
    last_billed = _as_date(item.get("last_billed_at"))
    if last_billed is None:
        start = _as_date(item.get("start_date"))
        return start if start and start > today else today
    # Use the item's own cadence so the next charge matches what the
    # recurring item would have billed after the last charge.
    item_months = _ITEM_FREQUENCY_MONTHS.get(
        str(item.get("billing_frequency") or "").strip().lower(), payment_months
    )
    periods = 1
    candidate = add_months(last_billed, item_months)
    while candidate < today:
        periods += 1
        candidate = add_months(last_billed, item_months * periods)
    return candidate


def build_term(
    next_charge_date: date,
    *,
    commitment: str,
    payment_months: int,
    item_start: date | None,
) -> tuple[date, date]:
    """Return the current subscription term (start, end).

    Renewals bill on ``end_date + 1``, so the term ends the day before the
    next charge when the commitment and payment periods match. For an annual
    commitment paid monthly the term follows the yearly anniversary of the
    item's start date instead.
    """
    commitment_months = 1 if commitment == "monthly" else 12
    if commitment_months == payment_months or item_start is None or item_start >= next_charge_date:
        if commitment_months == payment_months:
            term_start = add_months(next_charge_date, -commitment_months)
            return term_start, next_charge_date - timedelta(days=1)
        term_start = add_months(next_charge_date, -payment_months)
        return term_start, add_months(term_start, commitment_months) - timedelta(days=1)
    years = 0
    while add_months(item_start, commitment_months * (years + 1)) < next_charge_date:
        years += 1
    term_start = add_months(item_start, commitment_months * years)
    return term_start, add_months(term_start, commitment_months) - timedelta(days=1)


async def find_matching_product(
    company_id: int, item: Mapping[str, Any]
) -> dict[str, Any] | None:
    """Return the shop subscription product matching the item's SKU, if any.

    Items already backed by an active subscription for that product are not
    convertible, so ``None`` is returned for them.
    """
    sku = str(item.get("product_code") or "").strip()
    if not sku:
        return None
    product = await shop_repo.get_product_by_sku(sku)
    if not product or product.get("subscription_category_id") is None:
        return None
    if str(product.get("sku") or "").strip().lower() != sku.lower():
        return None
    active_product_ids = await subscriptions_repo.get_active_subscription_product_ids(company_id)
    if int(product["id"]) in active_product_ids:
        return None
    return product


async def list_convertible_items(
    company_id: int, items: Sequence[Mapping[str, Any]]
) -> dict[int, dict[str, Any]]:
    """Map convertible item ids to their matching product summary."""
    if not items:
        return {}
    active_product_ids = await subscriptions_repo.get_active_subscription_product_ids(company_id)
    products_by_sku: dict[str, dict[str, Any] | None] = {}
    result: dict[int, dict[str, Any]] = {}
    for item in items:
        sku = str(item.get("product_code") or "").strip()
        if not sku or item.get("id") is None:
            continue
        key = sku.lower()
        if key not in products_by_sku:
            product = await shop_repo.get_product_by_sku(sku)
            if (
                product
                and product.get("subscription_category_id") is not None
                and str(product.get("sku") or "").strip().lower() == key
            ):
                products_by_sku[key] = product
            else:
                products_by_sku[key] = None
        product = products_by_sku[key]
        if not product or int(product["id"]) in active_product_ids:
            continue
        result[int(item["id"])] = {
            "product_id": int(product["id"]),
            "product_name": product.get("name") or sku,
        }
    return result


def build_plan(
    item: Mapping[str, Any],
    product: Mapping[str, Any],
    *,
    today: date,
    next_charge_date: date | None = None,
    quantity: int | None = None,
) -> ConversionPlan:
    commitment, payment, payment_months = _billing_terms(product, item)
    resolved_next = next_charge_date or calculate_next_charge_date(
        item, payment_months, today=today
    )
    term_start, term_end = build_term(
        resolved_next,
        commitment=commitment,
        payment_months=payment_months,
        item_start=_as_date(item.get("start_date")),
    )
    if item.get("price_override") is not None:
        unit_price = Decimal(str(item["price_override"]))
    else:
        unit_price = shop_service.get_product_price(product)
    description = str(item.get("description_template") or "").strip() or str(
        product.get("name") or product.get("sku") or ""
    )
    return ConversionPlan(
        item_id=int(item["id"]),
        product_id=int(product["id"]),
        product_name=str(product.get("name") or product.get("sku") or ""),
        product_sku=str(product.get("sku") or ""),
        subscription_category_id=int(product["subscription_category_id"]),
        description=description,
        quantity=quantity if quantity is not None else _item_quantity(item),
        unit_price=unit_price,
        commitment=commitment,
        payment_frequency=payment,
        payment_months=payment_months,
        next_charge_date=resolved_next,
        term_start_date=term_start,
        term_end_date=term_end,
    )


async def _load(company_id: int, item_id: int) -> tuple[dict[str, Any], dict[str, Any]]:
    item = await recurring_items_repo.get_recurring_invoice_item(item_id)
    if not item or int(item.get("company_id") or 0) != int(company_id):
        raise LookupError("Recurring invoice item not found")
    product = await find_matching_product(company_id, item)
    if not product:
        raise ConversionError(
            "No shop subscription product matches this item's product code, "
            "or the company already has an active subscription for it."
        )
    return item, product


async def preview_conversion(
    company_id: int,
    item_id: int,
    *,
    today: date | None = None,
    next_charge_date: date | None = None,
) -> ConversionPlan:
    item, product = await _load(company_id, item_id)
    return build_plan(
        item, product, today=today or date.today(), next_charge_date=next_charge_date
    )


async def convert_item(
    company_id: int,
    item_id: int,
    *,
    user_id: int | None,
    today: date | None = None,
    next_charge_date: date | None = None,
    quantity: int | None = None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Create the subscription and align the recurring item. Returns both."""
    current = today or date.today()
    if next_charge_date is not None and next_charge_date < current:
        raise ConversionError("The next charge date cannot be in the past.")
    item, product = await _load(company_id, item_id)
    plan = build_plan(
        item, product, today=current, next_charge_date=next_charge_date, quantity=quantity
    )
    if plan.quantity is None or plan.quantity < 1:
        raise ConversionError(
            "Enter a whole-number quantity; this item's quantity is calculated from a variable."
        )
    subscription = await subscriptions_repo.create_subscription(
        customer_id=company_id,
        product_id=plan.product_id,
        subscription_category_id=plan.subscription_category_id,
        start_date=plan.term_start_date,
        end_date=plan.term_end_date,
        quantity=plan.quantity,
        unit_price=plan.unit_price,
        status="active",
        auto_renew=True,
        created_by=user_id,
        external_name=plan.description,
        billing_frequency=plan.payment_frequency,
    )
    updated_item = await recurring_items_repo.update_recurring_invoice_item(
        int(item["id"]),
        description_template=plan.description,
        qty_expression=str(plan.quantity),
        price_override=float(plan.unit_price),
        active=True,
        billing_frequency="monthly" if plan.payment_frequency == "monthly" else "yearly",
        clear_billing_interval=True,
        start_date=plan.next_charge_date,
        clear_end_date=True,
    )
    return subscription, updated_item or dict(item)
