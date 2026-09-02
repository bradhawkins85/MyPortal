"""Tests for ticket list column customisation feature.

Validates that the /admin/tickets template includes the column toggle
controls and that table cells carry the expected data-column attributes.
"""
from pathlib import Path


TEMPLATE_PATH = (
    Path(__file__).resolve().parent.parent
    / "app" / "templates" / "admin" / "tickets.html"
)

EXPECTED_COLUMNS = ["id", "status", "priority", "company", "sla", "assigned", "updated", "last-reply-status"]
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
    result_limit_index = html.index('id="ticket-result-limit"')
    group_by_index = html.index('data-ticket-group-by')
    columns_index = html.index('data-ticket-columns')
    stats_index = html.index('data-ticket-stats')
    table_index = html.index('id="tickets-table"')

    assert quick_search_index < result_limit_index < group_by_index < stats_index < table_index
    assert quick_search_index < columns_index < stats_index
    assert '<option value="200" selected>200 tickets</option>' in html
    assert '<option value="500">500 tickets</option>' in html
    assert '<option value="all">All tickets</option>' in html

    css = (TEMPLATE_PATH.parent.parent.parent / "static" / "css" / "app.css").read_text(encoding="utf-8")
    assert "grid-template-columns: minmax(260px, 320px) minmax(260px, 340px) minmax(0, 1fr);" in css
    assert ".ticket-dashboard__overview .table-wrapper" in css
    assert "grid-column: 1 / -1;" in css


def test_ticket_stats_render_every_status_with_customisation_controls():
    """Every configured status is available for a technician to show or hide."""
    html = _template_html()

    assert 'from "macros/counters.html" import counter_strip' in html
    assert "{% for definition in ticket_status_definitions %}" in html
    assert 'data-ticket-stat-controls' in html
    assert 'data-ticket-stat-toggle="{{ definition.tech_status }}"' in html
    assert "'data-ticket-stat-selected': 'true' if status_count > 0 else 'false'" in html
    assert "'data-ticket-stat': definition.tech_status" in html
    assert "total_label='All tickets'" in html
    assert "class='ticket-dashboard__stats-strip'" in html


def test_ticket_stat_preferences_are_persisted_and_zero_counts_stay_hidden():
    """Saved status choices persist, but a selected status with no tickets stays hidden."""
    javascript = (
        TEMPLATE_PATH.parent.parent.parent / "static" / "js" / "ticket_stats.js"
    ).read_text(encoding="utf-8")

    assert "portal.tickets.stats" in javascript
    assert "storedValue === null" in javascript
    assert "saveSelectedStatuses(selectedStatuses)" in javascript
    assert "tile.hidden = !isSelected || !Number.isFinite(value) || value === 0" in javascript

    css = (
        TEMPLATE_PATH.parent.parent.parent / "static" / "css" / "app.css"
    ).read_text(encoding="utf-8")
    assert ".stat-strip__stat[hidden] {\n  display: none;\n}" in css


def test_ticket_stats_refresh_preserves_counter_labels():
    """Refreshing counts updates values without replacing each complete stat tile."""
    javascript = (
        TEMPLATE_PATH.parent.parent.parent / "static" / "js" / "admin.js"
    ).read_text(encoding="utf-8")

    update_stats_start = javascript.index("function updateStats(counts)")
    update_stats_end = javascript.index("function formatReviewDate", update_stats_start)
    update_stats = javascript[update_stats_start:update_stats_end]
    assert "element.querySelector('.stat-strip__stat-value')" in update_stats
    assert "statElements.total.querySelector('.stat-strip__stat-value')" in update_stats
    assert "statElements[key].dataset.ticketStatSelected === 'true'" in update_stats
    assert "element.textContent =" not in update_stats
    assert "statElements.total.textContent =" not in update_stats


