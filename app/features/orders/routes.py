"""Orders page routes for the ``orders`` feature pack."""

from __future__ import annotations

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse


router = APIRouter(tags=["Orders"])


def _main():
    from app import main as main_module

    return main_module


@router.get("/orders", response_class=HTMLResponse)
async def route_orders_page(
    request: Request,
    status_filter: str | None = Query(None, alias="status"),
    shipping_filter: str | None = Query(None, alias="shippingStatus"),
):
    return await _main().orders_page(
        request=request,
        status_filter=status_filter,
        shipping_filter=shipping_filter,
    )


__all__ = ["router"]
