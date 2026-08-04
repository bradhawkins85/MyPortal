"""Unit coverage for the configurable Company Overview layout."""
from __future__ import annotations

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
    assert rows[0] == {"type": "divider", "title": "Security"}
    assert rows[1]["columns"][0]["thresholds"] == []


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
