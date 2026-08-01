"""Regression coverage for the shared, ticket-style table column filters."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_shared_tables_build_typed_column_filter_menus() -> None:
    javascript = (ROOT / "app/static/js/tables.js").read_text(encoding="utf-8")

    assert "setupColumnFilters()" in javascript
    assert "data-table-column-filters-disabled" in javascript
    assert "table-column-filter__toggle" in javascript
    assert "data-table-column-filter-apply" in javascript
    assert "['greater', 'Greater than']" in javascript
    assert "['before', 'Before']" in javascript


def test_shared_table_filters_use_ticket_filter_visual_language() -> None:
    css = (ROOT / "app/static/css/app.css").read_text(encoding="utf-8")

    assert ".table-column-filter__panel" in css
    assert ".table-column-filter--active .table-column-filter__toggle" in css
    assert "var(--color-primary, #2563eb)" in css

