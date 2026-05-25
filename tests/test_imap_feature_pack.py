"""Smoke tests for the ``imap`` feature pack."""

from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import FastAPI

import app.main as main_module
from app.core.config import Settings
from app.core.features import init_registry
from app.features.imap import PACK


PROJECT_ROOT = Path(__file__).resolve().parents[1]

EXPECTED = {
    ("GET", "/imap/accounts"),
    ("POST", "/imap/accounts"),
    ("GET", "/imap/accounts/{account_id}"),
    ("PUT", "/imap/accounts/{account_id}"),
    ("DELETE", "/imap/accounts/{account_id}"),
    ("POST", "/imap/accounts/{account_id}/sync"),
    ("POST", "/imap/accounts/{account_id}/clone"),
    ("GET", "/admin/modules/imap"),
    ("POST", "/admin/modules/imap/accounts"),
    ("POST", "/admin/modules/imap/accounts/{account_id}"),
    ("POST", "/admin/modules/imap/accounts/{account_id}/clone"),
    ("POST", "/admin/modules/imap/accounts/{account_id}/delete"),
    ("POST", "/admin/modules/imap/accounts/{account_id}/sync"),
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


def test_imap_pack_manifest_declares_all_routes():
    declared = set()
    for router in PACK.routers:
        for route in router.routes:
            for method in route.methods or set():
                declared.add((method, route.path))

    assert PACK.slug == "imap"
    assert PACK.version
    assert declared == EXPECTED


def test_imap_pack_routes_are_implemented_in_feature_pack():
    for router in PACK.routers:
        for route in router.routes:
            assert route.endpoint.__module__.startswith("app.features.imap"), (
                f"Route {route.path} is implemented in {route.endpoint.__module__}; "
                "imap feature-pack code should live under app.features.imap."
            )


def test_imap_pack_is_enabled_by_default():
    default_feature_packs = str(Settings.model_fields["feature_packs"].default).split(",")
    assert "imap" in default_feature_packs

    env_example = (PROJECT_ROOT / ".env.example").read_text(encoding="utf-8")
    assert "imap" in env_example


def test_app_main_no_longer_owns_imap_routes():
    in_main_app = _routes_for(main_module.app)
    for method, path in EXPECTED:
        assert (method, path) not in in_main_app, (
            f"{method} {path} still mounted directly on app.main; "
            "feature-pack migration is incomplete."
        )


def test_imap_pack_loads_and_reloads_cleanly():
    async def _run() -> None:
        test_app = FastAPI()
        registry = init_registry(test_app)

        await registry.load("imap")
        after_load = _routes_for(test_app)
        assert EXPECTED.issubset(after_load)

        await registry.reload("imap")
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
