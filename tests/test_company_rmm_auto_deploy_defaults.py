from pathlib import Path

import pytest

from app.core.database import Database
from app.repositories import companies
from app.services import rmm_agent_deploy
from tests.test_rmm_scripts import _SqliteDb


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = "516_enable_company_rmm_auto_deploy.sql"


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
async def company_db(tmp_path, monkeypatch):
    database = Database()
    database._use_sqlite = True
    monkeypatch.setattr(database, "_get_sqlite_path", lambda: tmp_path / "companies.db")
    migration_dir = tmp_path / "migrations"
    migration_dir.mkdir()
    (migration_dir / MIGRATION).write_text((ROOT / "migrations" / MIGRATION).read_text())
    monkeypatch.setattr(database, "_get_migrations_dir", lambda: migration_dir)
    await database.connect()
    monkeypatch.setattr(companies, "db", _SqliteDb(database._sqlite_conn))
    await database.execute(
        "CREATE TABLE companies (id INTEGER PRIMARY KEY, name TEXT, archived INT DEFAULT 0)"
    )
    await database.execute(
        database._adapt_sql_for_sqlite(
            (ROOT / "migrations" / "473_rmm_agent_auto_deploy.sql").read_text()
        )
    )
    await database.execute(
        "CREATE TABLE company_email_domains (company_id INT, domain TEXT)"
    )
    try:
        yield database
    finally:
        await database.disconnect()


@pytest.mark.anyio
async def test_existing_companies_enabled_once_and_later_opt_out_survives(company_db):
    await company_db.execute(
        "INSERT INTO companies (id, name, archived, rmm_agent_auto_deploy) VALUES "
        "(1, 'Disabled', 0, 0), (2, 'Enabled', 0, 1), (3, 'Archived', 1, 0)"
    )
    await company_db.run_migrations()
    rows = await company_db.fetch_all("SELECT rmm_agent_auto_deploy FROM companies ORDER BY id")
    assert [row["rmm_agent_auto_deploy"] for row in rows] == [1, 1, 1]
    assert await rmm_agent_deploy._auto_enabled(1) is True

    await companies.update_company(1, rmm_agent_auto_deploy=0)
    await company_db.run_migrations()
    assert await rmm_agent_deploy._auto_enabled(1) is False


@pytest.mark.anyio
async def test_new_company_defaults_to_auto_deploy_and_can_opt_out(company_db):
    await company_db.run_migrations()
    company = await companies.create_company(name="New company")
    assert company["rmm_agent_auto_deploy"] == 1
    assert await rmm_agent_deploy._auto_enabled(company["id"]) is True

    await companies.update_company(company["id"], rmm_agent_auto_deploy=0)
    await companies.update_company(company["id"], name="Renamed company")
    assert await rmm_agent_deploy._auto_enabled(company["id"]) is False


@pytest.mark.anyio
@pytest.mark.parametrize("enabled", [0, 1])
async def test_company_creation_preserves_explicit_preference(company_db, enabled):
    company = await companies.create_company(name="Explicit preference", rmm_agent_auto_deploy=enabled)
    assert company["rmm_agent_auto_deploy"] == enabled
