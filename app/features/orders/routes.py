"""Orders page routes for the ``orders`` feature pack."""

from __future__ import annotations

from collections import Counter
from typing import Any

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse

from app.repositories import shop as shop_repo


router = APIRouter(tags=["Orders"])


def _main():
    from app import main as main_module

    return main_module


def _normalise_status_badge(label: str) -> str:
    lowered = label.lower()
    if any(token in lowered for token in ("cancel", "decline", "failed")):
        return "badge--danger"
    if any(token in lowered for token in ("ship", "delivered", "complete", "fulfilled")):
        return "badge--success"
    if any(token in lowered for token in ("pending", "processing", "backorder", "hold")):
        return "badge--warning"
    return "badge--muted"


def _summarise_orders(
    orders: list[dict[str, Any]],
    *,
    attribute: str,
) -> list[dict[str, Any]]:
    total = len(orders)
    if total == 0:
        return []
    counter = Counter(order.get(attribute, "") or "Unknown" for order in orders)
    summary: list[dict[str, Any]] = []
    for label, count in sorted(counter.items(), key=lambda item: (-item[1], item[0].lower())):
        percentage = round((count / total) * 100, 1)
        summary.append({"label": label, "count": count, "percentage": percentage})
    return summary


async def orders_page(
    request: Request,
    status_filter: str | None = Query(None, alias="status"),
    shipping_filter: str | None = Query(None, alias="shippingStatus"),
):
    (
        user,
        membership,
        company,
        company_id,
        redirect,
    ) = await _main()._load_company_section_context(
        request,
        permission_field="can_access_orders",
    )
    if redirect:
        return redirect

    orders_raw = await shop_repo.list_order_summaries(company_id)

    def _label(value: str | None) -> str:
        text = (value or "").strip()
        return text if text else "Pending"

    enriched_orders: list[dict[str, Any]] = []
    for order in orders_raw:
        label = _label(order.get("status"))
        shipping_label = _label(order.get("shipping_status"))
        record = dict(order)
        record["status_label"] = label
        record["shipping_status_label"] = shipping_label
        record["status_value"] = label.lower()
        record["shipping_status_value"] = shipping_label.lower()
        record["status_badge"] = _normalise_status_badge(label)
        record["shipping_badge"] = _normalise_status_badge(shipping_label)
        record["order_date_iso"] = order.get("order_date")
        record["eta_iso"] = order.get("eta")
        enriched_orders.append(record)

    status_options = sorted(
        {(order["status_value"], order["status_label"]) for order in enriched_orders},
        key=lambda item: item[1].lower(),
    )
    shipping_options = sorted(
        {(order["shipping_status_value"], order["shipping_status_label"]) for order in enriched_orders},
        key=lambda item: item[1].lower(),
    )

    status_option_map = {value: label for value, label in status_options}
    shipping_option_map = {value: label for value, label in shipping_options}

    status_key = (status_filter or "").strip().lower() or None
    if status_key not in status_option_map:
        status_key = None
    shipping_key = (shipping_filter or "").strip().lower() or None
    if shipping_key not in shipping_option_map:
        shipping_key = None

    filtered_orders = [
        order
        for order in enriched_orders
        if (status_key is None or order["status_value"] == status_key)
        and (shipping_key is None or order["shipping_status_value"] == shipping_key)
    ]

    total_orders = len(enriched_orders)
    visible_orders = len(filtered_orders)

    status_summary = _summarise_orders(filtered_orders, attribute="status_label")
    shipping_summary = _summarise_orders(filtered_orders, attribute="shipping_status_label")

    extra = {
        "title": "Orders",
        "orders": filtered_orders,
        "status_options": [
            {"value": value, "label": label} for value, label in status_options
        ],
        "shipping_options": [
            {"value": value, "label": label} for value, label in shipping_options
        ],
        "status_filter": status_key,
        "shipping_filter": shipping_key,
        "status_summary": status_summary,
        "shipping_summary": shipping_summary,
        "orders_total": visible_orders,
        "orders_total_all": total_orders,
        "filters_active": bool(status_key or shipping_key),
    }
    return await _main()._render_template("shop/orders.html", request, user, extra=extra)


@router.get("/orders", response_class=HTMLResponse)
async def route_orders_page(
    request: Request,
    status_filter: str | None = Query(None, alias="status"),
    shipping_filter: str | None = Query(None, alias="shippingStatus"),
):
    return await orders_page(
        request=request,
        status_filter=status_filter,
        shipping_filter=shipping_filter,
    )


__all__ = ["router", "orders_page", "_normalise_status_badge", "_summarise_orders"]
