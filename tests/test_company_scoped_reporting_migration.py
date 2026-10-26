"""Coverage for the company scoped copies of the reporting catalogue."""

from __future__ import annotations

import asyncio
import re
import sqlite3
from pathlib import Path
from types import SimpleNamespace

from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.core.database import Database
from app.features.reporting import handlers as reporting_handlers
from app.repositories import reporting as reporting_repo
from app.services.reporting import (
    _SENSITIVE_COLUMN_PATTERN,
    substitute_query_context,
    uses_company_context,
    validate_select_query,
)

ROOT = Path(__file__).parent.parent
MIGRATION = ROOT / "migrations" / "525_company_scoped_reporting_queries.sql"
TEMPLATES_DIR = ROOT / "app" / "templates"
SUFFIX = "-company-scoped"


def _sql() -> str:
    return MIGRATION.read_text(encoding="utf-8")


def _entries() -> list[tuple[str, str, str, str, str, int]]:
    """Load the migration's rows into an in-memory table and read them back."""
    statements = Database()._split_sql_statements(_sql())
    assert len(statements) == 1
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE reporting_queries (slug TEXT UNIQUE, name TEXT, "
        "description TEXT, category TEXT, sql_query TEXT, is_system INTEGER)"
    )
    conn.execute(statements[0].replace("INSERT IGNORE INTO", "INSERT OR IGNORE INTO", 1))
    return conn.execute(
        "SELECT slug, name, description, category, sql_query, is_system FROM reporting_queries"
    ).fetchall()


def _earlier_slugs() -> set[str]:
    slugs: set[str] = set()
    for path in MIGRATION.parent.glob("*.sql"):
        if int(path.name.split("_", 1)[0]) >= 525:
            continue
        text = path.read_text(encoding="utf-8")
        if "reporting_queries" in text:
            slugs.update(re.findall(r"'([a-z0-9]+(?:-[a-z0-9]+)+)'", text))
    return slugs


def test_migration_only_inserts_new_system_reports():
    sql = _sql()
    statement = Database()._split_sql_statements(sql)[0]

    assert sql.startswith("-- phase: data")
    assert statement.startswith("INSERT IGNORE INTO reporting_queries")
    assert not re.search(r"\b(UPDATE|DELETE|ALTER|REPLACE)\b", re.sub(r"'(?:[^']|'')*'", "''", statement))
    entries = _entries()
    assert len(entries) >= 100
    assert all(is_system == 1 for *_, is_system in entries)


def test_copies_are_named_and_slugged_as_company_scoped_copies_of_existing_reports():
    entries = _entries()
    earlier = _earlier_slugs()
    slugs = [slug for slug, *_ in entries]

    assert len(slugs) == len(set(slugs))
    assert not set(slugs) & earlier
    for slug, name, description, category, _query, _ in entries:
        assert slug.endswith(SUFFIX) and len(slug) <= 120, slug
        assert slug[: -len(SUFFIX)] in earlier, slug
        assert name.endswith(" (Company Scoped)") and len(name) <= 255, slug
        assert description and "Company scoped" in description, slug
        assert category, slug


def test_every_copy_is_scoped_to_the_current_company_and_passes_validation():
    for slug, _name, _description, _category, query, _ in _entries():
        assert uses_company_context(query), slug
        assert validate_select_query(query) == query, slug
        for company_id in (7, None):
            assert "{{" not in substitute_query_context(query, company_id=company_id), slug
        for alias in re.findall(r"\bAS (\w+)", query):
            assert not _SENSITIVE_COLUMN_PATTERN.search(alias), (slug, alias)


def test_copies_add_a_company_filter_to_the_original_query():
    queries = {slug: query for slug, _n, _d, _c, query, _ in _entries()}

    assert queries["unbilled-tickets-company-scoped"].startswith(
        "SELECT t.id AS ticket_id, c.name AS company"
    )
    assert "WHERE t.company_id = {{current.company}} AND t.xero_invoice_number IS NULL" in (
        queries["unbilled-tickets-company-scoped"]
    )
    # An existing OR condition is bracketed so the company filter still applies.
    assert (
        "WHERE r.company_id = {{current.company}} AND (r.bounced_at IS NOT NULL OR "
        "r.status IN ('skipped', 'failed'))"
    ) in queries["marketing-bounces-and-skips-company-scoped"]
    # Settings with no company apply to every company.
    assert "WHERE COALESCE(sch.company_id, {{current.company}}) = {{current.company}}" in (
        queries["rmm-schedules-company-scoped"]
    )


def test_manage_reports_flags_company_scoped_reports_from_their_sql(monkeypatch):
    captured: dict[str, object] = {}
    records = [
        {"id": 1, "name": "Global", "sql_query": "SELECT id FROM tickets", "updated_at": None},
        {
            "id": 2,
            "name": "Scoped",
            "sql_query": "SELECT id FROM tickets WHERE company_id = {{ current.company }}",
            "updated_at": None,
        },
    ]

    async def fake_require(_request):
        return {"id": 1, "is_super_admin": True}, None

    async def fake_render(template, _request, _user, *, extra):
        captured.update(extra=extra)
        return SimpleNamespace(status_code=200)

    async def fake_list_queries():
        return records

    monkeypatch.setattr(
        reporting_handlers,
        "_main",
        lambda: SimpleNamespace(
            _require_super_admin_page=fake_require, _render_template=fake_render, _to_iso=lambda _v: None
        ),
    )
    monkeypatch.setattr(reporting_repo, "list_queries", fake_list_queries)

    asyncio.run(reporting_handlers.admin_reporting(SimpleNamespace()))

    flags = {report["id"]: report["company_scoped"] for report in captured["extra"]["reports"]}
    assert flags == {1: False, 2: True}


def test_manage_reports_table_has_a_filterable_company_scoped_column():
    env = Environment(loader=FileSystemLoader(TEMPLATES_DIR), autoescape=select_autoescape(("html",)))
    template = env.get_template("admin/reporting.html")
    base = {"name": "R", "slug": "r", "category": "Billing", "is_system": True, "updated_at_iso": None}
    context = template.new_context(
        {
            "reports": [
                {**base, "id": 1, "company_scoped": True},
                {**base, "id": 2, "company_scoped": False},
            ],
            "report_columns": reporting_handlers.REPORT_COLUMNS,
            "csrf_token": "tok",
        }
    )
    content = "".join(template.blocks["content"](context))

    assert re.search(
        r'<th[^>]*data-column-key="company_scoped"[^>]*data-filter-options="multiple"[^>]*>Company scoped</th>',
        content,
    )
    cells = re.findall(r'<td data-column-key="company_scoped"[^>]*data-value="(\w+)">(.*?)</td>', content, re.S)
    assert [value for value, _ in cells] == ["Yes", "No"]
    assert "&#10003;" in cells[0][1] and "&#10003;" not in cells[1][1]
