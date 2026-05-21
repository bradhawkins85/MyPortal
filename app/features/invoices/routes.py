"""Portal invoice routes for the ``invoices`` feature pack."""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import HTMLResponse


router = APIRouter(tags=["Invoices"])


def _main():
    from app import main as main_module

    return main_module


def _add(path: str, endpoint_name: str, methods: list[str], **kwargs) -> None:
    router.add_api_route(path, getattr(_main(), endpoint_name), methods=methods, **kwargs)


_add("/invoices", "invoices_page", ["GET"], response_class=HTMLResponse)
_add("/invoices/{invoice_id}", "invoice_detail_page", ["GET", "HEAD"], response_class=HTMLResponse)


__all__ = ["router"]
