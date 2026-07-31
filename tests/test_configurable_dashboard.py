import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from app.services import dashboard_layouts
from app.services.dashboard_layouts import InvalidDashboardLayout, validate_layout


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


def test_resolve_layout_omits_report_panels_without_permission(monkeypatch):
    layout = validate_layout(
        {
            "panels": [
                {"id": "help", "type": "link", "title": "Help", "url": "/tickets"},
                {"id": "private", "type": "stat", "title": "Invoices", "report": "invoices"},
            ]
        }
    )
    monkeypatch.setattr(
        dashboard_layouts.reporting_repo,
        "get_query_by_slug",
        AsyncMock(return_value={"id": 7, "sql_query": "SELECT secret FROM invoices"}),
    )
    monkeypatch.setattr(
        dashboard_layouts.reporting_repo, "user_has_permission", AsyncMock(return_value=False)
    )
    run_query = AsyncMock()
    monkeypatch.setattr(dashboard_layouts.reporting_service, "run_query_with_context", run_query)

    result = asyncio.run(
        dashboard_layouts.resolve_layout(
            layout, company_id=4, can_run_all=False, user_id=9
        )
    )

    assert [panel["id"] for panel in result["panels"]] == ["help"]
    run_query.assert_not_awaited()
