"""Smoke tests for the ``continuity`` feature pack."""

from __future__ import annotations

from fastapi import FastAPI

import app.main as main_module
from app.core.features import init_registry
from app.features.continuity import PACK
from app.features.continuity import routes as continuity_routes


EXPECTED = {
    ("GET", "/admin/business-continuity-plans"),
    ("GET", "/admin/business-continuity-plans/new"),
    ("GET", "/admin/business-continuity-plans/{plan_id}"),
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


def test_continuity_pack_manifest_declares_all_routes():
    declared = set()
    for router in PACK.routers:
        for route in router.routes:
            for method in route.methods or set():
                declared.add((method, route.path))

    assert PACK.slug == "continuity"
    assert PACK.version
    assert declared == EXPECTED


def test_app_main_no_longer_owns_continuity_routes():
    in_main_app = _routes_for(main_module.app)
    for method, path in EXPECTED:
        assert (method, path) not in in_main_app, (
            f"{method} {path} still mounted directly on app.main; "
            "feature-pack migration is incomplete."
        )
    for name in (
        "admin_business_continuity_plans_page",
        "admin_new_business_continuity_plan_page",
        "admin_edit_business_continuity_plan_page",
    ):
        assert not hasattr(main_module, name), (
            f"app.main still defines {name}; "
            "feature-pack migration is incomplete."
        )


def test_continuity_pack_owns_continuity_handlers():
    for name in (
        "admin_business_continuity_plans_page",
        "admin_new_business_continuity_plan_page",
        "admin_edit_business_continuity_plan_page",
    ):
        assert hasattr(continuity_routes, name)


def test_continuity_pack_loads_and_reloads_cleanly():
    import asyncio

    async def _run() -> None:
        test_app = FastAPI()
        registry = init_registry(test_app)

        await registry.load("continuity")
        after_load = _routes_for(test_app)
        assert EXPECTED.issubset(after_load)

        await registry.reload("continuity")
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
