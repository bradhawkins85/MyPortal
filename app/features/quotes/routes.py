"""Portal quote routes for the ``quotes`` feature pack."""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse

from app.core.config import get_settings
from app.features.orders import routes as orders_routes
from app.repositories import cart as cart_repo
from app.repositories import shop as shop_repo
from app.repositories import users as user_repo
from app.security.session import session_manager


router = APIRouter(tags=["Quotes"])


@lru_cache(maxsize=1)
def _main():
    from app import main as main_module

    return main_module


@router.get("/quotes", response_class=HTMLResponse)
async def quotes_page(
    request: Request,
    status_filter: str | None = Query(None, alias="status"),
):
    main_module = _main()
    (
        user,
        membership,
        company,
        company_id,
        redirect,
    ) = await main_module._load_company_section_context(
        request,
        permission_field="can_access_quotes",
    )
    if redirect:
        return redirect

    quotes_raw = await shop_repo.list_quote_summaries(company_id)

    def _label(value: str | None) -> str:
        text = (value or "").strip()
        return text if text else "Active"

    def _is_expired(expires_at: datetime | str | None) -> bool:
        if not expires_at:
            return False
        if isinstance(expires_at, str):
            try:
                expires_at = datetime.fromisoformat(expires_at)
            except (ValueError, AttributeError):
                return False
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        return expires_at < datetime.now(timezone.utc)

    enriched_quotes: list[dict[str, Any]] = []

    user_ids_to_fetch = set()
    for quote_record in quotes_raw:
        assigned_user_id = quote_record.get("assigned_user_id")
        if assigned_user_id:
            user_ids_to_fetch.add(assigned_user_id)

    user_emails = {}
    for user_id in user_ids_to_fetch:
        user_data = await user_repo.get_user_by_id(user_id)
        if user_data:
            user_emails[user_id] = user_data.get("email")

    for quote_record in quotes_raw:
        label = _label(quote_record.get("status"))
        is_expired = _is_expired(quote_record.get("expires_at"))
        if is_expired and label.lower() == "active":
            label = "Expired"
        record = dict(quote_record)
        record["status_label"] = label
        record["status_value"] = label.lower()
        record["status_badge"] = orders_routes._normalise_status_badge(label)
        record["created_at_iso"] = quote_record.get("created_at")
        record["expires_at_iso"] = quote_record.get("expires_at")
        record["is_expired"] = is_expired

        assigned_user_id = quote_record.get("assigned_user_id")
        record["assigned_user_email"] = (
            user_emails.get(assigned_user_id) if assigned_user_id else None
        )

        enriched_quotes.append(record)

    status_options = sorted(
        {(record["status_value"], record["status_label"]) for record in enriched_quotes},
        key=lambda item: item[1].lower(),
    )

    status_option_map = {value: label for value, label in status_options}

    status_key = (status_filter or "").strip().lower() or None
    if status_key not in status_option_map:
        status_key = None

    filtered_quotes = [
        record
        for record in enriched_quotes
        if status_key is None or record["status_value"] == status_key
    ]

    total_quotes = len(enriched_quotes)
    visible_quotes = len(filtered_quotes)

    extra = {
        "title": "Quotes",
        "quotes": filtered_quotes,
        "status_options": [
            {"value": value, "label": label} for value, label in status_options
        ],
        "status_filter": status_key,
        "status_summary": orders_routes._summarise_orders(
            filtered_quotes,
            attribute="status_label",
        ),
        "quotes_total": visible_quotes,
        "quotes_total_all": total_quotes,
        "filters_active": bool(status_key),
    }
    return await main_module._render_template("shop/quotes.html", request, user, extra=extra)


@router.post(
    "/quotes/load/{quote_number}",
    response_class=RedirectResponse,
    name="load_quote",
    include_in_schema=False,
)
async def load_quote_to_cart(request: Request, quote_number: str) -> RedirectResponse:
    main_module = _main()
    (
        user,
        membership,
        company,
        company_id,
        redirect,
    ) = await main_module._load_company_section_context(
        request,
        permission_field="can_access_quotes",
    )
    if redirect:
        return redirect

    session = await session_manager.load_session(request)
    if not session:
        return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)

    quote_items = await shop_repo.list_quote_items(quote_number, company_id)
    if not quote_items:
        message = quote("Quote not found or has no items.")
        return RedirectResponse(
            url=f"/quotes?quoteMessage={message}",
            status_code=status.HTTP_303_SEE_OTHER,
        )

    for item in quote_items:
        product_id = int(item.get("product_id"))
        product = await shop_repo.get_product_by_id(product_id, company_id=company_id)
        if not product:
            continue

        existing_item = await cart_repo.get_item(session.id, product_id)
        new_quantity = int(item.get("quantity"))

        if existing_item:
            new_quantity += existing_item.get("quantity", 0)

        await cart_repo.upsert_item(
            session_id=session.id,
            product_id=product_id,
            quantity=new_quantity,
            unit_price=item.get("price"),
            name=str(item.get("product_name")),
            sku=str(item.get("sku") or ""),
            vendor_sku=product.get("vendor_sku"),
            description=product.get("description"),
            image_url=product.get("image_url"),
        )

    success = quote("Quote loaded to cart successfully.")
    return RedirectResponse(
        url=f"{request.url_for('cart_page')}?cartMessage={success}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post(
    "/cart/save-as-quote",
    response_class=RedirectResponse,
    name="cart_save_quote",
    include_in_schema=False,
)
async def save_as_quote(request: Request) -> RedirectResponse:
    main_module = _main()
    (
        user,
        membership,
        company,
        company_id,
        redirect,
    ) = await main_module._load_company_section_context(
        request,
        permission_field="can_access_quotes",
    )
    if redirect:
        return redirect

    session = await session_manager.load_session(request)
    if not session:
        return RedirectResponse(url="/login", status_code=status.HTTP_303_SEE_OTHER)
    if company_id is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No active company selected")

    form = await request.form()
    po_number_raw = form.get("poNumber")
    po_number = (str(po_number_raw).strip() or None) if po_number_raw is not None else None
    if po_number and len(po_number) > 100:
        po_number = po_number[:100]

    quote_name_raw = form.get("quoteName")
    quote_name = (str(quote_name_raw).strip() or None) if quote_name_raw is not None else None
    if quote_name and len(quote_name) > 255:
        quote_name = quote_name[:255]

    items = await cart_repo.list_items(session.id)
    if not items:
        return RedirectResponse(url=request.url_for("cart_page"), status_code=status.HTTP_303_SEE_OTHER)

    quote_number = "QUO" + "".join(secrets.choice("0123456789") for _ in range(12))

    expires_at = datetime.now(timezone.utc) + timedelta(days=get_settings().quote_expiry_days)

    for item in items:
        await shop_repo.create_quote(
            user_id=int(user["id"]),
            company_id=company_id,
            product_id=int(item.get("product_id")),
            quantity=int(item.get("quantity")),
            quote_number=quote_number,
            status="active",
            po_number=po_number,
            expires_at=expires_at.replace(tzinfo=None),
            name=quote_name,
        )

    await cart_repo.clear_cart(session.id)

    success = quote("Your quote has been saved successfully.")
    return RedirectResponse(
        url=f"/quotes?quoteMessage={success}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


__all__ = ["router"]
