"""Tests for the reporting service: SQL validation and exporters."""
from __future__ import annotations

import asyncio
import os
import sys
import types
from datetime import datetime
from importlib import util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ.setdefault("SESSION_SECRET", "test")
os.environ.setdefault("TOTP_ENCRYPTION_KEY", "A" * 64)

import pytest

from app.services import reporting


def test_validate_select_passes_simple_select():
    assert reporting.validate_select_query("SELECT 1 AS x").upper().startswith("SELECT")


def test_validate_select_strips_trailing_semicolon_and_comments():
    cleaned = reporting.validate_select_query("  SELECT id FROM users; -- comment ")
    assert cleaned == "SELECT id FROM users"


def test_validate_select_allows_with_cte():
    cleaned = reporting.validate_select_query(
        "WITH t AS (SELECT 1 AS n) SELECT * FROM t"
    )
    assert cleaned.startswith("WITH")


def test_validate_select_allows_keyword_inside_string_literal():
    cleaned = reporting.validate_select_query(
        "SELECT name FROM users WHERE name = 'UPDATE me'"
    )
    assert "UPDATE" in cleaned


def test_validate_select_handles_doubled_quote_escape():
    cleaned = reporting.validate_select_query(
        "SELECT name FROM users WHERE name = 'O''Brien'"
    )
    assert "O''Brien" in cleaned


@pytest.mark.parametrize(
    "bad_sql",
    [
        "DELETE FROM users",
        "UPDATE users SET email = 'x'",
        "INSERT INTO users (email) VALUES ('x')",
        "DROP TABLE users",
        "TRUNCATE TABLE users",
        "ALTER TABLE users ADD COLUMN x INT",
        "SELECT 1; SELECT 2",  # multiple statements
        "SET autocommit = 0",
        "SELECT * FROM users INTO OUTFILE '/tmp/x'",
        "/* hidden */ INSERT INTO users (email) VALUES ('x')",
        "",
        "   ",
    ],
)
def test_validate_select_rejects_unsafe_sql(bad_sql):
    with pytest.raises(reporting.ReportingQueryError):
        reporting.validate_select_query(bad_sql)


def test_export_csv_handles_none_and_decimal():
    from decimal import Decimal

    csv_text = reporting.export_csv(
        ["a", "b", "c"],
        [
            {"a": 1, "b": "hello", "c": Decimal("3.50")},
            {"a": 2, "b": None, "c": None},
        ],
    )
    lines = csv_text.strip().splitlines()
    assert lines[0] == "a,b,c"
    assert lines[1] == "1,hello,3.50"
    assert lines[2] == "2,,"


def test_export_json_serialises_dates_and_decimal():
    from decimal import Decimal

    text = reporting.export_json(
        ["d", "v"],
        [{"d": datetime(2025, 1, 2, 3, 4, 5), "v": Decimal("1.5")}],
    )
    assert "2025-01-02T03:04:05" in text
    assert '"1.5"' in text


def test_export_xml_sanitises_column_names_and_escapes_values():
    text = reporting.export_xml(
        ["col one", "v"],
        [{"col one": "<value>", "v": "ok"}],
    )
    assert "<col_one>&lt;value&gt;</col_one>" in text
    assert "<v>ok</v>" in text


def test_export_html_for_pdf_includes_name_and_rows():
    html = reporting.export_html_for_pdf(
        "My Report",
        "A description",
        ["a", "b"],
        [{"a": 1, "b": "<x>"}],
        datetime(2025, 5, 1, 12, 0, 0),
    )
    assert "<h1>My Report</h1>" in html
    assert "A description" in html
    assert "&lt;x&gt;" in html
    assert "2025-05-01" in html
    assert "table-layout:fixed" in html
    assert "overflow-wrap:anywhere" in html


def test_export_html_for_pdf_handles_no_rows():
    html = reporting.export_html_for_pdf(
        "Empty", None, ["a"], [], datetime(2025, 1, 1, 0, 0, 0)
    )
    assert "No rows." in html
