"""Smoke tests for the ``quotes`` feature pack."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI

import app.main as main_module
from app.core.features import init_registry
from app.features.quotes import PACK
from app.features.quotes import routes as quotes_routes


EXPECTED = {
    ("GET", "/quotes"),
    ("POST", "/quotes/load/{quote_number}"),
    ("POST", "/cart/save-as-quote"),
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


def test_quotes_pack_manifest_declares_all_routes():
    declared = set()
    for router in PACK.routers:
        for route in router.routes:
            for method in route.methods or set():
                declared.add((method, route.path))

    assert PACK.slug == "quotes"
    assert PACK.version
    assert declared == EXPECTED


def test_quotes_pack_owns_quote_endpoints():
    assert quotes_routes.quotes_page.__module__ == "app.features.quotes.routes"
    assert quotes_routes.load_quote_to_cart.__module__ == "app.features.quotes.routes"
    assert quotes_routes.save_as_quote.__module__ == "app.features.quotes.routes"


def test_app_main_no_longer_owns_quote_routes():
    in_main_app = _routes_for(main_module.app)
    for method, path in EXPECTED:
        assert (method, path) not in in_main_app, (
            f"{method} {path} still mounted directly on app.main; "
            "feature-pack migration is incomplete."
        )


def test_quotes_pack_loads_and_reloads_cleanly():
    import asyncio

    async def _run() -> None:
        test_app = FastAPI()
        registry = init_registry(test_app)

        await registry.load("quotes")
        after_load = _routes_for(test_app)
        assert EXPECTED.issubset(after_load)

        await registry.reload("quotes")
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


def test_quotes_template_renders_for_non_admin_without_membership_alias():
    from types import SimpleNamespace

    from jinja2 import DictLoader, Environment, select_autoescape

    template_source = Path("app/templates/shop/quotes.html").read_text(encoding="utf-8")
    env = Environment(
        loader=DictLoader(
            {
                "shop/quotes.html": template_source,
                "base.html": (
                    "{% block header_title %}{% endblock %}"
                    "{% block header_actions %}{% endblock %}"
                    "{% block content %}{% endblock %}"
                    "{% block scripts %}{% endblock %}"
                ),
                "macros/header.html": (
                    "{% macro page_header_actions(actions) %}"
                    "{% for action in actions %}{{ action.label }}{% endfor %}"
                    "{% endmacro %}"
                ),
                "partials/csrf.html": (
                    '<input type="hidden" name="csrf_token" '
                    'value="{{ csrf_token or \'\' }}">'
                ),
            }
        ),
        autoescape=select_autoescape(["html"]),
    )
    env.globals["static_url"] = lambda path: path

    rendered = env.get_template("shop/quotes.html").render(
        request=SimpleNamespace(url=SimpleNamespace(path="/quotes")),
        current_user={"is_super_admin": False},
        is_company_admin=False,
        csrf_token="token",
        quotes_total_all=1,
        quotes_total=1,
        filters_active=False,
        status_summary=[],
        status_options=[],
        status_filter=None,
        quotes=[
            {
                "quote_number": "QUO-001",
                "company_id": 7,
                "name": "Non-admin quote",
                "po_number": None,
                "status_value": "active",
                "status_badge": "badge--success",
                "status_label": "Active",
                "assigned_user_email": None,
                "created_at_iso": None,
                "expires_at_iso": None,
                "is_expired": False,
                "notes": None,
                "assigned_user_id": None,
            }
        ],
    )

    assert "QUO-001" in rendered
    assert "Load to Cart" in rendered
    assert "Sync to Xero" not in rendered
    assert "data-quote-assign" not in rendered
