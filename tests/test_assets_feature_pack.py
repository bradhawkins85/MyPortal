"""Smoke tests for the ``assets`` feature pack."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

from fastapi import FastAPI
import pytest
from starlette.requests import Request

import app.main as main_module
from app.core.features import init_registry
from app.features.assets import PACK
from app.features.assets import routes as assets_routes


EXPECTED = {
    ("GET", "/assets"),
    ("GET", "/assets/{asset_id}"),
    ("GET", "/assets/settings"),
    ("DELETE", "/assets/{asset_id}"),
}


async def _dummy_receive() -> dict[str, Any]:
    return {"type": "http.request", "body": b"", "more_body": False}


def _make_request(path: str, method: str = "GET") -> Request:
    scope = {
        "type": "http",
        "method": method,
        "path": path,
        "query_string": b"",
        "headers": [],
        "scheme": "http",
        "server": ("testserver", 80),
    }
    return Request(scope, _dummy_receive)


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


def test_assets_pack_manifest_declares_all_routes():
    """Manifest should expose exactly the routes that were migrated."""

    declared = set()
    for router in PACK.routers:
        for route in router.routes:
            for method in route.methods or set():
                declared.add((method, route.path))

    assert PACK.slug == "assets"
    assert PACK.version
    assert declared == EXPECTED


def test_app_main_no_longer_owns_assets_routes():
    """The routes must have been removed from ``app/main.py`` so that
    the pack is the sole owner — otherwise reloading the pack would
    leave stale handlers behind."""

    in_main_app = _routes_for(main_module.app)
    for method, path in EXPECTED:
        assert (method, path) not in in_main_app, (
            f"{method} {path} still mounted directly on app.main; "
            "feature-pack migration is incomplete."
        )


def test_asset_helpers_moved_out_of_main_module():
    """Assets-only helpers should live with the assets feature pack."""

    assert not hasattr(main_module, "_load_asset_context")
    assert not hasattr(main_module, "_ASSET_TABLE_COLUMNS")
    assert hasattr(assets_routes, "_load_asset_context")
    assert hasattr(assets_routes, "_ASSET_TABLE_COLUMNS")


def test_assets_pack_loads_and_reloads_cleanly():
    """The pack should load via the registry, mount its routes, and
    survive a hot reload without leaking duplicate routes."""

    import asyncio

    async def _run() -> None:
        test_app = FastAPI()
        registry = init_registry(test_app)

        await registry.load("assets")
        after_load = _routes_for(test_app)
        assert EXPECTED.issubset(after_load)

        await registry.reload("assets")
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


@pytest.mark.anyio
async def test_asset_detail_page_redirects_to_assets_anchor(monkeypatch):
    import app.repositories.assets as asset_repo

    monkeypatch.setattr(
        assets_routes,
        "_load_asset_context",
        AsyncMock(return_value=({"id": 7}, None, {"id": 3}, 3, None)),
    )
    monkeypatch.setattr(
        asset_repo,
        "get_asset_by_id",
        AsyncMock(return_value={"id": 42, "company_id": 3}),
    )

    response = await assets_routes.asset_detail_page(_make_request("/assets/42"), 42)

    assert response.status_code == 303
    assert response.headers["location"] == "/assets#asset-42"


@pytest.mark.anyio
async def test_asset_detail_page_rejects_assets_from_other_companies(monkeypatch):
    import app.repositories.assets as asset_repo

    monkeypatch.setattr(
        assets_routes,
        "_load_asset_context",
        AsyncMock(return_value=({"id": 7}, None, {"id": 3}, 3, None)),
    )
    monkeypatch.setattr(
        asset_repo,
        "get_asset_by_id",
        AsyncMock(return_value={"id": 42, "company_id": 9}),
    )

    with pytest.raises(assets_routes.HTTPException) as excinfo:
        await assets_routes.asset_detail_page(_make_request("/assets/42"), 42)

    assert excinfo.value.status_code == 404
