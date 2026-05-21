"""Cart and orders routes for the ``cart`` feature pack."""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import HTMLResponse, RedirectResponse


router = APIRouter(tags=["Cart"])


def _main():
    from app import main as main_module

    return main_module


def _add(path: str, endpoint_name: str, methods: list[str], **kwargs) -> None:
    router.add_api_route(path, getattr(_main(), endpoint_name), methods=methods, **kwargs)


# Cart portal routes.
_add("/cart/add", "add_to_cart", ["POST"], response_class=RedirectResponse, include_in_schema=False)
_add(
    "/cart/add-package",
    "add_package_to_cart",
    ["POST"],
    response_class=RedirectResponse,
    include_in_schema=False,
)
_add("/cart", "view_cart", ["GET"], response_class=HTMLResponse, name="cart_page")
_add(
    "/cart/update",
    "update_cart_items",
    ["POST"],
    response_class=RedirectResponse,
    name="cart_update_items",
    include_in_schema=False,
)
_add(
    "/cart/remove",
    "remove_cart_items",
    ["POST"],
    response_class=RedirectResponse,
    name="cart_remove_items",
    include_in_schema=False,
)
_add(
    "/cart/place-order",
    "place_order",
    ["POST"],
    response_class=RedirectResponse,
    name="cart_place_order",
    include_in_schema=False,
)

# Orders portal route.
_add("/orders", "orders_page", ["GET"], response_class=HTMLResponse)


__all__ = ["router"]
