"""Coverage for report categories and the Manage reports table."""

from __future__ import annotations

import asyncio
import re
from pathlib import Path
from types import SimpleNamespace

from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.features.reporting import handlers as reporting_handlers
from app.repositories import reporting as reporting_repo

ROOT = Path(__file__).parent.parent
MIGRATION = ROOT / "migrations" / "524_reporting_query_categories.sql"
TEMPLATES_DIR = ROOT / "app" / "templates"


def _system_slugs() -> set[str]:
    slugs: set[str] = set()
    for path in (ROOT / "migrations").glob("*.sql"):
        if int(path.name.split("_", 1)[0]) >= 524:
            continue
        text = path.read_text(encoding="utf-8")
        if "reporting_queries" in text:
            slugs.update(re.findall(r"'([a-z0-9]+(?:-[a-z0-9]+)+)'", text))
    return slugs


def _categorised_slugs() -> dict[str, list[str]]:
    sql = MIGRATION.read_text(encoding="utf-8")
    result: dict[str, list[str]] = {}
    for category, body in re.findall(
        r"UPDATE reporting_queries SET category = '([^']+)'\s+WHERE category IS NULL AND is_system = 1 AND slug IN \((.*?)\);",
        sql,
        re.S,
    ):
        result[category] = re.findall(r"'([^']+)'", body)
    return result


def test_migration_adds_category_column_and_keeps_admin_choices():
    sql = MIGRATION.read_text(encoding="utf-8")

    assert sql.startswith("-- phase: expand")
    assert "ALTER TABLE reporting_queries ADD COLUMN IF NOT EXISTS category VARCHAR(64) NULL;" in sql
    assert sql.count("UPDATE reporting_queries") == len(_categorised_slugs())


def test_every_categorised_slug_is_a_real_report_in_exactly_one_category():
    categorised = _categorised_slugs()
    all_slugs = [slug for slugs in categorised.values() for slug in slugs]

    assert len(categorised) >= 20
    assert len(all_slugs) == len(set(all_slugs))
    assert set(all_slugs) <= _system_slugs()
    assert all(len(category) <= 64 for category in categorised)


def test_catalogue_reports_are_categorised():
    categorised = {slug for slugs in _categorised_slugs().values() for slug in slugs}

    for slug in (
        "unbilled-tickets",
        "dashboard-open-tickets",
        "rmm-agents-by-company",
        "rmm-windows-updates-missing-by-device",
        "health-checks-latest-by-asset",
        "report-licenses",
        "stat-strip-report-huntress-edr",
        "webhook-events-by-name-and-response-code",
    ):
        assert slug in categorised, slug


def test_form_category_is_whitespace_normalised_and_length_checked():
    payload = reporting_handlers._parse_reporting_form(
        _Form({"name": "Report", "sql_query": "SELECT 1", "category": "  RMM   Scripts "})
    )
    assert payload["category"] == "RMM Scripts"
    assert reporting_handlers._parse_reporting_form(_Form({"category": "   "}))["category"] is None

    payload.update(slug="report", category="x" * 65)
    assert reporting_handlers._validate_reporting_input(payload) == "Category must be 64 characters or fewer."


def test_create_query_stores_the_category(monkeypatch):
    captured: dict[str, object] = {}

    async def fake_insert(sql, params):
        captured.update(sql=sql, params=params)
        return 5

    monkeypatch.setattr(reporting_repo.db, "execute_returning_lastrowid", fake_insert)
    asyncio.run(
        reporting_repo.create_query(
            slug="r", name="R", description=None, sql_query="SELECT 1", created_by=1, category="Billing"
        )
    )

    assert "category" in captured["sql"]
    assert captured["params"] == ("r", "R", None, "Billing", "SELECT 1", 1)


def test_manage_reports_lists_by_category_then_name(monkeypatch):
    captured: dict[str, object] = {}
    records = [
        {"id": 1, "name": "Zeta", "category": "Billing", "updated_at": None},
        {"id": 2, "name": "Custom", "category": None, "updated_at": None},
        {"id": 3, "name": "alpha", "category": "Billing", "updated_at": None},
        {"id": 4, "name": "Agents", "category": "RMM", "updated_at": None},
    ]

    async def fake_require(_request):
        return {"id": 1, "is_super_admin": True}, None

    async def fake_render(template, _request, _user, *, extra):
        captured.update(template=template, extra=extra)
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

    reports = captured["extra"]["reports"]
    assert [report["id"] for report in reports] == [3, 1, 4, 2]
    assert reports[-1]["category"] == reporting_handlers.UNCATEGORISED
    assert captured["extra"]["report_columns"] == reporting_handlers.REPORT_COLUMNS


def test_manage_reports_table_is_sortable_filterable_with_a_row_actions_menu():
    env = Environment(loader=FileSystemLoader(TEMPLATES_DIR), autoescape=select_autoescape(("html",)))
    template = env.get_template("admin/reporting.html")
    context = template.new_context(
        {
            "reports": [
                {
                    "id": 9,
                    "name": "Unbilled tickets",
                    "slug": "unbilled-tickets",
                    "category": "Billing",
                    "description": "Time not yet billed",
                    "is_system": True,
                    "updated_at_iso": "2026-10-26T00:00:00+00:00",
                }
            ],
            "report_columns": reporting_handlers.REPORT_COLUMNS,
            "csrf_token": "tok",
        }
    )
    content = "".join(template.blocks["content"](context))
    header = "".join(template.blocks["header_title"](context))

    assert 'id="reporting-reports"' in content and 'data-table-id="reporting-reports"' in content
    assert re.search(r'<th[^>]*data-column-key="category"[^>]*data-sort="string"[^>]*data-filter-options="multiple"', content)
    assert re.search(r'<th[^>]*data-column-key="updated"[^>]*data-sort="date"', content)
    assert '<td data-column-key="category" data-label="Category">Billing</td>' in content
    assert 'data-table-filter="reporting-reports"' in header
    assert 'data-table-columns="reporting-reports"' in header

    assert 'data-header-menu' in content and 'aria-controls="report-actions-9"' in content
    for href in ("/reporting?report=9", "/admin/reporting/9/edit", "/admin/reporting/9/clone"):
        assert re.search(r'<a class="header-menu__item" href="' + re.escape(href) + '"', content), href
    assert re.search(
        r'<button type="submit" class="header-menu__item header-menu__item--danger" form="report-delete-9"', content
    )
    assert 'class="button button--ghost">Edit' not in content


class _Form(dict):
    def getlist(self, key):
        value = self.get(key)
        return [] if value is None else [value]
