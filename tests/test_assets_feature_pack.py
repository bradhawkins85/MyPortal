"""Smoke tests for the ``assets`` feature pack."""

from __future__ import annotations

from types import SimpleNamespace
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
    ("GET", "/assets/new"),
    ("POST", "/assets"),
    ("GET", "/assets/{asset_id}"),
    ("GET", "/assets/settings"),
    ("GET", "/devices"),
    ("POST", "/devices/discovered/{device_id}"),
    ("POST", "/devices/discovered/{device_id}/hudu-sync"),
    ("POST", "/devices/discovered-bulk-update"),
    ("POST", "/devices/discovered-purge"),
    ("POST", "/devices/alerts"),
    ("POST", "/devices/device-types"),
    ("POST", "/devices/device-types/{device_type_id}"),
    ("POST", "/devices/device-types/{device_type_id}/delete"),
    ("POST", "/devices/scanners"),
    ("POST", "/devices/scanners/{device_id}"),
    ("POST", "/devices/scanners/{device_id}/scan"),
    ("POST", "/assets/settings/device-types"),
    ("POST", "/assets/settings/device-types/{device_type_id}/delete"),
    ("POST", "/assets/settings/required-fields"),
    ("POST", "/assets/{asset_id}"),
    ("POST", "/assets/{asset_id}/photos"),
    ("GET", "/assets/{asset_id}/photos/{photo_id}/{variant}"),
    ("POST", "/assets/{asset_id}/photos/{photo_id}"),
    ("POST", "/assets/{asset_id}/photos/{photo_id}/delete"),
    ("POST", "/assets/{asset_id}/archive"),
    ("POST", "/assets/{asset_id}/reconciliation/{source_record_id}/approve"),
    ("POST", "/assets/{asset_id}/relationships"),
    ("POST", "/assets/{asset_id}/relationships/{relationship_id}/delete"),
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
    assert EXPECTED.issubset(declared)


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
async def test_asset_detail_page_renders_canonical_asset(monkeypatch):
    import app.repositories.assets as asset_repo

    monkeypatch.setattr(
        assets_routes,
        "_load_asset_context",
        AsyncMock(return_value=({"id": 7}, None, {"id": 3}, 3, None)),
    )
    monkeypatch.setattr(
        asset_repo,
        "get_asset_by_id",
        AsyncMock(return_value={"id": 42, "company_id": 3, "customer_visible": True}),
    )
    monkeypatch.setattr(assets_routes.asset_custom_fields_repo, "list_field_definitions", AsyncMock(return_value=[]))
    monkeypatch.setattr(assets_routes.asset_custom_fields_repo, "get_all_asset_field_values", AsyncMock(return_value={}))
    monkeypatch.setattr(asset_repo, "list_required_fields", AsyncMock(return_value=[]))
    monkeypatch.setattr(asset_repo, "list_tickets_for_asset", AsyncMock(return_value=[]))
    monkeypatch.setattr(asset_repo, "list_company_assets", AsyncMock(return_value=[]))
    monkeypatch.setattr(asset_repo, "list_relationships_for_asset", AsyncMock(return_value=[]))
    monkeypatch.setattr(assets_routes.audience_repo, "list_role_ids", AsyncMock(return_value=[]))
    monkeypatch.setattr(assets_routes.asset_photo_repo, "list_for_asset", AsyncMock(return_value=[]))
    monkeypatch.setattr(
        assets_routes.knowledge_base_service,
        "build_access_context",
        AsyncMock(return_value=object()),
    )
    monkeypatch.setattr(
        assets_routes.knowledge_base_service,
        "list_articles_for_context",
        AsyncMock(return_value=[]),
    )
    renderer = AsyncMock(return_value=assets_routes.HTMLResponse("detail"))
    monkeypatch.setattr(main_module, "_render_template", renderer)

    response = await assets_routes.asset_detail_page(_make_request("/assets/42"), 42)

    assert response.status_code == 200
    assert renderer.await_args.args[0] == "assets/detail.html"


@pytest.mark.anyio
async def test_roleless_asset_access_is_limited_to_legacy_publications(monkeypatch):
    audiences = AsyncMock(side_effect=[[4], []])
    monkeypatch.setattr(assets_routes.audience_repo, "list_role_ids", audiences)

    assert not await assets_routes._customer_role_can_view_asset(
        {"role_id": None}, 3, 42
    )
    assert await assets_routes._customer_role_can_view_asset(
        {"role_id": None}, 3, 43
    )

    assert audiences.await_args_list[0].args == (3, "asset", 42)
    assert audiences.await_args_list[1].args == (3, "asset", 43)


