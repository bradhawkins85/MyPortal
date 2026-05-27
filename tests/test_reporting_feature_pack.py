"""Smoke tests for the ``reporting`` feature pack."""

from __future__ import annotations

from fastapi import FastAPI

import app.main as main_module
from app.core.features import init_registry
from app.features.reporting import PACK
from app.features.reporting import handlers as reporting_handlers
from app.features.reporting import routes as reporting_routes


EXPECTED = {
    ("GET", "/reporting"),
    ("GET", "/reporting/{report_id}/export"),
    ("GET", "/admin/reporting"),
    ("GET", "/admin/reporting/new"),
    ("GET", "/admin/reporting/{report_id}/edit"),
    ("POST", "/admin/reporting"),
    ("POST", "/admin/reporting/{report_id}"),
    ("POST", "/admin/reporting/{report_id}/delete"),
}


def _routes_for(app: FastAPI) -> set[tuple[str, str]]:
    routes: set[tuple[str, str]] = set()
    for route in app.router.routes:
        path = getattr(route, "path", None)
        methods = getattr(route, "methods", None) or set()
        if not path:
            continue
        for method in methods:
            routes.add((method, path))
    return routes


def test_reporting_pack_manifest_declares_all_routes():
    declared = set()
    for router in PACK.routers:
        for route in router.routes:
            for method in route.methods or set():
                declared.add((method, route.path))

    assert PACK.slug == "reporting"
    assert PACK.version
    assert declared == EXPECTED


def test_app_main_no_longer_owns_reporting_routes():
    in_main_app = _routes_for(main_module.app)
    for method, path in EXPECTED:
        assert (method, path) not in in_main_app, (
            f"{method} {path} still mounted directly on app.main; "
            "feature-pack migration is incomplete."
        )


def test_reporting_pack_owns_handlers():
    assert reporting_routes.router.routes[0].endpoint == reporting_handlers.reporting_page
    assert reporting_routes.router.routes[1].endpoint == reporting_handlers.reporting_export
    assert reporting_routes.router.routes[2].endpoint == reporting_handlers.admin_reporting
    assert reporting_routes.router.routes[3].endpoint == reporting_handlers.admin_reporting_new
    assert reporting_routes.router.routes[4].endpoint == reporting_handlers.admin_reporting_edit
    assert reporting_routes.router.routes[5].endpoint == reporting_handlers.admin_reporting_create
    assert reporting_routes.router.routes[6].endpoint == reporting_handlers.admin_reporting_update
    assert reporting_routes.router.routes[7].endpoint == reporting_handlers.admin_reporting_delete


def test_reporting_pack_loads_and_reloads_cleanly():
    import asyncio

    async def _run() -> None:
        test_app = FastAPI()
        registry = init_registry(test_app)

        await registry.load("reporting")
        after_load = _routes_for(test_app)
        assert EXPECTED.issubset(after_load)

        await registry.reload("reporting")
        after_reload = _routes_for(test_app)
        assert EXPECTED.issubset(after_reload)

        counts: dict[tuple[str, str], int] = {}
        for route in test_app.router.routes:
            path = getattr(route, "path", None)
            for method in getattr(route, "methods", None) or set():
                if path:
                    counts[(method, path)] = counts.get((method, path), 0) + 1
        for key in EXPECTED:
            assert counts.get(key, 0) == 1, (
                f"Route {key} duplicated after reload (count={counts.get(key)})"
            )

        await registry.unload_all()

    asyncio.new_event_loop().run_until_complete(_run())
