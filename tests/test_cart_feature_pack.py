"""Smoke tests for the ``cart`` feature pack."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI

import app.main as main_module
from app.core.features import init_registry
from app.features.cart import PACK


EXPECTED = {
    ("POST", "/cart/add"),
    ("POST", "/cart/add-package"),
    ("GET", "/cart"),
    ("POST", "/cart/update"),
    ("POST", "/cart/remove"),
    ("POST", "/cart/place-order"),
}

REPO_ROOT = Path(__file__).resolve().parents[1]


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


def test_cart_pack_manifest_declares_all_routes():
    declared = set()
    for router in PACK.routers:
        for route in router.routes:
            for method in route.methods or set():
                declared.add((method, route.path))

    assert PACK.slug == "cart"
    assert PACK.version
    assert declared == EXPECTED


def test_app_main_no_longer_owns_cart_routes():
    in_main_app = _routes_for(main_module.app)
    for method, path in EXPECTED:
        assert (method, path) not in in_main_app, (
            f"{method} {path} still mounted directly on app.main; "
            "feature-pack migration is incomplete."
        )


def test_app_main_no_longer_defines_cart_handlers():
    for name in (
        "add_to_cart",
        "add_package_to_cart",
        "view_cart",
        "update_cart_items",
        "remove_cart_items",
        "place_order",
    ):
        assert not hasattr(main_module, name), (
            f"app.main still defines {name}; "
            "feature-pack migration is incomplete."
        )


def test_cart_pack_runtime_dependencies_remain_available_from_main():
    """Protect dependencies used by the cart pack's legacy main-module seam."""
    for name in (
        "shop_repo",
        "subscriptions_repo",
        "subscription_shop_integration",
    ):
        assert hasattr(main_module, name), (
            f"app.main must expose {name} while cart routes resolve dependencies "
            "through the legacy main-module seam."
        )


def test_cart_pack_loads_and_reloads_cleanly():
    import asyncio

    async def _run() -> None:
        test_app = FastAPI()
        registry = init_registry(test_app)

        await registry.load("cart")
        after_load = _routes_for(test_app)
        assert EXPECTED.issubset(after_load)

        await registry.reload("cart")
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


def test_cart_quantity_controls_auto_save_without_remove_column():
    template = (REPO_ROOT / "app/templates/shop/cart.html").read_text()
    script = (REPO_ROOT / "app/static/js/cart.js").read_text()

    assert '>Remove</th>' not in template
    assert 'name="remove"' not in template
    assert "data-cart-quantity" in template
    assert "input.addEventListener('input'" in script
    assert "Number(input.value) === 0" in script
