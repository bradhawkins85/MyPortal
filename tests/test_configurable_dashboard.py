import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from app.api.routes import dashboard as dashboard_routes
from app.services import dashboard_layouts
from app.services.dashboard_layouts import InvalidDashboardLayout, validate_layout


def dashboard_request(permission: str) -> Request:
    request = Request(
        {"type": "http", "method": "GET", "path": "/api/dashboard", "headers": []}
    )
    request.state.active_company_id = 7
    request.state.active_membership = {
        "menu_permissions": {"menu.dashboard": permission}
    }
    return request


def test_dashboard_editability_requires_write_access():
    user = {"id": 42, "is_super_admin": False}

    assert (
        asyncio.run(dashboard_routes._editable(user, dashboard_request("read")))
        is False
    )
    assert (
        asyncio.run(dashboard_routes._editable(user, dashboard_request("write")))
        is True
    )


def test_super_admin_can_edit_dashboard_without_active_membership():
    request = Request(
        {"type": "http", "method": "GET", "path": "/api/dashboard", "headers": []}
    )

    assert (
        asyncio.run(
            dashboard_routes._editable({"id": 1, "is_super_admin": True}, request)
        )
        is True
    )


def test_read_only_dashboard_role_cannot_save_reset_or_resolve(monkeypatch):
    user = {"id": 42, "is_super_admin": False}
    request = dashboard_request("read")
    delete_personal = AsyncMock()
    monkeypatch.setattr(
        dashboard_routes.layouts_repo, "delete_personal", delete_personal
    )

    for operation in (
        dashboard_routes.save_dashboard({}, request, user),
        dashboard_routes.reset_dashboard(request, user),
        dashboard_routes.resolve_dashboard({}, request, user),
    ):
        with pytest.raises(HTTPException) as exc_info:
            asyncio.run(operation)
        assert exc_info.value.status_code == 403

    delete_personal.assert_not_awaited()


def test_layout_accepts_all_panel_types():
    layout = validate_layout(
        {
            "title": "Operations",
            "panels": [
                {
                    "id": "a",
                    "type": "link",
                    "title": "Tickets",
                    "url": "/tickets",
                    "label": "Open",
                },
                {
                    "id": "b",
                    "type": "stat",
                    "title": "Count",
                    "report": "dashboard-open-tickets",
                    "function": "count",
                },
                {
                    "id": "c",
                    "type": "variable",
                    "title": "Version",
                    "variable": "app_version",
                },
                {
                    "id": "d",
                    "type": "graph",
                    "title": "Trend",
                    "report": "dashboard-tickets-created-30-days",
                    "chart": "line",
                },
            ],
        }
    )
    assert [panel["type"] for panel in layout["panels"]] == [
        "link",
        "stat",
        "variable",
        "graph",
    ]
    assert layout["panels"][2]["variable"] == "APP_VERSION"


def test_layout_rejects_unsafe_link_and_duplicate_ids():
    with pytest.raises(InvalidDashboardLayout):
        validate_layout(
            {"panels": [{"id": "x", "type": "link", "url": "javascript:alert(1)"}]}
        )
    with pytest.raises(InvalidDashboardLayout):
        validate_layout(
            {
                "panels": [
                    {"id": "x", "type": "variable"},
                    {"id": "x", "type": "variable"},
                ]
            }
        )


def test_dashboard_seed_catalog_has_at_least_30_prefixed_queries():
    sql = Path("migrations/309_configurable_dashboards.sql").read_text()
    assert sql.count("INSERT IGNORE INTO reporting_queries") >= 30
    assert sql.count("'dashboard-") >= 30
    assert sql.count("'Dashboard -") >= 30


def test_license_product_dashboard_query_uses_license_name_column():
    sql = Path("migrations/314_fix_dashboard_license_product_query.sql").read_text()
    query_update = next(
        line for line in sql.splitlines() if line.startswith("SET sql_query")
    )

    assert "COALESCE(name, ''Other'') AS X" in query_update
    assert "GROUP BY name ORDER BY Y DESC" in query_update
    assert "product_name" not in query_update


def test_client_dashboard_example_is_valid():
    import json

    layout = json.loads(Path("docs/examples/client-dashboard.json").read_text())
    assert validate_layout(layout)["title"] == "Client service overview"


def test_dashboard_builder_uses_form_elements_collection():
    script = Path("app/static/js/dashboard.js").read_text()

    assert "const builderForm = dialog?.querySelector('form')" in script
    assert "builderForm?.elements.type.addEventListener" in script
    assert "dialog?.elements.type" not in script


