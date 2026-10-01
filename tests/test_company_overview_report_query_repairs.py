"""Regression coverage for Company Overview queries that referenced missing columns."""

import re
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from app.services import reports

MIGRATIONS = Path(__file__).parent.parent / "migrations"
REPAIR = MIGRATIONS / "449_fix_company_overview_report_queries.sql"
NULLABLE_COMPANY = MIGRATIONS / "448_users_company_id_nullable.sql"


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _new_queries() -> dict[str, str]:
    sql = REPAIR.read_text(encoding="utf-8")
    pattern = re.compile(r"SET sql_query = '((?:[^']|'')*)' WHERE slug = '([^']+)'")
    return {slug: query.replace("''", "'") for query, slug in pattern.findall(sql)}


def test_repair_migration_updates_each_broken_company_overview_query():
    assert set(_new_queries()) == {
        "report-active-staff",
        "report-active-user-accounts",
        "report-licenses",
        "report-subscriptions",
    }


def test_repaired_queries_drop_columns_missing_from_schema():
    queries = _new_queries()
    for slug in ("report-active-staff", "report-active-user-accounts"):
        assert "s.name" not in queries[slug]
        assert "s.position" not in queries[slug]
    assert "l.notes" not in queries["report-licenses"]
    assert "s.product_name" not in queries["report-subscriptions"]


def test_repair_only_touches_unmodified_seeded_queries():
    sql = REPAIR.read_text(encoding="utf-8")
    updates = [line for line in sql.splitlines() if line.startswith("UPDATE")]
    assert len(updates) == 4
    assert all(" AND sql_query = '" in line for line in updates)


def test_users_company_id_is_made_nullable():
    sql = NULLABLE_COMPANY.read_text(encoding="utf-8")
    assert "ALTER TABLE users MODIFY company_id INT NULL" in sql


@pytest.mark.anyio
async def test_active_staff_accounts_query_uses_existing_staff_columns(monkeypatch):
    fetch_all = AsyncMock(
        return_value=[{"first_name": "Alice", "last_name": "Anderson", "job_title": "IT Manager"}]
    )
    monkeypatch.setattr(reports.db, "fetch_all", fetch_all)

    accounts = await reports._list_active_staff_accounts(7)

    sql = fetch_all.await_args.args[0]
    select_list = sql.split("FROM", 1)[0]
    assert not re.search(r"\bname\b", select_list.replace("first_name", "").replace("last_name", ""))
    assert "position" not in select_list
    assert accounts[0]["name"] == "Alice Anderson"
    assert accounts[0]["job_title"] == "IT Manager"
