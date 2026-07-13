"""Portal quote routes for the ``quotes`` feature pack."""

from __future__ import annotations

import secrets
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from html import escape
from functools import lru_cache
from typing import Any
from urllib.parse import quote

from weasyprint import HTML  # type: ignore

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from app.core.config import get_settings
from app.features.orders import routes as orders_routes
from app.repositories import cart as cart_repo
from app.repositories import shop as shop_repo
from app.repositories import users as user_repo
from app.security.session import session_manager
from app.services.sanitization import sanitize_rich_text

router = APIRouter(tags=["Quotes"])


@lru_cache(maxsize=1)
def _main():
    from app import main as main_module

    return main_module


def _format_money(value: Any) -> str:
    try:
        amount = Decimal(str(value or "0"))
    except Exception:
        amount = Decimal("0")
    return f"${amount:,.2f}"


def _format_pdf_date(value: Any) -> str:
    if not value:
        return "—"
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return escape(value)
    if isinstance(value, datetime):
        return value.strftime("%d %b %Y")
    return escape(str(value))


def _absolute_asset_url(request: Request, url: str | None) -> str | None:
    if not url:
        return None
    text = str(url).strip()
    if not text:
        return None
    if text.startswith(("http://", "https://", "data:")):
        return text
    return f"{str(request.base_url).rstrip('/')}/{text.lstrip('/')}"


def _build_quote_pdf_html(
    *,
    request: Request,
    company: dict[str, Any] | None,
    quote: dict[str, Any],
    items: list[dict[str, Any]],
    include_line_images: bool,
) -> str:
    quote_number = escape(str(quote.get("quote_number") or ""))
    company_name = escape(str((company or {}).get("name") or ""))
    quote_name = escape(str(quote.get("name") or ""))
    po_number = escape(str(quote.get("po_number") or ""))
    subtotal = sum(
        Decimal(str(item.get("price") or "0")) * int(item.get("quantity") or 0)
        for item in items
    )

    rows = []
    for item in items:
        qty = int(item.get("quantity") or 0)
        unit_price = Decimal(str(item.get("price") or "0"))
        line_total = unit_price * qty
        rows.append(
            "<tr>"
            f"<td><strong>{escape(str(item.get('product_name') or 'Product'))}</strong><br>"
            f"<span class='muted'>SKU: {escape(str(item.get('sku') or '—'))}</span></td>"
            f"<td class='num'>{qty}</td>"
            f"<td class='num'>{_format_money(unit_price)}</td>"
            f"<td class='num'>{_format_money(line_total)}</td>"
            "</tr>"
        )

    def _remove_rich_text_images(html_value: str) -> str:
        return re.sub(r"<img\b[^>]*>", "", html_value, flags=re.IGNORECASE)

    detail_pages = []
    for item in items:
        image_url = (
            _absolute_asset_url(request, item.get("image_url"))
            if include_line_images
            else None
        )
        image_html = (
            f'<img class="detail-image" src="{escape(image_url, quote=True)}" alt="">'
            if image_url
            else '<div class="image-placeholder">No image available</div>'
        )
        sanitized_description = sanitize_rich_text(str(item.get("description") or ""))
        description = (
            _remove_rich_text_images(sanitized_description.html)
            if sanitized_description.html
            else "<p>No description available.</p>"
        )
        product_link = str(item.get("product_link") or "").strip()
        product_link_html = (
            "<p class='product-link'><strong>Product Link:</strong> "
            f"<a href='{escape(product_link, quote=True)}'>{escape(product_link)}</a></p>"
            if product_link
            else ""
        )
        detail_pages.append(
            "<section class='product-page page-break'>"
            "<div class='product-hero'>"
            "<div class='product-heading'>"
            "<p class='eyebrow'>Product Details</p>"
            f"<h1>{escape(str(item.get('product_name') or 'Product'))}</h1>"
            "</div>"
            f"<div class='product-image-card'>{image_html}</div>"
            "</div>"
            f"<div class='description rich-text-viewer'>{description}</div>"
            f"{product_link_html}"
            "</section>"
        )

    return f"""
<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <style>
    @page {{ size: A4; margin: 18mm; }}
    body {{ color: #172033; font-family: Arial, sans-serif; font-size: 11px; line-height: 1.4; }}
    h1, h2, p {{ margin: 0; }}
    .hero {{ border-bottom: 3px solid #2563eb; display: flex; justify-content: space-between; margin-bottom: 22px; padding-bottom: 18px; }}
    .eyebrow {{ color: #2563eb; font-size: 10px; font-weight: 700; letter-spacing: .12em; text-transform: uppercase; }}
    .hero h1 {{ font-size: 22px; margin-top: 4px; overflow-wrap: anywhere; }}
    .meta {{ background: #f4f7fb; border-radius: 10px; padding: 14px; width: 220px; }}
    .meta div {{ display: flex; justify-content: space-between; margin: 5px 0; }}
    .section-title {{ font-size: 16px; margin: 20px 0 8px; }}
    table {{ border-collapse: collapse; width: 100%; }}
    th {{ background: #172033; color: white; font-size: 10px; letter-spacing: .04em; padding: 9px; text-align: left; text-transform: uppercase; }}
    td {{ border-bottom: 1px solid #e4e8ef; padding: 9px; vertical-align: top; }}
    .num {{ text-align: right; white-space: nowrap; }}
    .muted, .text-muted {{ color: #6b7280; }}
    .thumb-col {{ width: 58px; }}
    .line-thumb {{ border: 1px solid #e5e7eb; border-radius: 6px; max-height: 42px; max-width: 52px; object-fit: contain; }}
    .totals {{ margin-left: auto; margin-top: 18px; width: 260px; }}
    .totals div {{ display: flex; justify-content: space-between; padding: 8px 0; }}
    .grand {{ border-top: 2px solid #172033; font-size: 16px; font-weight: 700; }}
    .page-break {{ page-break-before: always; }}
    .product-hero {{ align-items: stretch; display: flex; gap: 18px; margin-bottom: 18px; }}
    .product-heading {{ flex: 1 1 52%; min-width: 0; }}
    .product-page h1 {{ font-size: 24px; line-height: 1.16; margin: 6px 0 0; overflow-wrap: anywhere; }}
    .product-image-card {{ align-items: center; background: #e8f5e9; border: 2px solid #22c55e; border-radius: 12px; box-shadow: 0 1px 4px rgba(34, 197, 94, .35); display: flex; flex: 0 0 42%; justify-content: center; min-height: 150px; padding: 12px; }}
    .detail-image {{ display: block; max-height: 170px; max-width: 100%; object-fit: contain; }}
    .image-placeholder {{ color: #4b5563; font-weight: 700; padding: 36px 12px; text-align: center; }}
    .description {{ font-size: 12px; margin-top: 18px; }}
    .description p {{ margin: 0 0 8px; }}
    .description ul, .description ol {{ margin: 0 0 8px 18px; padding: 0; }}
    .description iframe {{ border: 1px solid #e5e7eb; min-height: 260px; width: 100%; }}
    .product-link {{ border-top: 1px solid #e5e7eb; font-size: 12px; margin-top: 18px; padding-top: 10px; overflow-wrap: anywhere; }}
    .product-link a {{ color: #2563eb; }}
  </style>
</head>
<body>
  <section>
    <div class="hero">
      <div><p class="eyebrow">Quote</p><h1>{quote_number}</h1><p>{quote_name}</p></div>
      <div class="meta">
        <div><span>Company</span><strong>{company_name or '—'}</strong></div>
        <div><span>Created</span><strong>{_format_pdf_date(quote.get('created_at'))}</strong></div>
        <div><span>Expires</span><strong>{_format_pdf_date(quote.get('expires_at'))}</strong></div>
        <div><span>PO</span><strong>{po_number or '—'}</strong></div>
      </div>
    </div>
    <h2 class="section-title">Quote Items</h2>
    <table>
      <thead><tr><th>Product</th><th class="num">Qty</th><th class="num">Unit</th><th class="num">Total</th></tr></thead>
      <tbody>{''.join(rows)}</tbody>
    </table>
    <div class="totals"><div class="grand"><span>Total</span><span>{_format_money(subtotal)}</span></div></div>
  </section>
  {''.join(detail_pages)}
</body>
</html>
"""


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
        record["status_badge"] = orders_routes.normalise_status_badge(label)
        record["created_at_iso"] = quote_record.get("created_at")
        record["expires_at_iso"] = quote_record.get("expires_at")
        record["is_expired"] = is_expired

        assigned_user_id = quote_record.get("assigned_user_id")
        record["assigned_user_email"] = (
            user_emails.get(assigned_user_id) if assigned_user_id else None
        )

        enriched_quotes.append(record)

    status_options = sorted(
        {
            (record["status_value"], record["status_label"])
            for record in enriched_quotes
        },
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
        "status_summary": orders_routes.summarise_orders(
            filtered_quotes,
            attribute="status_label",
        ),
        "quotes_total": visible_quotes,
        "quotes_total_all": total_quotes,
        "filters_active": bool(status_key),
    }
    return await main_module._render_template(
        "shop/quotes.html", request, user, extra=extra
    )


