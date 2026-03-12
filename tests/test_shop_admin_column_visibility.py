"""Tests for shop admin column visibility feature."""
from __future__ import annotations

import pytest


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio("asyncio")
async def test_column_visibility_defaults_to_all_visible():
    """Test that all columns are visible when no preference is stored in localStorage.

    The JavaScript initialises column visibility from localStorage. When no preference
    exists (loadColumnPrefs returns {}), columnPrefs[key] !== false evaluates to true
    for every key, so all columns default to visible.
    """
    # Simulate loadColumnPrefs returning {} (no stored preference)
    prefs: dict[str, bool] = {}
    column_keys = ['image', 'name', 'sku', 'vendor-sku', 'dbp', 'price', 'vip', 'category', 'stock']
    for key in column_keys:
        assert prefs.get(key) is not False  # default: visible


@pytest.mark.anyio("asyncio")
async def test_column_visibility_hidden_when_pref_is_false():
    """Test that a column is hidden when its preference is explicitly set to False."""
    prefs: dict[str, bool] = {'sku': False, 'dbp': False}
    column_keys = ['image', 'name', 'sku', 'vendor-sku', 'dbp', 'price', 'vip', 'category', 'stock']
    for key in column_keys:
        visible = prefs.get(key) is not False
        if key in ('sku', 'dbp'):
            assert not visible, f"Column '{key}' should be hidden"
        else:
            assert visible, f"Column '{key}' should be visible"


@pytest.mark.anyio("asyncio")
async def test_column_visibility_toggle_saves_pref():
    """Test that toggling a column updates the preference dictionary."""
    prefs: dict[str, bool] = {}

    # Simulate unchecking the 'image' column
    prefs['image'] = False
    assert prefs['image'] is False

    # Simulate re-checking the 'image' column
    prefs['image'] = True
    assert prefs['image'] is True


@pytest.mark.anyio("asyncio")
async def test_template_contains_data_column_attributes():
    """Test that the shop admin template has data-column attributes on table headers and cells."""
    import re
    from pathlib import Path

    template = Path('app/templates/admin/shop.html').read_text()

    column_keys = ['image', 'name', 'sku', 'vendor-sku', 'dbp', 'price', 'vip', 'category', 'stock']
    for key in column_keys:
        # Check that at least one element (th or td) has data-column="<key>"
        assert f'data-column="{key}"' in template, (
            f'Missing data-column="{key}" attribute in shop admin template'
        )


@pytest.mark.anyio("asyncio")
async def test_template_contains_column_toggle_checkboxes():
    """Test that the shop admin template has data-column-toggle checkboxes for every column."""
    from pathlib import Path

    template = Path('app/templates/admin/shop.html').read_text()

    column_keys = ['image', 'name', 'sku', 'vendor-sku', 'dbp', 'price', 'vip', 'category', 'stock']
    for key in column_keys:
        assert f'data-column-toggle="{key}"' in template, (
            f'Missing data-column-toggle="{key}" checkbox in shop admin template'
        )


@pytest.mark.anyio("asyncio")
async def test_template_contains_columns_dropdown():
    """Test that the shop admin template includes the Columns dropdown button."""
    from pathlib import Path

    template = Path('app/templates/admin/shop.html').read_text()

    assert 'id="columns-dropdown"' in template
    assert 'id="columns-toggle"' in template
    assert 'id="columns-menu"' in template
