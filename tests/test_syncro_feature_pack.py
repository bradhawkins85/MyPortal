"""Smoke tests for the ``syncro`` feature pack."""

from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import FastAPI

import app.main as main_module
from app.core.config import Settings
from app.core.features import init_registry
from app.features.syncro import PACK


PROJECT_ROOT = Path(__file__).resolve().parents[1]

EXPECTED = {
    ("GET", "/admin/tickets/syncro-import"),
    ("POST", "/admin/syncro/import-contacts"),
    ("POST", "/admin/syncro/import-companies"),
    ("POST", "/admin/syncro/import-tickets"),
}


def _routes_for(app: FastAPI) -> set[tuple[str, str]]:
    def visit(route) -> None:
        original_router = getattr(route, "original_router", None)
        nested_routes = getattr(original_router, "routes", None) or getattr(route, "routes", None)
        if nested_routes:
            for nested_route in nested_routes:
                visit(nested_route)
            return
        path = getattr(route, "path", None)
        methods = getattr(route, "methods", None) or set()
        if not path:
            return
        for method in methods:
            routes.add((method, path))

    routes: set[tuple[str, str]] = set()
    for route in app.router.routes:
        visit(route)
    return routes


def test_syncro_pack_manifest_declares_all_routes():
    declared = set()
    for router in PACK.routers:
        for route in router.routes:
            for method in route.methods or set():
                declared.add((method, route.path))

    assert PACK.slug == "syncro"
    assert PACK.version
    assert declared == EXPECTED


def test_syncro_import_route_wins_when_tickets_pack_loads_first():
    from starlette.routing import Match

    from app.features.tickets import PACK as tickets_pack

    test_app = FastAPI()
    for router in tickets_pack.routers:
        test_app.include_router(router)
    for router in PACK.routers:
        test_app.include_router(router)

    scope = {
        "type": "http",
        "method": "GET",
        "path": "/admin/tickets/syncro-import",
        "root_path": "",
        "headers": [],
        "query_string": b"",
    }

    for route in test_app.router.routes:
        match, _ = route.matches(scope)
        if match is Match.FULL:
            candidates = getattr(route, "effective_candidates", lambda: [route])()
            assert any(
                getattr(candidate, "path", None) == "/admin/tickets/syncro-import"
                for candidate in candidates
            )
            break
    else:  # pragma: no cover - defensive assertion failure path
        raise AssertionError("No route matched /admin/tickets/syncro-import")


def test_syncro_pack_is_enabled_by_default():
    default_feature_packs = str(Settings.model_fields["feature_packs"].default).split(",")
    assert "syncro" in default_feature_packs

    env_example = (PROJECT_ROOT / ".env.example").read_text(encoding="utf-8")
    assert "syncro" in env_example


def test_app_main_no_longer_owns_syncro_routes():
    in_main_app = _routes_for(main_module.app)
    for method, path in EXPECTED:
        assert (method, path) not in in_main_app, (
            f"{method} {path} still mounted directly on app.main; "
            "feature-pack migration is incomplete."
        )


def test_syncro_pack_loads_and_reloads_cleanly():
    async def _run() -> None:
        test_app = FastAPI()
        registry = init_registry(test_app)

        await registry.load("syncro")
        after_load = _routes_for(test_app)
        assert EXPECTED.issubset(after_load)

        await registry.reload("syncro")
        after_reload = _routes_for(test_app)
        assert EXPECTED.issubset(after_reload)

        for key in EXPECTED:
            assert key in after_reload

        await registry.unload_all()

    asyncio.new_event_loop().run_until_complete(_run())
