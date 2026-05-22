"""Orders page routes for the ``orders`` feature pack."""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import HTMLResponse


router = APIRouter(tags=["Orders"])


def _main():
    from app import main as main_module

    return main_module


router.add_api_route(
    "/orders",
    _main().orders_page,
    methods=["GET"],
    response_class=HTMLResponse,
)


__all__ = ["router"]
