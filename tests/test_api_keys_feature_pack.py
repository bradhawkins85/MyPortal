"""Smoke tests for the ``api_keys`` feature pack."""

from __future__ import annotations

from fastapi import FastAPI

import app.main as main_module
from app.core.features import init_registry
from app.features.api_keys import PACK
from app.features.api_keys import handlers as api_key_handlers
from app.features.api_keys import routes as api_key_routes


EXPECTED = {
    ("GET", "/admin/api-keys"),
    ("POST", "/admin/api-keys"),
    ("POST", "/admin/api-keys/update"),
    ("POST", "/admin/api-keys/rotate"),
    ("POST", "/admin/api-keys/delete"),
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


def test_api_keys_pack_manifest_declares_all_routes():
    declared = set()
    for router in PACK.routers:
        for route in router.routes:
            for method in route.methods or set():
                declared.add((method, route.path))

    assert PACK.slug == "api_keys"
    assert PACK.version
    assert declared == EXPECTED


def test_app_main_no_longer_owns_api_keys_routes():
    in_main_app = _routes_for(main_module.app)
    for method, path in EXPECTED:
        assert (method, path) not in in_main_app, (
            f"{method} {path} still mounted directly on app.main; "
            "feature-pack migration is incomplete."
        )


def test_api_keys_pack_owns_handlers():
    assert api_key_routes.router.routes[0].endpoint == api_key_handlers.admin_api_keys_page
    assert api_key_routes.router.routes[1].endpoint == api_key_handlers.admin_create_api_key_page
    assert api_key_routes.router.routes[2].endpoint == api_key_handlers.admin_update_api_key_page
    assert api_key_routes.router.routes[3].endpoint == api_key_handlers.admin_rotate_api_key_page
    assert api_key_routes.router.routes[4].endpoint == api_key_handlers.admin_delete_api_key_page


def test_app_main_no_longer_defines_api_key_admin_handlers():
    for name in (
        "_render_api_keys_dashboard",
        "admin_api_keys_page",
        "admin_create_api_key_page",
        "admin_update_api_key_page",
        "admin_rotate_api_key_page",
        "admin_delete_api_key_page",
    ):
        assert not hasattr(main_module, name), (
            f"app.main still defines {name}; "
            "feature-pack migration is incomplete."
        )


def test_api_keys_pack_loads_and_reloads_cleanly():
    import asyncio

    async def _run() -> None:
        test_app = FastAPI()
        registry = init_registry(test_app)

        await registry.load("api_keys")
        after_load = _routes_for(test_app)
        assert EXPECTED.issubset(after_load)

        await registry.reload("api_keys")
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