def test_stat_colours_and_custom_panel_size_are_validated():
    panel = validate_layout(
        {
            "panels": [
                {
                    "id": "count",
                    "type": "stat",
                    "report": "example",
                    "function": "count",
                    "compare_value": "12.5",
                    "less_colour": "#112233",
                    "equal_colour": "not-a-colour",
                    "greater_colour": "#AABBCC",
                    "w": 1,
                    "h": 12,
                }
            ]
        }
    )["panels"][0]

    assert panel["compare_value"] == 12.5
    assert panel["less_colour"] == "#112233"
    assert panel["equal_colour"] == "#1e3a8a"
    assert panel["greater_colour"] == "#AABBCC"
    assert (panel["w"], panel["h"]) == (1, 12)


def test_stat_panel_preserves_optional_detail_report():
    panel = validate_layout(
        {
            "panels": [
                {
                    "id": "open-tickets",
                    "type": "stat",
                    "report": "dashboard-open-tickets",
                    "detail_report": "all-open-ticket-details",
                }
            ]
        }
    )["panels"][0]

    assert panel["detail_report"] == "all-open-ticket-details"


def test_linked_stat_resolves_permitted_report_url(monkeypatch):
    layout = validate_layout(
        {
            "panels": [
                {
                    "id": "open-tickets",
                    "type": "stat",
                    "report": "dashboard-open-tickets",
                    "detail_report": "open-ticket-details",
                }
            ]
        }
    )
    monkeypatch.setattr(
        dashboard_layouts.reporting_repo,
        "get_query_by_slug",
        AsyncMock(
            side_effect=[
                {"id": 1, "sql_query": "SELECT id FROM tickets"},
                {"id": 42, "sql_query": "SELECT * FROM tickets"},
            ]
        ),
    )
    monkeypatch.setattr(
        dashboard_layouts.reporting_service,
        "run_query_with_context",
        AsyncMock(return_value={"columns": ["id"], "rows": [{"id": 1}]}),
    )

    result = asyncio.run(
        dashboard_layouts.resolve_layout(
            layout, company_id=None, can_run_all=True, user_id=9
        )
    )

    assert result["panels"][0]["detail_url"] == "/reporting?report=42"


def test_dashboard_renders_linked_stat_as_accessible_link():
    script = Path("app/static/js/dashboard.js").read_text()
    template = Path("app/templates/dashboard.html").read_text()

    assert "panel.type === 'stat' && panel.detail_url" in script
    assert 'class="dashboard-panel__detail-link"' in script
    assert 'name="detail_report"' in template


def test_detail_report_picker_reuses_reporting_query_options_after_form_reset():
    script = Path("app/static/js/dashboard.js").read_text()

    reset = script.index("builderForm.reset();")
    options = script.index("const reportOptions =", reset)
    reporting_picker = script.index("reports.innerHTML = reportOptions;", options)
    detail_picker = script.index(
        "detailReports.innerHTML = '<option value=\"\">No linked report</option>' + reportOptions;",
        reporting_picker,
    )

    assert reset < options < reporting_picker < detail_picker


def test_listall_stat_preserves_and_returns_every_column(monkeypatch):
    layout = validate_layout(
        {
            "panels": [
                {
                    "id": "details",
                    "type": "stat",
                    "report": "ticket-details",
                    "function": "listall",
                }
            ]
        }
    )
    assert layout["panels"][0]["function"] == "listall"
    monkeypatch.setattr(
        dashboard_layouts.reporting_repo,
        "get_query_by_slug",
        AsyncMock(return_value={"id": 1, "sql_query": "SELECT ..."}),
    )
    monkeypatch.setattr(
        dashboard_layouts.reporting_service,
        "run_query_with_context",
        AsyncMock(
            return_value={
                "columns": ["Ticket", "Status"],
                "rows": [
                    {"Ticket": "T-1", "Status": "Open"},
                    {"Ticket": "T-2", "Status": "Closed"},
                ],
            }
        ),
    )

    result = asyncio.run(
        dashboard_layouts.resolve_layout(
            layout, company_id=None, can_run_all=True, user_id=9
        )
    )

    assert result["panels"][0]["table_data"] == {
        "columns": ["Ticket", "Status"],
        "rows": [["T-1", "Open"], ["T-2", "Closed"]],
    }


def test_dashboard_editor_resolves_unsaved_panel_data():
    script = Path("app/static/js/dashboard.js").read_text()

    assert "await resolveState();" in script
    assert "api('/api/dashboard/resolve'" in script


