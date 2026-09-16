"""Admin subscription routes for the ``subscriptions`` feature pack.

Owns the admin subscription management page:

* ``GET /admin/subscriptions`` — admin subscription management page.

Handler code migrated from ``app/main.py``.
"""

from __future__ import annotations

from collections import Counter
from datetime import date
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import ValidationError

from app.api.routes.subscriptions import (
    CreateExistingSubscriptionRequest,
    create_existing_subscription,
)
from app.core.logging import log_error
from app.repositories import companies as companies_repo
from app.repositories import subscription_categories as categories_repo
from app.repositories import shop as shop_repo
from app.repositories import subscriptions as subscriptions_repo
from app.security.csrf import parse_csrf_form
from app.services.voice_monitor_billing import (
    contract_display,
    is_voice_monitor_subscription,
)


router = APIRouter(tags=["Subscriptions"])


async def _require_super_admin(request: Request):
    """Require the elevated role used by the existing creation API."""
    current_user, _membership, redirect = await _main()._require_administration_access(
        request
    )
    if redirect:
        return current_user, redirect
    if not current_user.get("is_super_admin"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Super admin privileges required to create existing subscriptions",
        )
    return current_user, None


async def _creation_form_options() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    companies = await companies_repo.list_companies()
    products = await shop_repo.list_products_summary(
        shop_repo.ProductFilters(include_archived=False, sort="name_asc")
    )
    subscription_products = [
        product
        for product in products
        if product.get("subscription_category_id") is not None
    ]
    return companies, subscription_products


async def _render_creation_form(
    request: Request,
    current_user: dict[str, Any],
    *,
    values: dict[str, Any] | None = None,
    errors: dict[str, str] | None = None,
    failure_message: str | None = None,
    response_status: int = status.HTTP_200_OK,
):
    companies, products = await _creation_form_options()
    response = await _main()._render_template(
        "admin/subscription_create.html",
        request,
        current_user,
        extra={
            "title": "Add external subscription",
            "companies": companies,
            "products": products,
            "form_values": values
            or {
                "start_date": date.today().isoformat(),
                "quantity": "1",
                "auto_renew": True,
            },
            "form_errors": errors or {},
            "failure_message": failure_message,
        },
    )
    response.status_code = response_status
    return response


@router.get("/admin/subscriptions/create", response_class=HTMLResponse)
async def admin_create_subscription_page(request: Request):
    """Show the super-admin form for registering an externally billed subscription."""
    current_user, redirect = await _require_super_admin(request)
    if redirect:
        return redirect
    return await _render_creation_form(request, current_user)


@router.post("/admin/subscriptions/create", response_class=HTMLResponse)
async def admin_create_subscription(request: Request):
    """Validate the form and delegate creation to the existing API operation."""
    current_user, redirect = await _require_super_admin(request)
    if redirect:
        return redirect

    form = await parse_csrf_form(request)
    values = {
        "customer_id": str(form.get("customer_id", "")).strip(),
        "product_id": str(form.get("product_id", "")).strip(),
        "start_date": str(form.get("start_date", "")).strip(),
        "quantity": str(form.get("quantity", "")).strip(),
        "auto_renew": form.get("auto_renew") == "on",
    }
    try:
        payload = CreateExistingSubscriptionRequest.model_validate(values)
    except ValidationError as exc:
        labels = {
            "customer_id": "Customer",
            "product_id": "Subscription product",
            "start_date": "Start date",
            "quantity": "Quantity",
        }
        errors: dict[str, str] = {}
        for issue in exc.errors():
            field = str(issue["loc"][0])
            errors[field] = (
                f"{labels.get(field, field.replace('_', ' ').title())}: {issue['msg']}."
            )
        return await _render_creation_form(
            request,
            current_user,
            values=values,
            errors=errors,
            response_status=status.HTTP_422_UNPROCESSABLE_ENTITY,
        )

    try:
        subscription = await create_existing_subscription(payload, None, current_user)
    except HTTPException as exc:
        return await _render_creation_form(
            request,
            current_user,
            values=values,
            failure_message=str(exc.detail),
            response_status=exc.status_code,
        )
    except Exception as exc:  # pragma: no cover - defensive logging
        log_error("Failed to manually create subscription", error=str(exc))
        return await _render_creation_form(
            request,
            current_user,
            values=values,
            failure_message="The subscription could not be created. Please try again.",
            response_status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    message = quote(f"Subscription {subscription.id} was created successfully")
    return RedirectResponse(
        url=f"/admin/subscriptions?success={message}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


def _main():
    """Return the ``app.main`` module (lazy import to avoid circular imports)."""
    from app import main as main_module

    return main_module


@router.get("/admin/subscriptions", response_class=HTMLResponse)
async def admin_subscriptions_page(
    request: Request,
    status_filter: str | None = Query(default=None, alias="status"),
    category_filter: str | None = Query(default=None, alias="category"),
):
    """Admin page for viewing and managing subscriptions."""
    current_user, membership, redirect = await _main()._require_administration_access(
        request
    )
    if redirect:
        return redirect

    has_license = bool(membership and membership.get("can_manage_licenses"))
    has_cart = bool(membership and membership.get("can_access_cart"))

    if not (current_user.get("is_super_admin") or (has_license and has_cart)):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="License and Cart permissions required to access subscriptions",
        )

    active_company_id = getattr(request.state, "active_company_id", None)

    try:
        subs = await subscriptions_repo.list_subscriptions(
            customer_id=active_company_id
            if not current_user.get("is_super_admin")
            else None,
            status=status_filter,
            category_id=int(category_filter) if category_filter else None,
            limit=500,
        )

        categories = await categories_repo.list_categories()
        for sub in subs:
            if is_voice_monitor_subscription(sub):
                sub["voice_monitor_contract"] = contract_display(sub)
        status_counts: Counter[str | None] = Counter(sub.get("status") for sub in subs)

    except Exception as exc:  # pragma: no cover - defensive logging
        log_error("Failed to load subscriptions", error=str(exc))
        subs = []
        categories = []
        status_counts = Counter()

    extra: dict[str, Any] = {
        "title": "Subscriptions",
        "subscriptions": subs,
        "categories": categories,
        "filters": {
            "status": status_filter,
            "category": category_filter,
        },
        "status_counts": status_counts,
        "is_super_admin": current_user.get("is_super_admin", False),
    }

    return await _main()._render_template(
        "admin/subscriptions.html", request, current_user, extra=extra
    )


__all__ = ["router"]
