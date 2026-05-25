"""Public-portal subscription routes for the ``subscriptions`` feature pack.

Owns the portal subscription pages:

* ``GET  /subscriptions``                              — portal subscription list.
* ``POST /subscriptions/{subscription_id}/request-change`` — request a change.

Handler code migrated from ``app/main.py``.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from app.core.logging import log_error, log_info
from app.repositories import companies as company_repo
from app.repositories import subscriptions as subscriptions_repo
from app.repositories import user_companies as user_company_repo


router = APIRouter(tags=["Subscriptions"])


def _main():
    """Return the ``app.main`` module (lazy import to avoid circular imports)."""
    from app import main as main_module

    return main_module


async def _load_subscription_context(request: Request):
    """Load context for subscription-related pages.

    Requires the user to have both can_manage_licenses AND can_access_cart
    permissions (or to be a super admin).
    """
    user, redirect = await _main()._require_authenticated_user(request)
    if redirect:
        return user, None, None, None, redirect

    is_super_admin = bool(user.get("is_super_admin"))
    company_id_raw = user.get("company_id")
    if company_id_raw is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No company associated with the current user",
        )
    try:
        company_id = int(company_id_raw)
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid company identifier",
        )

    membership = await user_company_repo.get_user_company(user["id"], company_id)
    has_license = bool(membership and membership.get("can_manage_licenses"))
    has_cart = bool(membership and membership.get("can_access_cart"))

    if not (is_super_admin or (has_license and has_cart)):
        return (
            user,
            membership,
            None,
            company_id,
            RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER),
        )

    company = await company_repo.get_company_by_id(company_id)
    return user, membership, company, company_id, None


@router.get("/subscriptions", response_class=HTMLResponse)
async def subscriptions_page(request: Request):
    """Display active subscriptions for the current company."""
    user, membership, company, company_id, redirect = await _load_subscription_context(request)
    if redirect:
        return redirect

    subs = await subscriptions_repo.list_subscriptions(
        customer_id=company_id,
        limit=500,
    )

    formatted: list[dict[str, Any]] = []
    for sub in subs:
        formatted_sub = dict(sub)
        if isinstance(sub.get("start_date"), datetime):
            formatted_sub["start_date"] = sub["start_date"].strftime("%Y-%m-%d")
        elif isinstance(sub.get("start_date"), date):
            formatted_sub["start_date"] = sub["start_date"].strftime("%Y-%m-%d")

        if isinstance(sub.get("end_date"), datetime):
            formatted_sub["end_date"] = sub["end_date"].strftime("%Y-%m-%d")
        elif isinstance(sub.get("end_date"), date):
            formatted_sub["end_date"] = sub["end_date"].strftime("%Y-%m-%d")

        formatted_sub["contract_term"] = ""
        formatted.append(formatted_sub)

    is_super_admin = bool(user.get("is_super_admin"))
    can_request_changes = bool(
        is_super_admin
        or (
            membership
            and membership.get("can_manage_licenses")
            and membership.get("can_access_cart")
        )
    )

    extra: dict[str, Any] = {
        "title": "Subscriptions",
        "subscriptions": formatted,
        "company": company,
        "can_request_changes": can_request_changes,
        "is_super_admin": is_super_admin,
    }
    return await _main()._render_template("subscriptions/index.html", request, user, extra=extra)


@router.post("/subscriptions/{subscription_id}/request-change", response_class=JSONResponse)
async def request_subscription_change(request: Request, subscription_id: str):
    """Request a quantity change for a subscription."""
    user, membership, _, company_id, redirect = await _load_subscription_context(request)
    if redirect:
        return redirect

    subscription = await subscriptions_repo.get_subscription(subscription_id)
    if not subscription or int(subscription.get("customer_id", 0)) != company_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Subscription not found",
        )

    try:
        payload = await request.json()
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid payload",
        ) from exc

    new_quantity = payload.get("quantity")
    if new_quantity is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Quantity is required",
        )

    try:
        new_quantity = int(new_quantity)
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Quantity must be a valid integer",
        )

    if new_quantity < 0:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Quantity cannot be negative",
        )

    current_quantity = subscription.get("quantity", 0)
    if new_quantity == current_quantity:
        return JSONResponse(
            {
                "success": False,
                "message": "No change requested - the new quantity is the same as the current quantity",
            }
        )

    reason = payload.get("reason")

    log_info(
        "Subscription change requested",
        subscription_id=subscription_id,
        company_id=company_id,
        current_quantity=subscription.get("quantity"),
        requested_quantity=new_quantity,
        reason=reason,
        user_id=user.get("id"),
    )

    product_name = subscription.get("product_name") or "Unknown Product"
    subject = f"Subscription Change Request - {product_name}"

    description_parts = [
        "A subscription change has been requested.",
        "",
        "**Subscription Details:**",
        f"- Product: {product_name}",
        f"- Subscription ID: {subscription_id}",
        f"- Current Quantity: {current_quantity}",
        f"- Requested Quantity: {new_quantity}",
    ]

    if reason:
        description_parts.extend(
            [
                "",
                "**Reason for Change:**",
                reason,
            ]
        )

    description = "\n".join(description_parts)

    user_id = user.get("id")
    from app.services import tickets as tickets_service  # noqa: PLC0415 – avoid circular import

    try:
        ticket = await tickets_service.create_ticket(
            subject=subject,
            description=description,
            requester_id=user_id,
            company_id=company_id,
            assigned_user_id=None,
            priority="normal",
            status="open",
            category="subscription",
            module_slug=None,
            external_reference=f"subscription:{subscription_id}",
            trigger_automations=True,
        )

        ticket_id = ticket.get("id")
        ticket_number = ticket.get("ticket_number")

        log_info(
            "Ticket created for subscription change request",
            ticket_id=ticket_id,
            ticket_number=ticket_number,
            subscription_id=subscription_id,
        )

        return JSONResponse(
            {
                "success": True,
                "message": "Change request submitted and ticket created",
                "ticket_id": ticket_id,
                "ticket_number": ticket_number,
            }
        )
    except Exception as exc:
        log_error(
            "Failed to create ticket for subscription change request",
            subscription_id=subscription_id,
            error=str(exc),
        )
        return JSONResponse(
            {
                "success": False,
                "message": "Change request submitted but ticket creation failed",
            }
        )


__all__ = ["router"]
