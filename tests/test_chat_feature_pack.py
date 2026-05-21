"""Smoke tests for the ``chat`` feature pack."""

from __future__ import annotations

from fastapi import FastAPI

import app.main as main_module
from app.core.features import init_registry
from app.features.chat import PACK


EXPECTED = {
    ("GET", "/chat"),
    ("GET", "/chat/{room_id}"),
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


def test_chat_pack_manifest_declares_all_routes():
    """Manifest should expose exactly the routes that were migrated."""

    declared = set()
    for router in PACK.routers:
        for route in router.routes:
            for method in route.methods or set():
                declared.add((method, route.path))

    assert PACK.slug == "chat"
    assert PACK.version
    assert declared == EXPECTED


def test_app_main_no_longer_owns_chat_routes():
    """The routes must have been removed from ``app/main.py`` so that
    the pack is the sole owner — otherwise reloading the pack would
    leave stale handlers behind."""

    in_main_app = _routes_for(main_module.app)
    for method, path in EXPECTED:
        assert (method, path) not in in_main_app, (
            f"{method} {path} still mounted directly on app.main; "
            "feature-pack migration is incomplete."
        )


def test_chat_pack_loads_and_reloads_cleanly():
    """The pack should load via the registry, mount its routes, and
    survive a hot reload without leaking duplicate routes."""

    import asyncio

    async def _run() -> None:
        test_app = FastAPI()
        registry = init_registry(test_app)

        await registry.load("chat")
        after_load = _routes_for(test_app)
        assert EXPECTED.issubset(after_load)

        await registry.reload("chat")
        after_reload = _routes_for(test_app)
        assert EXPECTED.issubset(after_reload)

        # No duplicate routes after reload.
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
