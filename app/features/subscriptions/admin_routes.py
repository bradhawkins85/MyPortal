"""Admin subscription routes for the ``subscriptions`` feature pack.

Owns the admin subscription management page:

* ``GET /admin/subscriptions`` — admin subscription management page.

Handler code migrated from ``app/main.py``.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse

from app.core.logging import log_error
from app.repositories import subscription_categories as categories_repo
from app.repositories import subscriptions as subscriptions_repo


router = APIRouter(tags=["Subscriptions"])


def _main():
    """Return the ``app.main`` module (lazy import to avoid circular imports)."""
    from app import main as main_module

    return main_module


@router.get("/admin/subscriptions", response_class=HTMLResponse)
async def admin_subscriptions_page(
    request: Request,
    status_filter: str | None = Query(default=None, alias="status"),
    category_filter: str | None = Query(default=None, alias="category"),
    success: str | None = Query(default=None),
    error: str | None = Query(default=None),
):
    """Admin page for viewing and managing subscriptions."""
    current_user, membership, redirect = await _main()._require_administration_access(request)
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
            customer_id=active_company_id if not current_user.get("is_super_admin") else None,
            status=status_filter,
            category_id=int(category_filter) if category_filter else None,
            limit=500,
        )

        categories = await categories_repo.list_categories()
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
        "success_message": _main()._sanitize_message(success),
        "error_message": _main()._sanitize_message(error),
        "is_super_admin": current_user.get("is_super_admin", False),
    }

    return await _main()._render_template(
        "admin/subscriptions.html", request, current_user, extra=extra
    )


__all__ = ["router"]
