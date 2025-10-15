from __future__ import annotations

from collections.abc import Iterable

DEFAULT_NOTIFICATION_EVENT_TYPES: list[str] = [
    "general",
    "billing.invoice_overdue",
    "billing.payment_received",
    "port.created",
    "port.updated",
    "port.deleted",
    "port.document_uploaded",
    "port.document_deleted",
    "port.pricing.created",
    "port.pricing.submitted",
    "port.pricing.approved",
    "port.pricing.rejected",
    "shop.order_submitted",
    "shop.order_cancelled",
    "shop.shipping_status_updated",
    "webhook.failed",
    "webhook.retried",
]


def merge_event_types(*collections: Iterable[str] | None) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for collection in collections:
        if not collection:
            continue
        for raw in collection:
            if not raw:
                continue
            value = str(raw).strip()
            if not value or value in seen:
                continue
            seen.add(value)
            ordered.append(value)
    return sorted(ordered)
