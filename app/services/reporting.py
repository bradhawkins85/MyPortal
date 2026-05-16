"""Reporting service: SELECT-only SQL validation, execution, and exporters.

Reports are author-supplied SQL queries that super admins create from the
Reporting admin page. To minimise the blast radius of a malicious or buggy
query we:

* Strip comments and reject anything that is not a single ``SELECT`` (or
  ``WITH`` … ``SELECT``) statement.
* Reject statements containing dangerous keywords (``INSERT``, ``UPDATE``,
  ``DELETE``, ``DROP`` …) or attempts to write files via ``INTO OUTFILE``.
* Wrap the validated query in ``SELECT * FROM (<query>) AS subq LIMIT N`` so
  even a careless ``SELECT *`` cannot return millions of rows.
* Execute on the same connection pool but never inside a writable
  transaction context; the wrapper above guarantees we only run a single
  read statement.
"""
from __future__ import annotations

import csv
import io
import json
import re
import xml.etree.ElementTree as ET
from datetime import date, datetime
from decimal import Decimal
from html import escape
from typing import Any, Iterable, Mapping

from app.core.database import db


MAX_RESULT_ROWS = 5000


class ReportingQueryError(ValueError):
    """Raised when an author-supplied SQL query fails validation."""


# Statement-level keywords that indicate a write or otherwise unsafe action.
_FORBIDDEN_KEYWORDS = (
    "INSERT",
    "UPDATE",
    "DELETE",
    "DROP",
    "ALTER",
    "CREATE",
    "TRUNCATE",
    "REPLACE",
    "RENAME",
    "GRANT",
    "REVOKE",
    "SET",
    "CALL",
    "LOCK",
    "UNLOCK",
    "HANDLER",
    "LOAD",
    "ATTACH",
    "DETACH",
    "PRAGMA",
    "MERGE",
    "USE",
)

_FORBIDDEN_PHRASES = (
    "INTO OUTFILE",
    "INTO DUMPFILE",
)


def _strip_sql_comments(sql: str) -> str:
    """Remove ``--``/``#`` line comments and ``/* … */`` block comments."""
    out: list[str] = []
    i = 0
    n = len(sql)
    in_single = False
    in_double = False
    while i < n:
        ch = sql[i]
        nxt = sql[i + 1] if i + 1 < n else ""
        if not in_single and not in_double:
            if ch == "-" and nxt == "-":
                while i < n and sql[i] != "\n":
                    i += 1
                continue
            if ch == "#":
                while i < n and sql[i] != "\n":
                    i += 1
                continue
            if ch == "/" and nxt == "*":
                i += 2
                while i + 1 < n and not (sql[i] == "*" and sql[i + 1] == "/"):
                    i += 1
                i += 2
                continue
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        out.append(ch)
        i += 1
    return "".join(out)


def _split_top_level_statements(sql: str) -> list[str]:
    """Split a SQL string on top-level semicolons, ignoring those inside strings."""
    statements: list[str] = []
    buf: list[str] = []
    in_single = False
    in_double = False
    for ch in sql:
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        if ch == ";" and not in_single and not in_double:
            piece = "".join(buf).strip()
            if piece:
                statements.append(piece)
            buf = []
            continue
        buf.append(ch)
    tail = "".join(buf).strip()
    if tail:
        statements.append(tail)
    return statements


