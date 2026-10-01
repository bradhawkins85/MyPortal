"""Voice Monitor entitlement and billing contract.

Voice Monitor is sold through the normal subscription shop as **one monitored
number per purchased unit**.  Each unit includes 100 successful or attempted
calls per calendar month.  Further calls are metered per attempt and per
connected minute; completed transcriptions carry an additional surcharge.

The prices below are the public product defaults.  The recurring number price
is always the subscription's ``unit_price`` (and consequently follows the
normal renewal/proration/change invoice path).  Usage rows are consumed by the
existing invoice generator/Xero exporter; this module deliberately never
creates or exports an invoice itself.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

from app.core.database import db
from app.services.shop import get_product_price


VOICE_MONITOR_CATEGORY = "Voice Monitor"
INCLUDED_ATTEMPTS_PER_NUMBER = 100
ATTEMPT_OVERAGE_PRICE = Decimal("0.05")
CONNECTED_MINUTE_PRICE = Decimal("0.02")
TRANSCRIPTION_SURCHARGE = Decimal("0.10")


@dataclass(frozen=True)
class VoiceMonitorContract:
    billing_unit: str = "monitored_number"
    included_attempts_per_number_month: int = INCLUDED_ATTEMPTS_PER_NUMBER
    attempt_overage_price: Decimal = ATTEMPT_OVERAGE_PRICE
    connected_minute_price: Decimal = CONNECTED_MINUTE_PRICE
    transcription_surcharge: Decimal = TRANSCRIPTION_SURCHARGE


CONTRACT = VoiceMonitorContract()


def contract_display(subscription: dict[str, Any]) -> dict[str, Any]:
    """Return presentation-safe contract details for both subscription views."""
    quantity = int(subscription.get("quantity") or 0)
    result = asdict(CONTRACT)
    result.update(
        entitlement="Voice Monitor",
        quantity=quantity,
        recurring_unit_price=Decimal(str(subscription.get("unit_price") or 0)),
        included_attempts=quantity * INCLUDED_ATTEMPTS_PER_NUMBER,
    )
    return result


def is_voice_monitor_subscription(subscription: dict[str, Any]) -> bool:
    return str(subscription.get("category_name") or "").strip().casefold() == VOICE_MONITOR_CATEGORY.casefold()


async def get_entitlement(subscription_id: str, *, company_id: int, today: date | None = None) -> dict[str, Any]:
    """Validate and return the exact subscription backing an endpoint.

    ``pending_renewal`` is intentionally denied: an unpaid/failed renewal must
    not produce service.  Any pending quantity change also freezes dispatch so
    provisioned numbers cannot race an approval.
    """
    today = today or date.today()
    row = await db.fetch_one(
        """SELECT s.*, c.name AS category_name,
                  EXISTS(SELECT 1 FROM subscription_change_requests r
                         WHERE r.subscription_id=s.id AND r.status='pending') AS has_pending_change
             FROM subscriptions s
             LEFT JOIN subscription_categories c ON c.id=s.subscription_category_id
            WHERE s.id=%s AND s.customer_id=%s""",
        (subscription_id, company_id),
    )
    if not row or not is_voice_monitor_subscription(row):
        raise ValueError("Voice Monitor subscription not found")
    status = str(row.get("status") or "").casefold()
    if status != "active":
        raise ValueError(f"subscription is not serviceable ({status or 'pending'})")
    start, end = row.get("start_date"), row.get("end_date")
    if isinstance(start, datetime):
        start = start.date()
    if isinstance(end, datetime):
        end = end.date()
    if not start or not end or today < start or today > end:
        raise ValueError("subscription is outside its service dates")
    if bool(row.get("has_pending_change")):
        raise ValueError("subscription is awaiting an approved quantity change")
    return dict(row)


async def assert_endpoint_capacity(subscription_id: str, *, company_id: int, enabling_endpoint_id: int | None = None) -> dict[str, Any]:
    """Lock the subscription and enforce enabled endpoints <= paid quantity."""
    subscription = await get_entitlement(subscription_id, company_id=company_id)
    row = await db.fetch_one(
        "SELECT COUNT(*) AS used FROM voice_monitor_endpoints WHERE subscription_id=%s AND company_id=%s AND enabled=1 AND (%s IS NULL OR id<>%s)",
        (subscription_id, company_id, enabling_endpoint_id, enabling_endpoint_id),
    )
    if int((row or {}).get("used") or 0) >= int(subscription["quantity"]):
        raise OverflowError("enabled monitored numbers exceed purchased quantity")
    return subscription


async def record_attempt_usage(attempt_id: int, *, duration_seconds: int = 0, attempted: bool = True, transcription_completed: bool = False, occurred_at: datetime | None = None) -> bool:
    """Insert the immutable bill-once ledger row for one logical attempt.

    The attempt primary key is the billing idempotency key, so worker retries
    and duplicate provider callbacks return ``False`` rather than billing twice.
    Failed calls are attempt-billable but never accrue minutes/transcription.
    """
    attempt = await db.fetch_one(
        "SELECT a.id,a.company_id,a.endpoint_id,a.outcome_status,e.subscription_id,e.transcription_enabled FROM voice_monitor_attempts a LEFT JOIN voice_monitor_endpoints e ON e.id=a.endpoint_id WHERE a.id=%s",
        (attempt_id,),
    )
    if not attempt or not attempt.get("subscription_id"):
        raise ValueError("attempt is not associated with a subscription")
    terminal = str(attempt.get("outcome_status")) in {"passed", "failed", "timed_out", "cancelled", "exhausted"}
    if not terminal:
        raise ValueError("usage can only be recorded for a terminal attempt")
    minutes = int((max(duration_seconds, 0) + 59) // 60) if str(attempt.get("outcome_status")) == "passed" else 0
    transcription = bool(transcription_completed and attempt.get("transcription_enabled") and str(attempt.get("outcome_status")) == "passed")
    verb = "INSERT OR IGNORE" if db.is_sqlite() else "INSERT IGNORE"
    changed = await db.execute_rowcount(
        f"{verb} INTO voice_monitor_usage_ledger (attempt_id,subscription_id,company_id,occurred_at,attempt_units,connected_minutes,transcription_units,attempt_price,minute_price,transcription_price) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (attempt_id, attempt["subscription_id"], attempt["company_id"], occurred_at or datetime.now(timezone.utc).replace(tzinfo=None), int(attempted), minutes, int(transcription), ATTEMPT_OVERAGE_PRICE, CONNECTED_MINUTE_PRICE, TRANSCRIPTION_SURCHARGE),
    )
    return bool(changed)


async def usage_invoice_lines(subscription_id: str, period_start: date, period_end: date) -> list[dict[str, Any]]:
    """Build lines for the existing subscription invoice service to export."""
    subscription = await db.fetch_one("SELECT quantity FROM subscriptions WHERE id=%s", (subscription_id,))
    rows = await db.fetch_one(
        "SELECT COALESCE(SUM(attempt_units),0) attempts,COALESCE(SUM(connected_minutes),0) minutes,COALESCE(SUM(transcription_units),0) transcriptions FROM voice_monitor_usage_ledger WHERE subscription_id=%s AND occurred_at >= %s AND occurred_at < %s",
        (subscription_id, period_start, period_end),
    ) or {}
    included = int((subscription or {}).get("quantity") or 0) * INCLUDED_ATTEMPTS_PER_NUMBER
    overage = max(0, int(rows.get("attempts") or 0) - included)
    values = (("Voice Monitor attempt overage", overage, ATTEMPT_OVERAGE_PRICE), ("Voice Monitor connected minutes", int(rows.get("minutes") or 0), CONNECTED_MINUTE_PRICE), ("Voice Monitor transcription surcharge", int(rows.get("transcriptions") or 0), TRANSCRIPTION_SURCHARGE))
    return [{"description": description, "quantity": quantity, "unit_price": price, "amount": (price * quantity).quantize(Decimal("0.01"))} for description, quantity, price in values if quantity]


def _term_days_from_commitment(commitment_type: str | None) -> int:
    """Return the term length in days for a given commitment type."""
    if str(commitment_type or "").casefold() == "monthly":
        return 30
    return 365


async def list_voice_monitor_products() -> list[dict[str, Any]]:
    """Return active shop products that belong to the Voice Monitor subscription category."""
    rows = await db.fetch_all(
        """
        SELECT p.id, p.name, p.price, p.commitment_type, p.payment_frequency,
               p.price_monthly_commitment, p.price_annual_monthly_payment,
               p.price_annual_annual_payment, p.voice_monitor_calls_per_day,
               p.description
        FROM shop_products p
        JOIN subscription_categories c ON c.id = p.subscription_category_id
        WHERE c.name = %s AND p.archived = 0
        ORDER BY p.price ASC, p.name ASC
        """,
        (VOICE_MONITOR_CATEGORY,),
    )
    return [
        {
            "id": int(row["id"]),
            "name": row["name"],
            "price": get_product_price(row),
            "calls_per_day": int(row.get("voice_monitor_calls_per_day") or 1),
            "term_days": _term_days_from_commitment(row.get("commitment_type")),
            "description": row.get("description"),
        }
        for row in rows
    ]


async def provision_subscription(
    company_id: int,
    product_id: int,
    created_by: int,
) -> dict[str, Any]:
    """Auto-provision a Voice Monitor subscription for a company.

    Validates the product belongs to the Voice Monitor subscription category,
    then creates a new active subscription with quantity=1.
    """
    row = await db.fetch_one(
        """
        SELECT p.id, p.price, p.commitment_type, p.payment_frequency,
               p.price_monthly_commitment, p.price_annual_monthly_payment,
               p.price_annual_annual_payment, p.voice_monitor_calls_per_day,
               c.id AS category_id
        FROM shop_products p
        JOIN subscription_categories c ON c.id = p.subscription_category_id
        WHERE p.id = %s AND c.name = %s AND p.archived = 0
        """,
        (product_id, VOICE_MONITOR_CATEGORY),
    )
    if not row:
        raise ValueError("product is not an active Voice Monitor subscription product")

    from app.repositories import subscriptions as subscriptions_repo

    today = date.today()
    term_days = _term_days_from_commitment(row.get("commitment_type"))
    end_date = today + timedelta(days=term_days)

    return await subscriptions_repo.create_subscription(
        customer_id=company_id,
        product_id=int(row["id"]),
        subscription_category_id=int(row["category_id"]),
        start_date=today,
        end_date=end_date,
        quantity=1,
        unit_price=get_product_price(row),
        status="active",
        auto_renew=False,
        created_by=created_by,
    )
