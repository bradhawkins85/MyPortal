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


def test_dashboard_controls_precede_stats_and_full_width_table():
    """The dashboard uses one control row followed by the full-width ticket list."""
    html = _template_html()
    quick_search_index = html.index('id="ticket-quick-filter"')
    group_by_index = html.index('data-ticket-group-by')
    columns_index = html.index('data-ticket-columns')
    stats_index = html.index('data-ticket-stats')
    table_index = html.index('id="tickets-table"')

    assert quick_search_index < group_by_index < stats_index < table_index
    assert quick_search_index < columns_index < stats_index

    css = (TEMPLATE_PATH.parent.parent.parent / "static" / "css" / "app.css").read_text(encoding="utf-8")
    assert "grid-template-columns: minmax(260px, 320px) minmax(260px, 340px) minmax(0, 1fr);" in css
    assert ".ticket-dashboard__overview .table-wrapper" in css
    assert "grid-column: 1 / -1;" in css


def test_next_ticket_number_handler_is_always_rendered():
    """The menu item handler must not depend on header-block scoped Jinja variables."""
    html = _template_html()
    script_index = html.index("data-next-ticket-number-open")
    scripts_block_index = html.index("{% block scripts %}")
    handler_index = html.index("form.requestSubmit();")

    assert script_index < scripts_block_index < handler_index
    assert "{% if show_next_ticket_number %}\n    <script>" not in html


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


def test_status_filter_is_in_status_column_header_with_funnel_icon():
    """Status choices should open from an accessible funnel in the Status heading."""
    html = _template_html()
    status_header = html[html.index('data-column="status"', html.index('<thead>')):]
    status_header = status_header[:status_header.index('</th>')]

    assert 'data-ticket-status-filter-toggle' in status_header
    assert 'aria-label="Filter tickets by status"' in status_header
    assert '<svg viewBox="0 0 24 24"' in status_header
    assert 'data-ticket-status-filter-panel' in status_header
    assert 'data-status-filter' in status_header
    assert '<h3 class="ticket-filters__section-title">Filter by Status</h3>' not in html


def test_obsolete_filter_controls_are_not_rendered():
    """The ticket sidebar stays visible and no longer offers phone search."""
    html = _template_html()
    assert 'data-ticket-filters-toggle' not in html
    assert 'Hide filters' not in html
    assert 'Search by Phone Number' not in html
    assert 'name="phoneNumber"' not in html


def test_status_filter_panel_has_an_opaque_background():
    """The floating status menu must remain readable over ticket rows."""
    css = (TEMPLATE_PATH.parent.parent.parent / "static" / "css" / "app.css").read_text(encoding="utf-8")
    panel_rule = css[css.index(".ticket-status-filter__panel {"):]
    panel_rule = panel_rule[:panel_rule.index("}")]
    assert "background: var(--color-surface-2, #0f172a);" in panel_rule


def test_table_cell_data_column_attributes():
    """Table <td> elements for customisable columns should carry data-column."""
    html = _template_html()
    for column in EXPECTED_COLUMNS + ALWAYS_VISIBLE_COLUMNS:
        assert f'data-column="{column}"' in html, (
            f"Expected data-column='{column}' attribute on a table element"
        )



def test_ticket_update_actor_type_column_is_available():
    """The automation variable ticket_update.actor_type should be available as a ticket column."""
    html = _template_html()
    assert "('ticket-update-actor-type', 'ticket_update.actor_type')" in html
    assert 'data-column="ticket-update-actor-type"' in html
    assert 'data-label="ticket_update.actor_type"' in html

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