def test_ticket_stats_refresh_hides_zero_count_statuses():
    """A refresh automatically hides selected status tiles when their count reaches zero."""
    javascript = (
        TEMPLATE_PATH.parent.parent.parent / "static" / "js" / "admin.js"
    ).read_text(encoding="utf-8")

    update_stats_start = javascript.index("function updateStats(counts)")
    update_stats_end = javascript.index("function formatReviewDate", update_stats_start)
    update_stats = javascript[update_stats_start:update_stats_end]
    assert (
        "tile.dataset.ticketStatSelected !== 'true' || !Number.isFinite(value) || value === 0"
        in update_stats
    )


def test_ticket_stats_refresh_uses_global_status_total():
    """The KPI total is derived from global counts, not the filtered row total."""
    javascript = (
        TEMPLATE_PATH.parent.parent.parent / "static" / "js" / "admin.js"
    ).read_text(encoding="utf-8")

    assert "state.updateStats(response?.status_counts);" in javascript
    assert "state.updateStats(response?.status_counts, response?.total);" not in javascript


def test_next_ticket_number_handler_is_always_rendered():
    """The menu item handler must not depend on header-block scoped Jinja variables."""
    html = _template_html()
    script_index = html.index("data-next-ticket-number-open")
    scripts_block_index = html.index("{% block scripts %}")
    handler_index = html.index("form.requestSubmit();")

    assert script_index < scripts_block_index < handler_index
    assert "{% if show_next_ticket_number %}\n    <script>" not in html


def test_bulk_action_modals_render_outside_the_filtered_page_header():
    """Fixed ticket modals retain their permissions outside the header block."""
    html = _template_html()
    header_block_index = html.index("{% block header_title %}")
    content_block_index = html.index("{% block content %}")

    assert html.index("{% set can_bulk_edit_tickets =") < header_block_index
    assert html.index("{% set can_merge_tickets =") < header_block_index
    assert content_block_index < html.index('id="bulk-edit-tickets-modal"')
    assert content_block_index < html.index('id="merge-tickets-modal"')


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


def test_group_expansion_state_is_persisted():
    """Collapsed ticket groups should be restored after the page reloads."""
    javascript = (
        TEMPLATE_PATH.parent.parent.parent / "static" / "js" / "ticket_views.js"
    ).read_text(encoding="utf-8")

    assert "portal.tickets.collapsedGroups" in javascript
    assert "this.collapsedGroups = this.loadCollapsedGroups()" in javascript
    assert "this.saveCollapsedGroups()" in javascript
    assert "this.updateGroupVisibility()" in javascript


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


def test_ticket_table_does_not_render_actions_column():
    """Ticket subjects link to details, so the table needs no actions column."""
    html = _template_html()
    table = html[html.index('id="tickets-table"'):]
    table = table[:table.index("</table>")]

    assert 'tickets-table__column--actions' not in table
    assert 'tickets-table__cell--actions' not in table
    assert '>Actions</th>' not in table


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


def test_column_filter_panels_match_the_status_filter_background():
    """Every floating column filter should use the status menu surface colour."""
    css = (TEMPLATE_PATH.parent.parent.parent / "static" / "css" / "app.css").read_text(encoding="utf-8")
    panel_rule = css[css.index(".ticket-column-filter__panel {"):]
    panel_rule = panel_rule[:panel_rule.index("}")]
    assert "background: var(--color-surface-2, #0f172a);" in panel_rule


def test_table_cell_data_column_attributes():
    """Table <td> elements for customisable columns should carry data-column."""
    html = _template_html()
    for column in EXPECTED_COLUMNS + ALWAYS_VISIBLE_COLUMNS:
        assert f'data-column="{column}"' in html, (
            f"Expected data-column='{column}' attribute on a table element"
        )