@pytest.mark.anyio
async def test_asset_role_change_takes_effect_immediately(monkeypatch):
    allowed = AsyncMock(side_effect=[True, False])
    monkeypatch.setattr(assets_routes.audience_repo, "role_can_access", allowed)
    membership = {
        "role_id": 7,
        "menu_permissions": {"content.assets": "read"},
    }

    assert await assets_routes._customer_role_can_view_asset(membership, 3, 42)
    assert not await assets_routes._customer_role_can_view_asset(membership, 3, 42)
    assert allowed.await_count == 2


def _photo_permissions(_user, membership, key, *, write=False):
    value = (membership or {}).get(key)
    return value == "write" or (value == "read" and not write)


@pytest.mark.anyio
@pytest.mark.parametrize("variant", ["original", "thumbnail"])
async def test_direct_photo_variants_enforce_asset_role_audience(
    monkeypatch, variant
):
    membership = {
        "role_id": 8,
        "menu.asset_photos": "read",
        "menu.assets": "read",
        "menu_permissions": {"content.assets": "read"},
    }
    monkeypatch.setattr(
        assets_routes,
        "_load_asset_context",
        AsyncMock(return_value=({"id": 7}, membership, {"id": 3}, 3, None)),
    )
    monkeypatch.setattr(
        assets_routes,
        "_main",
        lambda: SimpleNamespace(_membership_menu_can=_photo_permissions),
    )
    monkeypatch.setattr(
        assets_routes.asset_repo,
        "get_asset_by_id",
        AsyncMock(return_value={"id": 42, "company_id": 3, "customer_visible": True}),
    )
    monkeypatch.setattr(
        assets_routes.audience_repo, "role_can_access", AsyncMock(return_value=False)
    )
    photo_get = AsyncMock()
    monkeypatch.setattr(assets_routes.asset_photo_repo, "get", photo_get)

    with pytest.raises(assets_routes.HTTPException) as excinfo:
        await assets_routes.get_asset_photo(
            _make_request(f"/assets/42/photos/9/{variant}"), 42, 9, variant
        )

    assert excinfo.value.status_code == 404
    photo_get.assert_not_awaited()


@pytest.mark.anyio
async def test_direct_photo_rejects_roleless_scoped_and_cross_company_assets(monkeypatch):
    membership = {
        "role_id": None,
        "menu.asset_photos": "read",
        "menu.assets": "read",
    }
    monkeypatch.setattr(
        assets_routes,
        "_load_asset_context",
        AsyncMock(return_value=({"id": 7}, membership, {"id": 3}, 3, None)),
    )
    monkeypatch.setattr(
        assets_routes,
        "_main",
        lambda: SimpleNamespace(_membership_menu_can=_photo_permissions),
    )
    asset_get = AsyncMock(
        side_effect=[
            {"id": 42, "company_id": 3, "customer_visible": True},
            {"id": 42, "company_id": 99, "customer_visible": True},
        ]
    )
    monkeypatch.setattr(assets_routes.asset_repo, "get_asset_by_id", asset_get)
    audiences = AsyncMock(return_value=[11])
    monkeypatch.setattr(assets_routes.audience_repo, "list_role_ids", audiences)

    with pytest.raises(assets_routes.HTTPException) as scoped:
        await assets_routes._photo_context(_make_request("/assets/42/photos/9/original"), 42)
    with pytest.raises(assets_routes.HTTPException) as cross_company:
        await assets_routes._photo_context(_make_request("/assets/42/photos/9/original"), 42)

    assert scoped.value.status_code == 404
    assert cross_company.value.status_code == 404
    audiences.assert_awaited_once_with(3, "asset", 42)


@pytest.mark.anyio
async def test_asset_technician_permissions_remain_independent_of_publication(monkeypatch):
    membership = {
        "role_id": None,
        "menu.asset_photos": "write",
        "menu.assets": "write",
    }
    monkeypatch.setattr(
        assets_routes,
        "_load_asset_context",
        AsyncMock(return_value=({"id": 7}, membership, {"id": 3}, 3, None)),
    )
    monkeypatch.setattr(
        assets_routes,
        "_main",
        lambda: SimpleNamespace(_membership_menu_can=_photo_permissions),
    )
    monkeypatch.setattr(
        assets_routes.asset_repo,
        "get_asset_by_id",
        AsyncMock(return_value={"id": 42, "company_id": 3, "customer_visible": False}),
    )
    audience_lookup = AsyncMock()
    monkeypatch.setattr(assets_routes.audience_repo, "list_role_ids", audience_lookup)

    _user, company_id, can_write = await assets_routes._photo_context(
        _make_request("/assets/42/photos"), 42, write=True
    )

    assert company_id == 3
    assert can_write
    audience_lookup.assert_not_awaited()


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
