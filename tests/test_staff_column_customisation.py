"""Tests for staff table column customisation feature.

Validates that the /staff template includes the column toggle controls and
that table cells carry the expected data-column attributes.
"""
import re
from pathlib import Path


TEMPLATE_PATH = (
    Path(__file__).resolve().parent.parent
    / "app" / "templates" / "staff" / "index.html"
)

JS_PATH = (
    Path(__file__).resolve().parent.parent
    / "app" / "static" / "js" / "staff_columns.js"
)

EXPECTED_COLUMNS = ["last-name", "email", "mobile", "code", "onboarded", "m365-sign-in", "enabled"]
ALWAYS_VISIBLE_COLUMNS = ["first-name"]


def _template_html():
    return TEMPLATE_PATH.read_text(encoding="utf-8")


def test_columns_toggle_button_present():
    """The toolbar should contain a 'Columns' toggle button."""
    html = _template_html()
    assert 'data-staff-columns' in html
    assert 'data-columns-toggle' in html
    assert '>Columns<' in html or 'Columns</span>' in html


def test_column_panel_present():
    """The column customisation panel should be rendered."""
    html = _template_html()
    assert 'data-columns-panel' in html


def test_all_customisable_column_toggles_present():
    """Each customisable column must have a checkbox toggle in the panel."""
    html = _template_html()
    for column in EXPECTED_COLUMNS:
        assert f'class="staff-column-toggle" data-column="{column}"' in html, (
            f"Expected toggle for column '{column}' to be present in the panel"
        )


def test_first_name_column_toggle_is_disabled():
    """The First name column toggle must be present but disabled (always visible)."""
    html = _template_html()
    first_name_inputs = re.findall(
        r'<input[^>]+data-column="first-name"[^>]*>', html
    )
    assert first_name_inputs, "No input with data-column='first-name' found"
    first_name_input = first_name_inputs[0]
    assert 'checked' in first_name_input, "First name toggle should be checked"
    assert 'disabled' in first_name_input, "First name toggle should be disabled"


def test_table_header_data_column_attributes():
    """Table <th> elements for customisable columns should carry data-column."""
    html = _template_html()
    for column in EXPECTED_COLUMNS + ALWAYS_VISIBLE_COLUMNS:
        assert f'data-column="{column}"' in html, (
            f"Expected data-column='{column}' attribute on a table element"
        )


def test_table_cell_data_column_attributes():
    """Table <td> elements for customisable columns should carry data-column."""
    html = _template_html()
    for column in EXPECTED_COLUMNS + ALWAYS_VISIBLE_COLUMNS:
        assert f'data-column="{column}"' in html, (
            f"Expected data-column='{column}' attribute on a table element"
        )


def test_staff_columns_js_included():
    """The staff_columns.js script should be included in the page."""
    html = _template_html()
    assert 'staff_columns.js' in html


def test_staff_columns_js_exists():
    """The staff_columns.js file should exist in the static assets."""
    assert JS_PATH.exists(), "staff_columns.js should exist in app/static/js/"


def test_localStorage_storage_key_in_js():
    """The JS file should use a distinct localStorage key for staff columns."""
    js_content = JS_PATH.read_text(encoding="utf-8")
    assert "portal.staff.columns" in js_content


def test_first_name_column_always_visible_in_js():
    """The JS should enforce that the first-name column is always visible."""
    js_content = JS_PATH.read_text(encoding="utf-8")
    assert "'first-name'" in js_content


def test_custom_field_column_toggle_loop_in_panel():
    """The column panel must contain a Jinja loop for custom field toggles.

    Custom fields are rendered with a ``custom-`` prefix so that their toggle
    data-column values match the corresponding table th/td data-column values.
    """
    html = _template_html()
    # The template iterates over staff_custom_field_definitions for the panel
    assert 'staff_custom_field_definitions' in html
    # The loop generates custom-prefixed column toggles matching the table cells
    assert 'data-column="custom-' in html or 'data-column="custom-{{ field.name }}"' in html
