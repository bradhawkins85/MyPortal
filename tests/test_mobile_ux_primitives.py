"""Static-asset checks for the mobile UX primitives.

These tests do not require the full FastAPI test environment — they simply
verify that the breakpoint utilities, table--stack-mobile pattern, and
supporting macro/JS files are present and wired into base.html. They guard
against accidental removal of the shared mobile foundation described in
``docs/MOBILE_UX_GUIDELINES.md``.
"""
from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
APP = REPO / "app"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_app_css_defines_mobile_utility_classes():
    css = _read(APP / "static" / "css" / "app.css")
    for utility in (
        ".u-hide-mobile",
        ".u-only-mobile",
        ".u-hide-tablet",
        ".u-priority-low",
        ".u-priority-secondary",
    ):
        assert utility in css, f"missing utility class {utility} in app.css"


def test_app_css_defines_table_stack_mobile_and_collapsible():
    css = _read(APP / "static" / "css" / "app.css")
    assert ".table--stack-mobile" in css
    assert ".mobile-collapsible" in css
    assert ".header__actions--overflow" in css
    # data-mobile-hidden should fire on landscape phones now (no orientation gate
    # on the primary rule).
    assert "[data-mobile-hidden='true']" in css


def test_mobile_macros_module_exists():
    macros = APP / "templates" / "macros" / "mobile.html"
    assert macros.is_file(), "macros/mobile.html should exist"
    body = _read(macros)
    assert "header_actions_overflow" in body
    assert "mobile_collapsible" in body


def test_viewport_js_is_wired_into_base_template():
    base = _read(APP / "templates" / "base.html")
    assert "/static/js/viewport.js" in base
    viewport_js = _read(APP / "static" / "js" / "viewport.js")
    assert "window.MyPortal" in viewport_js
    assert "viewport:change" in viewport_js


def test_priority_a_pages_use_mobile_primitives():
    """Priority A pages from the mobile plan should use the shared primitives."""
    tickets_index = _read(APP / "templates" / "tickets" / "index.html")
    assert "table--stack-mobile" in tickets_index

    invoices_index = _read(APP / "templates" / "invoices" / "index.html")
    assert "table--stack-mobile" in invoices_index

    dashboard = _read(APP / "templates" / "dashboard.html")
    assert "mobile_collapsible" in dashboard

    ticket_detail = _read(APP / "templates" / "tickets" / "detail.html")
    assert "mobile_collapsible" in ticket_detail

    notifications = _read(APP / "templates" / "notifications" / "index.html")
    assert "mobile_collapsible" in notifications


def test_tables_js_uses_standard_mobile_breakpoint():
    js = _read(APP / "static" / "js" / "tables.js")
    # The new query is "(max-width: 640px)" — and importantly does NOT depend
    # on portrait orientation, so supporting columns also collapse on landscape
    # phones.
    assert "(max-width: 640px)" in js
    assert "orientation: portrait" not in js