@router.get(
    "/quotes/export/{quote_number}",
    response_class=Response,
    name="export_quote_pdf",
    include_in_schema=False,
)
async def export_quote_pdf(
    request: Request,
    quote_number: str,
    with_images: bool = Query(False, alias="withImages"),
) -> Response:
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

    quote_summary = await shop_repo.get_quote_summary(quote_number, company_id)
    quote_items = await shop_repo.list_quote_items(quote_number, company_id)
    if not quote_summary or not quote_items:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Quote not found"
        )

    html = _build_quote_pdf_html(
        request=request,
        company=company,
        quote=quote_summary,
        items=quote_items,
        include_line_images=with_images,
    )
    pdf_bytes = HTML(string=html, base_url=str(request.base_url)).write_pdf()
    filename = f"{quote_number}-{'with-images' if with_images else 'no-images'}.pdf"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


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
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="No active company selected"
        )

    form = await request.form()
    po_number_raw = form.get("poNumber")
    po_number = (
        (str(po_number_raw).strip() or None) if po_number_raw is not None else None
    )
    if po_number and len(po_number) > 100:
        po_number = po_number[:100]

    quote_name_raw = form.get("quoteName")
    quote_name = (
        (str(quote_name_raw).strip() or None) if quote_name_raw is not None else None
    )
    if quote_name and len(quote_name) > 255:
        quote_name = quote_name[:255]

    items = await cart_repo.list_items(session.id)
    if not items:
        return RedirectResponse(
            url=request.url_for("cart_page"), status_code=status.HTTP_303_SEE_OTHER
        )

    quote_number = "QUO" + "".join(secrets.choice("0123456789") for _ in range(12))

    expires_at = datetime.now(timezone.utc) + timedelta(
        days=get_settings().quote_expiry_days
    )

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