def _strip_string_literals(sql: str) -> str:
    """Replace single/double-quoted string literals with empty placeholders.

    Used before scanning for forbidden keywords so the contents of string
    literals (e.g. ``WHERE name = 'UPDATE'``) cannot trigger false positives.
    """
    out: list[str] = []
    i = 0
    n = len(sql)
    in_single = False
    in_double = False
    while i < n:
        ch = sql[i]
        nxt = sql[i + 1] if i + 1 < n else ""
        if not in_single and not in_double:
            if ch == "'":
                in_single = True
                i += 1
                continue
            if ch == '"':
                in_double = True
                i += 1
                continue
            out.append(ch)
            i += 1
            continue
        # Inside a quoted literal — skip until the matching closing quote,
        # honouring SQL doubled-quote escaping ('' or "").
        if in_single:
            if ch == "'" and nxt == "'":
                i += 2
                continue
            if ch == "'":
                in_single = False
                i += 1
                continue
            i += 1
            continue
        if in_double:
            if ch == '"' and nxt == '"':
                i += 2
                continue
            if ch == '"':
                in_double = False
                i += 1
                continue
            i += 1
            continue
    return "".join(out)


def validate_select_query(sql: str) -> str:
    """Validate ``sql`` is a single read-only SELECT/WITH statement.

    Returns the cleaned (comment-stripped, single-statement) SQL on success;
    raises :class:`ReportingQueryError` otherwise. The returned string never
    contains a trailing semicolon so it is safe to wrap inside a subquery.
    """
    if not sql or not sql.strip():
        raise ReportingQueryError("SQL query is required.")

    cleaned = _strip_sql_comments(sql).strip()
    if not cleaned:
        raise ReportingQueryError("SQL query is required.")

    statements = _split_top_level_statements(cleaned)
    if not statements:
        raise ReportingQueryError("SQL query is required.")
    if len(statements) > 1:
        raise ReportingQueryError(
            "Only a single SELECT statement is allowed (found multiple statements)."
        )

    statement = statements[0]
    upper = statement.upper()
    # For keyword scanning, strip string literals so values inside quotes do
    # not trigger false positives like WHERE name = 'UPDATE'.
    upper_no_strings = _strip_string_literals(upper)

    # Must begin with SELECT or WITH
    first_token_match = re.match(r"\s*([A-Z]+)", upper_no_strings)
    if not first_token_match or first_token_match.group(1) not in {"SELECT", "WITH"}:
        raise ReportingQueryError("Only SELECT statements are allowed.")

    # Reject dangerous keywords as standalone tokens
    tokens = set(re.findall(r"\b[A-Z]+\b", upper_no_strings))
    for keyword in _FORBIDDEN_KEYWORDS:
        if keyword in tokens:
            raise ReportingQueryError(
                f"Statement contains the forbidden keyword '{keyword}'."
            )
    for phrase in _FORBIDDEN_PHRASES:
        if phrase in upper_no_strings:
            raise ReportingQueryError(
                f"Statement contains the forbidden phrase '{phrase}'."
            )

    return statement


async def run_query(sql: str, *, max_rows: int = MAX_RESULT_ROWS) -> dict[str, Any]:
    """Validate and execute ``sql``, returning columns and rows.

    Returns ``{"columns": [...], "rows": [{...}, ...], "row_count": int,
    "truncated": bool}``. ``truncated`` is ``True`` when the underlying query
    returned more than ``max_rows`` rows (the result is capped at the limit).
    """
    statement = validate_select_query(sql)
    # Wrap the query so a careless SELECT * cannot return unbounded rows.
    fetch_limit = max_rows + 1
    wrapped = f"SELECT * FROM ({statement}) AS reporting_subq LIMIT {int(fetch_limit)}"
    raw_rows = await db.fetch_all(wrapped)
    rows = [dict(r) for r in (raw_rows or [])]
    truncated = len(rows) > max_rows
    if truncated:
        rows = rows[:max_rows]
    columns: list[str] = []
    for row in rows:
        for key in row.keys():
            if key not in columns:
                columns.append(key)
    return {
        "columns": columns,
        "rows": rows,
        "row_count": len(rows),
        "truncated": truncated,
    }


# ---------------------------------------------------------------------------
# Exporters
# ---------------------------------------------------------------------------


def _coerce_for_export(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (bytes, bytearray)):
        try:
            return value.decode("utf-8")
        except UnicodeDecodeError:
            return value.hex()
    return value


