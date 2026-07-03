"""Test dynamic saved-report template variables."""

import pytest

from app.services import dynamic_variables, reporting, value_templates


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def test_extract_report_requests():
    tokens = [
        "report.asset-billing.count",
        "report.security.stack.list",
        "report.invalid.total",
        "template.asset-billing.count",
    ]

    assert dynamic_variables._extract_report_requests(tokens) == {
        "report.asset-billing.count": ("asset-billing", "count"),
        "report.security.stack.list": ("security.stack", "list"),
    }


def test_substitute_query_context_uses_safe_numeric_company_id():
    sql = "SELECT * FROM assets WHERE company_id = {{current.company}}"

    assert reporting.substitute_query_context(sql, company_id=42) == (
        "SELECT * FROM assets WHERE company_id = 42"
    )
    assert reporting.substitute_query_context(sql, company_id=None) == (
        "SELECT * FROM assets WHERE company_id = NULL"
    )


@pytest.mark.anyio
async def test_report_count_and_list_variables(monkeypatch):
    async def fake_get_query_by_slug(slug):
        assert slug == "asset-billing"
        return {
            "slug": slug,
            "sql_query": "SELECT name FROM assets WHERE company_id = {{current.company}}",
        }

    async def fake_count_query_rows(sql_query, *, company_id=None):
        assert (
            sql_query
            == "SELECT name FROM assets WHERE company_id = {{current.company}}"
        )
        assert company_id == 42
        return 2

    async def fake_run_query_with_context(sql_query, *, company_id=None):
        assert (
            sql_query
            == "SELECT name FROM assets WHERE company_id = {{current.company}}"
        )
        assert company_id == 42
        return {
            "columns": ["name"],
            "rows": [{"name": "Server-01"}, {"name": "Laptop-02"}],
        }

    monkeypatch.setattr(
        dynamic_variables.reporting_repo, "get_query_by_slug", fake_get_query_by_slug
    )
    monkeypatch.setattr(
        dynamic_variables.reporting_service, "count_query_rows", fake_count_query_rows
    )
    monkeypatch.setattr(
        dynamic_variables.reporting_service,
        "run_query_with_context",
        fake_run_query_with_context,
    )

    result = await dynamic_variables.build_dynamic_token_map(
        ["report.asset-billing.count", "report.asset-billing.list"],
        {"ticket": {"company_id": 42}},
    )

    assert result["report.asset-billing.count"] == "2"
    assert result["report.asset-billing.list"] == "name\r\nServer-01\r\nLaptop-02"


@pytest.mark.anyio
async def test_report_variables_render_in_templates(monkeypatch):
    async def fake_get_query_by_slug(slug):
        return {"slug": slug, "sql_query": "SELECT name FROM assets"}

    async def fake_count_query_rows(sql_query, *, company_id=None):
        return 3

    monkeypatch.setattr(
        dynamic_variables.reporting_repo, "get_query_by_slug", fake_get_query_by_slug
    )
    monkeypatch.setattr(
        dynamic_variables.reporting_service, "count_query_rows", fake_count_query_rows
    )

    rendered = await value_templates.render_string_async(
        "Billable assets: {{ report.billable-assets.count }}",
        {"company_id": 7},
    )

    assert rendered == "Billable assets: 3"
