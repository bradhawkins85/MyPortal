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


# ---------------------------------------------------------------------------
# Sensitive-column redaction
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "col_name",
    [
        "password_hash",
        "passwd",
        "client_secret",
        "totp_secret",
        "pending_totp_secret",
        "access_token",
        "refresh_token",
        "invite_token",
        "auth_token_hash",
        "xero_api_key",
        "syncro_api_key",
        "webhook_api_key",
        "api-key",
        "totp",
        "otp_code",
        "private_key",
        "private-key",
        "password_encrypted",
        "access_token_encrypted",
        "credential",
        "user_credentials",
        "credentials",
        "tokens",
        "passwords",
    ],
)
def test_redact_sensitive_rows_redacts_sensitive_columns(col_name):
    columns = [col_name, "name"]
    rows = [{col_name: "supersecret", "name": "Alice"}]
    result = reporting._redact_sensitive_rows(columns, rows)
    assert result[0][col_name] == reporting._REDACTED
    assert result[0]["name"] == "Alice"


def test_redact_sensitive_rows_leaves_safe_columns_unchanged():
    columns = ["id", "email", "created_at"]
    rows = [{"id": 1, "email": "a@b.com", "created_at": "2025-01-01"}]
    result = reporting._redact_sensitive_rows(columns, rows)
    assert result == rows


def test_redact_sensitive_rows_handles_empty_rows():
    assert reporting._redact_sensitive_rows(["password_hash"], []) == []


def test_redact_sensitive_rows_returns_same_list_when_no_sensitive_columns():
    rows = [{"a": 1}, {"a": 2}]
    result = reporting._redact_sensitive_rows(["a"], rows)
    # No sensitive column — original list returned unchanged
    assert result is rows


def test_redact_sensitive_rows_word_boundary_avoids_false_positives():
    """Columns like 'secretary_name' must NOT be redacted (contains 'secret' but it
    is not a standalone word — 'secretary' ends with alphabetic chars after 'secret')."""
    columns = ["secretary_name", "is_selected", "topic"]
    rows = [{"secretary_name": "Jane", "is_selected": True, "topic": "budget"}]
    result = reporting._redact_sensitive_rows(columns, rows)
    assert result is rows


def test_redact_sensitive_rows_case_insensitive():
    columns = ["PASSWORD_HASH", "Secret_Key"]
    rows = [{"PASSWORD_HASH": "abc", "Secret_Key": "xyz"}]
    result = reporting._redact_sensitive_rows(columns, rows)
    assert result[0]["PASSWORD_HASH"] == reporting._REDACTED
    assert result[0]["Secret_Key"] == reporting._REDACTED


def test_redact_sensitive_rows_redacts_none_values_in_sensitive_columns():
    """Even NULL values in sensitive columns should be replaced with [REDACTED]."""
    columns = ["password_hash", "name"]
    rows = [{"password_hash": None, "name": "Bob"}]
    result = reporting._redact_sensitive_rows(columns, rows)
    assert result[0]["password_hash"] == reporting._REDACTED
    assert result[0]["name"] == "Bob"

