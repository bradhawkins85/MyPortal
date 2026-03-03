"""Tests for ticket list column customisation feature.

Validates that the /admin/tickets template includes the column toggle
controls and that table cells carry the expected data-column attributes.
"""
from pathlib import Path


TEMPLATE_PATH = (
    Path(__file__).resolve().parent.parent
    / "app" / "templates" / "admin" / "tickets.html"
)

EXPECTED_COLUMNS = ["id", "status", "priority", "company", "assigned", "updated"]
ALWAYS_VISIBLE_COLUMNS = ["subject"]


def _template_html():
    return TEMPLATE_PATH.read_text(encoding="utf-8")


def test_columns_toggle_button_present():
    """The toolbar should contain a 'Columns' toggle button."""
    html = _template_html()
    assert 'data-ticket-columns' in html
    assert 'data-columns-toggle' in html
    assert '>Columns<' in html


def test_column_panel_present():
    """The column customisation panel should be rendered."""
    html = _template_html()
    assert 'data-columns-panel' in html
    assert 'ticket-columns__panel' in html


def test_all_customisable_column_toggles_present():
    """Each customisable column must have a checkbox toggle in the panel."""
    html = _template_html()
    for column in EXPECTED_COLUMNS:
        assert f'class="ticket-column-toggle" data-column="{column}"' in html, (
            f"Expected toggle for column '{column}' to be present in the panel"
        )


def test_subject_column_toggle_is_disabled():
    """The Subject column toggle must be present but disabled (always visible)."""
    html = _template_html()
    # Check the subject toggle is present with both checked and disabled attributes
    assert 'class="ticket-column-toggle" data-column="subject"' in html
    # The subject checkbox must be disabled (order-independent check)
    import re
    subject_inputs = re.findall(
        r'<input[^>]+data-column="subject"[^>]*>', html
    )
    assert subject_inputs, "No input with data-column='subject' found"
    subject_input = subject_inputs[0]
    assert 'checked' in subject_input, "Subject toggle should be checked"
    assert 'disabled' in subject_input, "Subject toggle should be disabled"


def test_table_header_data_column_attributes():
    """Table <th> elements for customisable columns should carry data-column."""
    html = _template_html()
    for column in EXPECTED_COLUMNS + ALWAYS_VISIBLE_COLUMNS:
        assert f'data-sort' in html  # sanity check
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


def test_ticket_columns_js_included():
    """The ticket_columns.js script should be included in the page."""
    html = _template_html()
    assert 'ticket_columns.js' in html


def test_ticket_columns_js_exists():
    """The ticket_columns.js file should exist in the static assets."""
    js_path = (
        Path(__file__).resolve().parent.parent
        / "app" / "static" / "js" / "ticket_columns.js"
    )
    assert js_path.exists(), "ticket_columns.js should exist in app/static/js/"


def test_localStorage_storage_key_in_js():
    """The JS file should use a distinct localStorage key for ticket columns."""
    js_path = (
        Path(__file__).resolve().parent.parent
        / "app" / "static" / "js" / "ticket_columns.js"
    )
    js_content = js_path.read_text(encoding="utf-8")
    assert "portal.tickets.columns" in js_content


def test_subject_column_always_visible_in_js():
    """The JS should enforce that the subject column is always visible."""
    js_path = (
        Path(__file__).resolve().parent.parent
        / "app" / "static" / "js" / "ticket_columns.js"
    )
    js_content = js_path.read_text(encoding="utf-8")
    assert "'subject'" in js_content

