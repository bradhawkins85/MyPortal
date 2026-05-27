"""Smoke tests for the ``shop`` feature pack."""

from __future__ import annotations

from fastapi import FastAPI

import app.main as main_module
from app.core.features import init_registry
from app.features.shop import PACK
from app.features.shop import handlers as shop_handlers
from app.features.shop import routes as shop_routes


EXPECTED = {
    ("GET", "/shop"),
    ("GET", "/shop/packages"),
    ("GET", "/api/shop/products/{product_id}"),
    ("GET", "/api/admin/shop/products/search"),
    ("GET", "/api/admin/shop/products/{product_id}/restrictions"),
    ("GET", "/api/admin/shop/products/{product_id}"),
    ("GET", "/api/admin/shop/products/{product_id}/price-history"),
    ("GET", "/admin/shop"),
    ("GET", "/admin/shop/packages"),
    ("GET", "/admin/shop/packages/{package_id}"),
    ("GET", "/admin/shop/optional-accessories"),
    ("GET", "/admin/shop/categories"),
    ("GET", "/admin/shop/products/new"),
    ("GET", "/admin/shop/subscription-categories"),
    ("POST", "/shop/admin/package"),
    ("POST", "/shop/admin/package/{package_id}/update"),
    ("POST", "/shop/admin/package/{package_id}/archive"),
    ("POST", "/shop/admin/package/{package_id}/delete"),
    ("POST", "/shop/admin/package/{package_id}/items/add"),
    ("POST", "/shop/admin/package/{package_id}/items/{product_id}/update"),
    ("POST", "/shop/admin/package/{package_id}/items/{product_id}/alternates/add"),
    (
        "POST",
        "/shop/admin/package/{package_id}/items/{product_id}/alternates/{alternate_product_id}/remove",
    ),
    ("POST", "/shop/admin/package/{package_id}/items/{product_id}/remove"),
    ("POST", "/admin/shop/optional-accessories/sync"),
    ("POST", "/admin/shop/optional-accessories/{accessory_id}/import"),
    ("POST", "/admin/shop/optional-accessories/{accessory_id}/dismiss"),
    ("POST", "/admin/shop/optional-accessories/bulk-dismiss"),
    ("POST", "/admin/shop/optional-accessories/{accessory_id}/restore"),
    ("POST", "/shop/admin/category"),
    ("POST", "/shop/admin/category/{category_id}/delete"),
    ("POST", "/shop/admin/category/{category_id}/update"),
    ("POST", "/shop/admin/subscription-category"),
    ("POST", "/shop/admin/subscription-category/{category_id}/delete"),
    ("POST", "/shop/admin/subscription-category/{category_id}/update"),
    ("POST", "/shop/admin/product/import"),
    ("POST", "/shop/admin/product"),
    ("POST", "/shop/admin/product/{product_id}"),
    ("POST", "/shop/admin/product/{product_id}/archive"),
    ("POST", "/shop/admin/product/{product_id}/unarchive"),
    ("POST", "/shop/admin/product/{product_id}/visibility"),
    ("POST", "/shop/admin/product/{product_id}/delete"),
    ("GET", "/api/admin/companies/{company_id}/shop-items"),
    ("POST", "/admin/companies/{company_id}/shop-visibility"),
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


def test_shop_pack_manifest_declares_all_routes():
    declared = set()
    for router in PACK.routers:
        for route in router.routes:
            for method in route.methods or set():
                declared.add((method, route.path))

    assert PACK.slug == "shop"
    assert PACK.version
    assert declared == EXPECTED


def test_app_main_no_longer_owns_shop_routes():
    in_main_app = _routes_for(main_module.app)
    for method, path in EXPECTED:
        assert (method, path) not in in_main_app, (
            f"{method} {path} still mounted directly on app.main; "
            "feature-pack migration is incomplete."
        )


def test_shop_pack_owns_handlers():
    assert shop_routes.router.routes[0].endpoint == shop_handlers.shop_page
    assert shop_routes.router.routes[1].endpoint == shop_handlers.shop_packages_page
    assert shop_routes.router.routes[2].endpoint == shop_handlers.shop_product_detail_api
    assert shop_routes.router.routes[3].endpoint == shop_handlers.admin_shop_product_search_api
    assert shop_routes.router.routes[4].endpoint == shop_handlers.admin_shop_product_restrictions_api


def test_shop_pack_loads_and_reloads_cleanly():
    import asyncio

    async def _run() -> None:
        test_app = FastAPI()
        registry = init_registry(test_app)

        await registry.load("shop")
        after_load = _routes_for(test_app)
        assert EXPECTED.issubset(after_load)

        await registry.reload("shop")
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
