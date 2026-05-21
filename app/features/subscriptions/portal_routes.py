"""Public-portal subscription routes for the ``subscriptions`` feature pack.

Mirrors the routes that used to live in ``app/main.py``:

* ``GET  /subscriptions``                              — portal subscription list.
* ``POST /subscriptions/{subscription_id}/request-change`` — request a change.

URLs and behaviour are intentionally identical to the previous in-line
handlers so external links, bookmarks, and tests keep working after
the migration.
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import HTMLResponse, JSONResponse


router = APIRouter(tags=["Subscriptions"])


def _main():
    """Return the ``app.main`` module (lazy import)."""
    from app import main as main_module

    return main_module


def _add(path: str, endpoint_name: str, methods: list[str], **kwargs) -> None:
    router.add_api_route(path, getattr(_main(), endpoint_name), methods=methods, **kwargs)


# Portal subscription routes.
_add("/subscriptions", "subscriptions_page", ["GET"], response_class=HTMLResponse)
_add(
    "/subscriptions/{subscription_id}/request-change",
    "request_subscription_change",
    ["POST"],
    response_class=JSONResponse,
)


__all__ = ["router"]
