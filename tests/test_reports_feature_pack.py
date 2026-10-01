"""Smoke tests for the ``reports`` feature pack."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI

import app.main as main_module
from app.core.features import init_registry
from app.features.reports import PACK
from app.features.reports import handlers as report_handlers
from app.features.reports import routes as report_routes


EXPECTED = {
    ("GET", "/reports/company-overview"),
    ("GET", "/reports/company-overview.pdf"),
    ("GET", "/reports/company-overview/settings"),
    ("POST", "/reports/company-overview/settings"),
    ("GET", "/reports/company-overview/settings/export"),
    ("POST", "/reports/company-overview/settings/import"),
    ("GET", "/admin/reports/pdf-cover-image"),
    ("POST", "/admin/reports/pdf-cover-image"),
    ("POST", "/admin/reports/pdf-cover-image/delete"),
    ("GET", "/admin/reports/pdf-cover-image/preview"),
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


def test_reports_pack_manifest_declares_all_routes():
    """Manifest should expose exactly the routes that were migrated."""

    declared = set()
    for router in PACK.routers:
        for route in router.routes:
            for method in route.methods or set():
                declared.add((method, route.path))

    assert PACK.slug == "reports"
    assert PACK.version
    assert declared == EXPECTED


def test_app_main_no_longer_owns_report_routes():
    """The routes must have been removed from ``app/main.py`` so that
    the pack is the sole owner — otherwise reloading the pack would
    leave stale handlers behind."""

    in_main_app = _routes_for(main_module.app)
    for method, path in EXPECTED:
        assert (method, path) not in in_main_app, (
            f"{method} {path} still mounted directly on app.main; "
            "feature-pack migration is incomplete."
        )


def test_reports_pack_owns_handlers():
    assert report_routes.router.routes[0].endpoint == report_handlers.company_overview_report_page
    assert report_routes.router.routes[1].endpoint == report_handlers.company_overview_report_pdf
    assert report_routes.router.routes[2].endpoint == report_handlers.company_overview_report_settings_page
    assert report_routes.router.routes[3].endpoint == report_handlers.company_overview_report_settings_save
    assert report_routes.router.routes[4].endpoint == report_handlers.company_overview_report_settings_export
    assert report_routes.router.routes[5].endpoint == report_handlers.company_overview_report_settings_import
    assert report_routes.router.routes[6].endpoint == report_handlers.admin_report_cover_image_page
    assert report_routes.router.routes[7].endpoint == report_handlers.admin_report_cover_image_upload
    assert report_routes.router.routes[8].endpoint == report_handlers.admin_report_cover_image_delete
    assert report_routes.router.routes[9].endpoint == report_handlers.admin_report_cover_image_preview


def test_reports_pack_loads_and_reloads_cleanly():
    """The pack should load via the registry, mount its routes, and
    survive a hot reload without leaking duplicate routes."""

    import asyncio

    async def _run() -> None:
        test_app = FastAPI()
        registry = init_registry(test_app)

        await registry.load("reports")
        after_load = _routes_for(test_app)
        assert EXPECTED.issubset(after_load)

        await registry.reload("reports")
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


def test_report_designer_rows_are_collapsible_and_draggable():
    """The row boundary and reorder controls should remain part of the UI."""

    template = Path("app/templates/reports/settings.html").read_text()

    assert '<details class="designer-row">' in template
    assert 'class="designer-row__summary"' in template
    assert 'class="drag" draggable="true"' in template
    assert "rows.addEventListener('dragstart'" in template
    assert "rows.addEventListener('dragover'" in template
    assert "rows.addEventListener('drop'" in template


def test_report_designer_exposes_import_export_controls():
    """Admins should be able to move a designed report between companies."""

    template = Path("app/templates/reports/settings.html").read_text()

    assert 'href="/reports/company-overview/settings/export"' in template
    assert 'action="/reports/company-overview/settings/import"' in template
    assert 'name="layout_import_file"' in template
    assert 'name="layout_import_json"' in template


def test_company_overview_spacing_has_fallback_values():
    """Report spacing must work even when the global theme omits space tokens."""

    template = Path("app/templates/reports/index.html").read_text()

    assert ".report-page{display:flex;flex-direction:column;gap:var(--space-4,1rem);padding:var(--space-3,.75rem)}" in template
    assert "padding:var(--space-4,1rem)" in template
    assert "gap:var(--space-3,.75rem)" in template


def test_company_overview_pdf_tables_have_explicit_print_colours():
    """Table text must not inherit white foregrounds from coloured report cells."""

    template = Path("app/templates/reports/pdf.html").read_text()

    assert "table{width:100%;border-collapse:collapse;font-size:7pt;table-layout:fixed;background:#fff;color:#1f2937}" in template
    assert "th,td{border:1px solid #d1d5db;padding:3pt;overflow-wrap:anywhere;background:#fff;color:#1f2937}" in template
    assert "th{background:#f3f4f6;color:#111827;font-weight:bold}" in template
    assert ".card__header,.report-section__header{color:#111827}" in template
