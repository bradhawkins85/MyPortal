from pathlib import Path
import pytest
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