def _coerce_for_json(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (bytes, bytearray)):
        try:
            return value.decode("utf-8")
        except UnicodeDecodeError:
            return value.hex()
    return value


def export_csv(columns: Iterable[str], rows: Iterable[Mapping[str, Any]]) -> str:
    buf = io.StringIO()
    column_list = list(columns)
    writer = csv.writer(buf, quoting=csv.QUOTE_MINIMAL)
    writer.writerow(column_list)
    for row in rows:
        writer.writerow([_coerce_for_export(row.get(col)) for col in column_list])
    return buf.getvalue()


def export_json(columns: Iterable[str], rows: Iterable[Mapping[str, Any]]) -> str:
    column_list = list(columns)
    payload = [
        {col: _coerce_for_json(row.get(col)) for col in column_list}
        for row in rows
    ]
    return json.dumps(payload, indent=2, default=str)


_XML_TAG_RE = re.compile(r"[^A-Za-z0-9_]")


def _safe_xml_tag(name: str) -> str:
    cleaned = _XML_TAG_RE.sub("_", str(name))
    if not cleaned or not (cleaned[0].isalpha() or cleaned[0] == "_"):
        cleaned = f"col_{cleaned}"
    return cleaned


def export_xml(columns: Iterable[str], rows: Iterable[Mapping[str, Any]]) -> str:
    column_list = list(columns)
    tag_map = {col: _safe_xml_tag(col) for col in column_list}
    root = ET.Element("results")
    for row in rows:
        row_el = ET.SubElement(root, "row")
        for col in column_list:
            cell = ET.SubElement(row_el, tag_map[col])
            value = row.get(col)
            cell.text = "" if value is None else str(_coerce_for_export(value))
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(
        root, encoding="unicode"
    )


def export_html_for_pdf(
    name: str,
    description: str | None,
    columns: Iterable[str],
    rows: Iterable[Mapping[str, Any]],
    generated_at: datetime,
) -> str:
    column_list = list(columns)
    rows_list = list(rows)
    parts: list[str] = []
    parts.append("<!DOCTYPE html><html><head><meta charset=\"utf-8\"/>")
    parts.append(f"<title>{escape(name)}</title>")
    parts.append(
        "<style>"
        "body{font-family:Helvetica,Arial,sans-serif;font-size:10pt;color:#111;}"
        "h1{font-size:18pt;margin:0 0 8pt;}"
        "p.meta{color:#555;margin:0 0 12pt;}"
        "p.desc{margin:0 0 12pt;}"
        "table{border-collapse:collapse;width:100%;table-layout:fixed;}"
        "th,td{border:1px solid #ccc;padding:4pt 6pt;text-align:left;vertical-align:top;white-space:normal;word-break:break-word;overflow-wrap:anywhere;}"
        "th{background:#f2f4f7;}"
        "tr:nth-child(even) td{background:#fafafa;}"
        "</style></head><body>"
    )
    parts.append(f"<h1>{escape(name)}</h1>")
    parts.append(
        "<p class=\"meta\">Generated "
        f"{escape(generated_at.strftime('%Y-%m-%d %H:%M UTC'))} · "
        f"{len(rows_list)} row(s)</p>"
    )
    if description:
        parts.append(f"<p class=\"desc\">{escape(description)}</p>")
    parts.append("<table><thead><tr>")
    for col in column_list:
        parts.append(f"<th>{escape(str(col))}</th>")
    parts.append("</tr></thead><tbody>")
    if not rows_list:
        parts.append(
            f"<tr><td colspan=\"{max(len(column_list), 1)}\">No rows.</td></tr>"
        )
    else:
        for row in rows_list:
            parts.append("<tr>")
            for col in column_list:
                value = row.get(col)
                text = "" if value is None else str(_coerce_for_export(value))
                parts.append(f"<td>{escape(text)}</td>")
            parts.append("</tr>")
    parts.append("</tbody></table></body></html>")
    return "".join(parts)
