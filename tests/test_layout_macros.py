"""Smoke tests for the standardised page-layout macros.

These render the macros in isolation with a minimal Jinja environment that
mirrors the loader used by the application. They guard the public contract of
``page_header_actions``, ``counter_strip``, ``data_table``, ``table_toolbar``,
and ``table_column_picker`` so future tweaks don't accidentally break the
data-* hooks the front-end JS relies on.
"""
from __future__ import annotations

from pathlib import Path

import pytest
from jinja2 import Environment, FileSystemLoader, select_autoescape

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "app" / "templates"


@pytest.fixture()
def env():
    environment = Environment(
        loader=FileSystemLoader(str(TEMPLATES_DIR)),
        autoescape=select_autoescape(["html", "xml"]),
    )
    # Provide csrf_token global so render_action's form branch produces the hidden input.
    environment.globals["csrf_token"] = "test-csrf"
    return environment


def _render(env, source: str, **context) -> str:
    template = env.from_string(source)
    return template.render(**context)


# --- header.html ----------------------------------------------------------------


def test_page_header_actions_promotes_primary_and_collapses_rest(env):
    actions = [
        {"label": "New ticket", "type": "button", "variant": "primary",
         "attrs": {"data-create-ticket-modal-open": ""}},
        {"label": "Configure notifications", "type": "link", "href": "/notifications/settings"},
        {"label": "Mark selected as read", "type": "button",
         "attrs": {"data-notification-mark-selected": "", "disabled": ""}},
    ]
    html = _render(
        env,
        "{% from 'macros/header.html' import page_header_actions %}"
        "{{ page_header_actions(actions, menu_id='hdr-menu') }}",
        actions=actions,
    )
    # Primary button rendered with primary variant.
    assert 'class="button button--primary"' in html
    assert "New ticket" in html
    assert "data-create-ticket-modal-open" in html
    # Secondary actions collapsed into the menu.
    assert 'data-header-menu' in html
    assert 'aria-haspopup="menu"' in html
    assert 'aria-controls="hdr-menu"' in html
    assert 'href="/notifications/settings"' in html
    # Disabled bulk action preserved as menu item with attribute.
    assert "Mark selected as read" in html
    assert " disabled" in html


def test_page_header_actions_form_includes_csrf(env):
    html = _render(
        env,
        "{% from 'macros/header.html' import page_header_actions %}"
        "{{ page_header_actions([\n"
        "  {'label': 'Primary', 'type': 'button', 'variant': 'primary'},\n"
        "  {'label': 'Delete', 'type': 'form', 'action': '/things/1', 'variant': 'danger'},\n"
        "]) }}",
    )
    assert '<form method="post" action="/things/1"' in html
    assert 'name="_csrf" value="test-csrf"' in html
    assert "header-menu__item--danger" in html


def test_page_header_actions_single_action_renders_inline(env):
    html = _render(
        env,
        "{% from 'macros/header.html' import page_header_actions %}"
        "{{ page_header_actions([{'label': 'Manage services', 'type': 'link', 'href': '/admin/x'}]) }}",
    )
    assert 'data-header-menu' not in html
    assert 'href="/admin/x"' in html


# --- counters.html --------------------------------------------------------------


def test_counter_strip_renders_total_and_items_with_variants(env):
    items = [
        {"label": "Operational", "value": 12, "variant": "operational"},
        {"label": "Outage", "value": 1, "variant": "outage"},
    ]
    html = _render(
        env,
        "{% from 'macros/counters.html' import counter_strip %}"
        "{{ counter_strip(items, total=13, total_label='Tracked services') }}",
        items=items,
    )
    assert 'class="stat-strip"' in html
    assert "stat-strip__stat--total" in html
    assert "Tracked services" in html
    assert "stat-strip__stat--operational" in html
    assert "stat-strip__stat--outage" in html
    assert ">12<" in html and ">13<" in html


# --- tables.html ---------------------------------------------------------------


def test_data_table_emits_table_id_and_column_keys(env):
    columns = [
        {"key": "title", "label": "Title", "sort_type": "string", "mobile_priority": "essential"},
        {"key": "updated", "label": "Updated", "sort_type": "date", "default_visible": False},
    ]
    html = _render(
        env,
        "{% from 'macros/tables.html' import data_table %}"
        "{% call data_table([], table_id='kb-table', columns=columns) %}"
        "<tr><td data-column-key='title'>X</td><td data-column-key='updated'>Y</td></tr>"
        "{% endcall %}",
        columns=columns,
    )
    assert 'data-table data-table-id="kb-table"' in html
    assert 'data-column-key="title"' in html
    assert 'data-column-key="updated"' in html
    assert 'data-mobile-priority="essential"' in html
    assert 'data-sort="date"' in html


def test_table_column_picker_emits_checkboxes_and_reset(env):
    columns = [
        {"key": "title", "label": "Title", "default_visible": True},
        {"key": "scope", "label": "Scope", "default_visible": False},
    ]
    html = _render(
        env,
        "{% from 'macros/tables.html' import table_column_picker %}"
        "{{ table_column_picker('kb-table', columns) }}",
        columns=columns,
    )
    assert 'data-table-columns="kb-table"' in html
    assert 'data-column-key="title"' in html
    assert 'data-default-visible="true"' in html
    assert 'data-default-visible="false"' in html
    assert "Reset to defaults" in html
    assert 'data-table-columns-reset' in html


def test_table_toolbar_renders_search_filters_and_bulk_menu(env):
    filters = [
        {"name": "status", "label": "Status", "value": "open",
         "options": [("", "All"), ("open", "Open"), ("closed", "Closed")]},
    ]
    bulk_actions = [
        {"label": "Mark selected as read", "type": "button",
         "attrs": {"data-mark-read": "", "disabled": ""}},
        {"label": "Delete selected", "type": "button", "variant": "danger",
         "attrs": {"data-delete-selected": "", "disabled": ""}},
    ]
    columns = [{"key": "subject", "label": "Subject", "default_visible": True}]
    html = _render(
        env,
        "{% from 'macros/tables.html' import table_toolbar %}"
        "{{ table_toolbar('tickets-history', search=True, search_value='vip',"
        " filters=filters, page_sizes=[25, 50], page_size=25,"
        " columns=columns, bulk_actions=bulk_actions) }}",
        filters=filters, bulk_actions=bulk_actions, columns=columns,
    )
    # Search input echoed back, filter selected option preserved.
    assert 'value="vip"' in html
    assert 'selected>Open' in html
    # Page-size select rendered.
    assert "25 per page" in html
    # Column picker present.
    assert 'data-table-columns="tickets-history"' in html
    # Bulk actions present and disabled by default.
    assert 'data-table-bulk-actions="tickets-history"' in html
    assert "Delete selected" in html
    assert " disabled" in html
    # Search ties to the table via data-table-filter for tables.js.
    assert 'data-table-filter="tickets-history"' in html


# --- backwards compatibility ---------------------------------------------------


def test_data_table_without_table_id_keeps_legacy_signature(env):
    """Callers that haven't migrated must not be broken by the new params."""
    html = _render(
        env,
        "{% from 'macros/tables.html' import data_table %}"
        "{% call data_table([{'label': 'Name'}, {'label': 'Updated', 'sortable': false}]) %}"
        "<tr><td>x</td><td>y</td></tr>{% endcall %}",
    )
    assert "<table" in html
    assert "data-table-id" not in html
    # First column is sortable by default, second has the flag flipped off.
    assert html.count("sortable") >= 1
