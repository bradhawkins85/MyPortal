"""Regression coverage for the shop admin table filters."""
from pathlib import Path


TEMPLATE = Path("app/templates/admin/shop.html").read_text()
TABLES_SCRIPT = Path("app/static/js/tables.js").read_text()
SHOP_SCRIPT = Path("app/static/js/shop_admin.js").read_text()


def test_redundant_catalogue_filter_dropdowns_are_removed() -> None:
    assert 'id="stock-filter"' not in TEMPLATE
    assert 'id="category-filter"' not in TEMPLATE
    assert "function applyFilters()" not in SHOP_SCRIPT


def test_category_column_uses_searchable_multi_select_filter() -> None:
    assert 'data-column="category" data-filter-options="multiple"' in TEMPLATE
    assert "type === 'multi'" in TABLES_SCRIPT
    assert "table-column-filter__options" in TABLES_SCRIPT
    assert "Search ${label}" in TABLES_SCRIPT
    assert "input:checked" in TABLES_SCRIPT


def test_multi_select_filter_state_is_restored_and_reapplied() -> None:
    assert "Array.isArray(value.values) && value.values.length" in TABLES_SCRIPT
    assert "selected.has(checkbox.value)" in TABLES_SCRIPT
    assert "selectedValues.includes(cellValue)" in TABLES_SCRIPT
    assert "this.updateFilterState();" in TABLES_SCRIPT
    assert "this.render();" in TABLES_SCRIPT
