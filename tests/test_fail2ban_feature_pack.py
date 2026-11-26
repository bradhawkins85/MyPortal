"""Smoke tests for the ``fail2ban`` feature pack."""

from __future__ import annotations

import asyncio

from fastapi import FastAPI

from app.core.features import init_registry
from app.features.fail2ban import PACK
from app.features.fail2ban import handlers as fail2ban_handlers
from app.features.fail2ban import routes as fail2ban_routes

EXPECTED = {("HEAD", "/admin/fail2ban"), ("GET", "/admin/fail2ban")}


def _routes_for(app: FastAPI) -> set[tuple[str, str]]:
    return {
        (method, route.path)
        for route in app.router.routes
        if getattr(route, "path", None)
        for method in (getattr(route, "methods", None) or set())
    }


def test_fail2ban_pack_is_view_only():
    declared = {
        (method, route.path) for router in PACK.routers for route in router.routes for method in route.methods
    }
    assert PACK.slug == "fail2ban"
    assert PACK.version
    assert declared == EXPECTED
    assert fail2ban_routes.router.routes[0].endpoint == fail2ban_handlers.admin_fail2ban_page


def test_fail2ban_pack_loads_and_reloads_cleanly():
    async def _run() -> None:
        test_app = FastAPI()
        registry = init_registry(test_app)
        await registry.load("fail2ban")
        assert EXPECTED.issubset(_routes_for(test_app))
        await registry.reload("fail2ban")
        counts: dict[tuple[str, str], int] = {}
        for route in test_app.router.routes:
            for method in getattr(route, "methods", None) or set():
                counts[(method, route.path)] = counts.get((method, route.path), 0) + 1
        assert all(counts.get(key) == 1 for key in EXPECTED)
        await registry.unload_all()

    asyncio.new_event_loop().run_until_complete(_run())


def test_fail2ban_page_is_restricted_to_trusted_networks():
    from app.security.trusted_networks import DEFAULT_RESTRICTED_PATHS

    assert "/admin/fail2ban" in DEFAULT_RESTRICTED_PATHS
