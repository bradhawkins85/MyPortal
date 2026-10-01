"""Unit coverage for the configurable Company Overview layout."""
from __future__ import annotations

import asyncio

import pytest

from app.services import company_report_layout as layout


def test_default_layout_contains_basics_and_variable_width_rows():
    rows = layout.default_layout()
    assert len(rows[0]["columns"]) == 3
    assert all(len(row["columns"]) == 1 for row in rows[1:])
    assert {cell["slug"] for row in rows for cell in row["columns"]} >= {
        "report-assets-synced-last-30-days",
        "report-m365-best-practice-summary",
        "report-licenses",
    }


def test_normalise_layout_caps_columns_and_sanitises_thresholds():
    cells = [{
        "slug": "valid", "display": "stat", "aggregate": "count",
        "thresholds": [
            {"operator": "gte", "value": "10", "colour": "#14532D"},
            {"operator": "invalid", "value": 1, "colour": "danger"},
        ],
    }] * 15
    rows = layout.normalise_layout([{"title": "Summary", "columns": cells}], {"valid"})
    assert len(rows[0]["columns"]) == 12
    assert rows[0]["columns"][0]["thresholds"] == [
        {"operator": "gte", "value": 10.0, "colour": "#14532d"}
    ]


def test_normalise_layout_preserves_dividers_and_rejects_unsafe_colours():
    rows = layout.normalise_layout([
        {"type": "divider", "title": "Security"},
        {"columns": [{"slug": "valid", "display": "stat", "thresholds": [
            {"operator": "gte", "value": 1, "colour": "red; display:none"},
        ]}]},
    ], {"valid"})
    assert rows[0] == {"type": "divider", "title": "Security", "height": 50}
    assert rows[1]["columns"][0]["thresholds"] == []


def test_normalise_layout_validates_divider_height():
    rows = layout.normalise_layout([
        {"type": "divider", "height": "72"},
        {"type": "divider", "height": 9999},
        {"columns": [{"slug": "valid"}]},
    ], {"valid"})
    assert rows[0]["height"] == 72
    assert rows[1]["height"] == layout.MAX_DIVIDER_HEIGHT


def test_normalise_layout_rejects_empty_or_unknown_slugs():
    with pytest.raises(ValueError, match="at least one row"):
        layout.normalise_layout([{"columns": [{"slug": "unknown"}]}], {"valid"})


def test_stat_aggregation_filter_and_ordered_threshold_colours():
    result = {
        "columns": ["status", "score"],
        "rows": [
            {"status": "pass", "score": 10},
            {"status": "fail", "score": 2},
            {"status": "pass", "score": 20},
        ],
    }
    config = {"aggregate": "average", "value_column": "score", "filter_column": "status", "filter_value": "pass"}
    assert layout._stat_value(config, result) == 15
    assert layout._variant(15, [
        {"operator": "gte", "value": 20, "colour": "#14532d"},
        {"operator": "gte", "value": 10, "colour": "#d99b16"},
    ]) == "#d99b16"


def test_available_queries_excludes_reports_without_company_context(monkeypatch):
    async def list_queries():
        return [
            {"slug": "tenant", "sql_query": "SELECT * FROM assets WHERE company_id = {{current.company}}"},
            {"slug": "global", "sql_query": "SELECT * FROM assets"},
        ]

    monkeypatch.setattr(layout.reporting_repo, "list_queries", list_queries)

    queries = asyncio.run(layout.available_queries())

    assert [query["slug"] for query in queries] == ["tenant"]


def test_build_does_not_execute_global_query_from_saved_layout(monkeypatch):
    async def list_queries():
        return [{"slug": "global", "name": "Global", "sql_query": "SELECT * FROM assets"}]

    async def get_layout(_company_id):
        return [{"columns": [{"slug": "global", "display": "table"}]}]

    async def fail_if_executed(*_args, **_kwargs):
        pytest.fail("global query must not execute in a company report")

    monkeypatch.setattr(layout.reporting_repo, "list_queries", list_queries)
    monkeypatch.setattr(layout.layout_repo, "get_layout", get_layout)
    monkeypatch.setattr(layout.reporting_service, "run_query_with_context", fail_if_executed)

    report = asyncio.run(layout.build(1, {"id": 1}))

    assert report.rows[0]["columns"][0]["error"] == "Reporting slug not found."


def test_stat_strip_display_is_preserved_and_builds_semantic_tiles():
    rows = layout.normalise_layout(
        [{"columns": [{"slug": "backup", "display": "stat_strip"}]}],
        {"backup"},
    )
    assert rows[0]["columns"][0]["display"] == "stat_strip"

    from app.services.stat_strips import build_items
    items = build_items({
        "columns": ["total_jobs", "pass", "fail", "unknown"],
        "rows": [{"total_jobs": 8, "pass": 6, "fail": 1, "unknown": 1}],
    })
    assert [(item["label"], item["variant"]) for item in items] == [
        ("Total Jobs", "total"),
        ("Pass", "success"),
        ("Fail", "danger"),
        ("Unknown", "warning"),
    ]
