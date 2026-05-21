"""Admin subscription routes for the ``subscriptions`` feature pack.

Mirrors the routes that used to live in ``app/main.py``:

* ``GET /admin/subscriptions`` — admin subscription management page.

URLs and behaviour are intentionally identical to the previous in-line
handlers so external links, bookmarks, and tests keep working after
the migration.
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import HTMLResponse


router = APIRouter(tags=["Subscriptions"])


def _main():
    """Return the ``app.main`` module (lazy import)."""
    from app import main as main_module

    return main_module


def _add(path: str, endpoint_name: str, methods: list[str], **kwargs) -> None:
    router.add_api_route(path, getattr(_main(), endpoint_name), methods=methods, **kwargs)


# Admin subscription routes.
_add("/admin/subscriptions", "admin_subscriptions_page", ["GET"], response_class=HTMLResponse)


__all__ = ["router"]
