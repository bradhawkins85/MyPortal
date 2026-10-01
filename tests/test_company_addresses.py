"""Regression tests for saved company delivery addresses."""

import sqlite3
from pathlib import Path

import pytest

from app.core.database import db
from app.repositories import company_addresses
from app.schemas.company_addresses import CompanyAddressInput


@pytest.mark.anyio
async def test_address_lookup_is_scoped_to_company(monkeypatch):
    captured = {}

    async def fetch_one(sql, params):
        captured["sql"] = sql
        captured["params"] = params
        return None

    monkeypatch.setattr(db, "fetch_one", fetch_one)
    assert await company_addresses.get_for_company(12, 34) is None
    assert "company_id = %s AND id = %s" in captured["sql"]
    assert captured["params"] == (12, 34)


def test_address_input_trims_customer_visible_values():
    value = CompanyAddressInput(label="  Warehouse  ", street="  1 Main St  ")
    assert value.label == "Warehouse"
    assert value.street == "1 Main St"


def test_company_address_migration_runs_on_sqlite():
    migration = Path("migrations/356_company_addresses.sql").read_text(encoding="utf-8")
    adapted = db._adapt_sql_for_sqlite(migration)
    connection = sqlite3.connect(":memory:")
    connection.execute("CREATE TABLE companies (id INTEGER PRIMARY KEY)")
    for statement in db._split_sql_statements(adapted):
        connection.execute(statement)
    connection.execute(
        "INSERT INTO company_addresses (company_id, label, street) VALUES (1, 'HQ', '1 Main St')"
    )
    row = connection.execute("SELECT label, street FROM company_addresses").fetchone()
    assert row == ("HQ", "1 Main St")
