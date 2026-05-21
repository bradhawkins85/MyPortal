"""Portal quote routes for the ``quotes`` feature pack."""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import HTMLResponse, RedirectResponse


router = APIRouter(tags=["Quotes"])


def _main():
    from app import main as main_module

    return main_module


def _add(path: str, endpoint_name: str, methods: list[str], **kwargs) -> None:
    router.add_api_route(path, getattr(_main(), endpoint_name), methods=methods, **kwargs)


_add("/quotes", "quotes_page", ["GET"], response_class=HTMLResponse)
_add(
    "/quotes/load/{quote_number}",
    "load_quote_to_cart",
    ["POST"],
    response_class=RedirectResponse,
    name="load_quote",
    include_in_schema=False,
)
_add(
    "/cart/save-as-quote",
    "save_as_quote",
    ["POST"],
    response_class=RedirectResponse,
    name="cart_save_quote",
    include_in_schema=False,
)


__all__ = ["router"]