def test_dashboard_supports_free_placement_auto_height_and_dirty_save_state():
    script = Path("app/static/js/dashboard.js").read_text()
    template = Path("app/templates/dashboard.html").read_text()

    assert "element.style.gridColumn" in script
    assert "element.style.gridRow" in script
    assert "function resizeAutomaticPanels()" in script
    assert "Math.max(1, Math.min(6" in script
    assert "dashboard-panel__resize" not in script
    assert "function makeRoom(moved)" in script
    assert "function setDirty(value = true)" in script
    assert "data-dashboard-save disabled" in template


def test_zero_panel_height_is_preserved_for_automatic_sizing():
    panel = validate_layout(
        {"panels": [{"id": "dynamic", "type": "variable", "h": 0}]}
    )["panels"][0]

    assert panel["h"] == 0


def test_automatic_height_measures_unconstrained_panel_content():
    script = Path("app/static/js/dashboard.js").read_text()

    assert "const measurement = element.cloneNode(true)" in script
    assert "gridRow: 'auto'" in script
    assert "child.style.overflow = 'visible'" in script
    assert "const contentHeight = measurement.scrollHeight" in script
    assert "element.scrollHeight + gap" not in script


def test_dashboard_renders_each_supported_graph_style_with_axes_and_legend():
    script = Path("app/static/js/dashboard.js").read_text()

    assert "panel.chart === 'bar'" in script
    assert "panel.chart === 'doughnut'" in script
    assert "panel.chart === 'area'" in script
    assert "dashboard-chart__grid" in script
    assert "dashboard-chart__legend" in script


def test_resolve_layout_omits_report_panels_without_permission(monkeypatch):
    layout = validate_layout(
        {
            "panels": [
                {"id": "help", "type": "link", "title": "Help", "url": "/tickets"},
                {
                    "id": "private",
                    "type": "stat",
                    "title": "Invoices",
                    "report": "invoices",
                },
            ]
        }
    )
    monkeypatch.setattr(
        dashboard_layouts.reporting_repo,
        "get_query_by_slug",
        AsyncMock(return_value={"id": 7, "sql_query": "SELECT secret FROM invoices"}),
    )
    monkeypatch.setattr(
        dashboard_layouts.reporting_repo,
        "user_has_permission",
        AsyncMock(return_value=False),
    )
    run_query = AsyncMock()
    monkeypatch.setattr(
        dashboard_layouts.reporting_service, "run_query_with_context", run_query
    )

    result = asyncio.run(
        dashboard_layouts.resolve_layout(
            layout, company_id=4, can_run_all=False, user_id=9
        )
    )

    assert [panel["id"] for panel in result["panels"]] == ["help"]
    run_query.assert_not_awaited()


def test_graph_uses_only_explicit_x_and_y_columns(monkeypatch):
    layout = validate_layout(
        {"panels": [{"id": "trend", "type": "graph", "report": "trend"}]}
    )
    monkeypatch.setattr(
        dashboard_layouts.reporting_repo,
        "get_query_by_slug",
        AsyncMock(return_value={"id": 1, "sql_query": "SELECT ..."}),
    )
    monkeypatch.setattr(
        dashboard_layouts.reporting_service,
        "run_query_with_context",
        AsyncMock(
            return_value={
                "columns": ["id", "X", "ignored", "Y", "Y1", "Y2"],
                "rows": [
                    {"id": 99, "X": "Jan", "ignored": 500, "Y": 2, "Y1": 3, "Y2": 4},
                    {"id": 98, "X": "Feb", "ignored": 600, "Y": 5, "Y1": 6, "Y2": 7},
                ],
            }
        ),
    )

    result = asyncio.run(
        dashboard_layouts.resolve_layout(
            layout, company_id=4, can_run_all=True, user_id=9
        )
    )

    graph = result["panels"][0]["chart_data"]
    assert graph["labels"] == ["Jan", "Feb"]
    assert [series["name"] for series in graph["series"]] == ["Y", "Y1", "Y2"]
    assert graph["series"][1]["values"] == [3, 6]


def test_graph_requires_explicit_x_and_y_columns(monkeypatch):
    layout = validate_layout(
        {"panels": [{"id": "trend", "type": "graph", "report": "trend"}]}
    )
    monkeypatch.setattr(
        dashboard_layouts.reporting_repo,
        "get_query_by_slug",
        AsyncMock(return_value={"id": 1, "sql_query": "SELECT ..."}),
    )
    monkeypatch.setattr(
        dashboard_layouts.reporting_service,
        "run_query_with_context",
        AsyncMock(
            return_value={
                "columns": ["month", "total"],
                "rows": [{"month": "Jan", "total": 2}],
            }
        ),
    )

    result = asyncio.run(
        dashboard_layouts.resolve_layout(
            layout, company_id=None, can_run_all=True, user_id=9
        )
    )

    assert "columns named X and Y" in result["panels"][0]["error"]