def test_ticket_refresh_rows_include_last_reply_status_column():
    """Refreshed ticket rows must include Last Reply Status to stay aligned with headers."""
    javascript = (
        TEMPLATE_PATH.parent.parent.parent / "static" / "js" / "admin.js"
    ).read_text(encoding="utf-8")

    build_row_start = javascript.index("function buildRow(ticket)")
    build_row_end = javascript.index("function patchRows(items)", build_row_start)
    build_row = javascript[build_row_start:build_row_end]

    updated_cell_index = build_row.index("appendTextCell('updated', 'Updated'")
    last_reply_cell_index = build_row.index(
        "row.appendChild(createLastReplyStatusCell(ticket.latest_public_reply_email_status))"
    )
    review_date_cell_index = build_row.index("appendTextCell('review-date', 'Review Date'")

    assert updated_cell_index < last_reply_cell_index < review_date_cell_index
    assert "cell.dataset.column = 'last-reply-status'" in javascript
    assert "No email status" in javascript


def test_last_reply_status_empty_state_is_visible():
    """The Last Reply Status column should not appear blank when no email tracking exists."""
    html = _template_html()
    last_reply_cell = html[html.index('data-column="last-reply-status"', html.index('<tbody>')):]
    last_reply_cell = last_reply_cell[:last_reply_cell.index("</td>")]

    assert '<span class="badge badge--muted">No email status</span>' in last_reply_cell


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


def test_every_ticket_data_column_gets_a_typed_filter():
    """The view manager builds filters from every table data-column header."""
    js = (TEMPLATE_PATH.parent.parent.parent / "static" / "js" / "ticket_views.js").read_text(encoding="utf-8")

    assert "thead th[data-column]" in js
    assert "setupColumnFilters()" in js
    assert "dateColumns" in js
    assert "numberColumns" in js
    assert "booleanColumns" in js
    assert "Does not contain" in js
    assert "In the last 30 days" in js
    assert "data-column-filter-clear" in js


def test_date_column_filters_can_use_dynamic_today_reference():
    """Date rules can remain relative to today when they are saved and reused."""
    js = (TEMPLATE_PATH.parent.parent.parent / "static" / "js" / "ticket_views.js").read_text(encoding="utf-8")

    assert 'data-column-filter-date-reference' in js
    assert '<option value="today">Today</option>' in js
    assert "filter.value === 'today' ? new Date()" in js


def test_column_filters_are_saved_and_active_headers_are_highlighted():
    """Saved views persist column rules and CSS identifies active headings."""
    root = TEMPLATE_PATH.parent.parent.parent
    js = (root / "static" / "js" / "ticket_views.js").read_text(encoding="utf-8")
    css = (root / "static" / "css" / "app.css").read_text(encoding="utf-8")

    assert "column_filters: this.filterState.columnFilters" in js
    assert "view.filters.column_filters || {}" in js
    assert "visible_columns: window.ticketColumns" in js
    assert "window.ticketColumns.applyVisibleColumns(view.filters.visible_columns)" in js
    assert "ticket-column-filter--active" in css
    assert "ticket-status-filter--active" in css


def test_localStorage_storage_key_in_js():
    """The JS file should use a distinct localStorage key for ticket columns."""
    js_path = (
        Path(__file__).resolve().parent.parent
        / "app" / "static" / "js" / "ticket_columns.js"
    )
    js_content = js_path.read_text(encoding="utf-8")
    assert "portal.tickets.columns" in js_content


def test_saved_views_can_read_and_apply_ticket_column_layouts():
    """Column controls expose their current selection to the saved-view manager."""
    js_path = (
        Path(__file__).resolve().parent.parent
        / "app" / "static" / "js" / "ticket_columns.js"
    )
    js_content = js_path.read_text(encoding="utf-8")

    assert "window.ticketColumns" in js_content
    assert "getVisibleColumns()" in js_content
    assert "applyVisibleColumns(columns)" in js_content


def test_subject_column_always_visible_in_js():
    """The JS should enforce that the subject column is always visible."""
    js_path = (
        Path(__file__).resolve().parent.parent
        / "app" / "static" / "js" / "ticket_columns.js"
    )
    js_content = js_path.read_text(encoding="utf-8")
    assert "'subject'" in js_content
