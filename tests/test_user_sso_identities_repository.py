from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.repositories import companies
from app.repositories import user_sso_identities as repo


class FakeDB:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple]] = []
        self.rows: list[dict] = []
        self.row: dict | None = None

    async def fetch_one(self, sql, params=()):
        self.calls.append((sql, tuple(params)))
        return self.row

    async def fetch_all(self, sql, params=()):
        self.calls.append((sql, tuple(params)))
        return self.rows

    async def execute(self, sql, params=()):
        self.calls.append((sql, tuple(params)))


@pytest.fixture
def fake_db(monkeypatch):
    fake = FakeDB()
    monkeypatch.setattr(repo, "db", fake)
    monkeypatch.setattr(companies, "db", fake)
    return fake


def _identity_kwargs(**overrides):
    kwargs = dict(
        user_id=7, provider="m365", tenant_id="TENANT-A", object_id="OID-1",
        upn="a@x.com", email="a@x.com", linked_via="auto", linked_by_user_id=None,
    )
    kwargs.update(overrides)
    return kwargs


def test_migration_defines_table_and_company_sso_mode():
    migration = next(Path("migrations").glob("*_user_sso_identities.sql"))
    sql = migration.read_text(encoding="utf-8")
    assert sql.startswith("-- phase: expand")
    assert "CREATE TABLE IF NOT EXISTS user_sso_identities" in sql
    assert "ADD COLUMN IF NOT EXISTS sso_mode VARCHAR(16) NOT NULL DEFAULT 'off'" in sql


@pytest.mark.asyncio
async def test_get_by_subject_lowercases_and_binds_params(fake_db):
    await repo.get_by_subject("m365", "TENANT-A", "OID-1")
    sql, params = fake_db.calls[-1]
    assert params == ("m365", "tenant-a", "oid-1")
    assert "%s" in sql and "TENANT-A" not in sql


@pytest.mark.asyncio
async def test_get_by_subject_returns_none_when_missing(fake_db):
    assert await repo.get_by_subject("m365", "t", "o") is None


@pytest.mark.asyncio
async def test_get_for_user_binds_user_and_provider(fake_db):
    fake_db.row = {"id": 1, "created_at": datetime(2026, 1, 1), "last_login_at": datetime(2026, 1, 2)}
    row = await repo.get_for_user(7)
    assert fake_db.calls[-1][1] == (7, "m365")
    assert row["created_at"].tzinfo is timezone.utc
    assert row["last_login_at"].tzinfo is timezone.utc


@pytest.mark.asyncio
async def test_create_inserts_lowercased_and_rereads_row(fake_db):
    fake_db.row = {"id": 3, "user_id": 7}
    result = await repo.create(**_identity_kwargs())
    insert_sql, insert_params = fake_db.calls[0]
    assert insert_sql.lstrip().startswith("INSERT INTO user_sso_identities")
    assert insert_params == (7, "m365", "tenant-a", "oid-1", "a@x.com", "a@x.com", "auto", None)
    assert fake_db.calls[-1][1] == ("m365", "tenant-a", "oid-1")
    assert result == {"id": 3, "user_id": 7}


@pytest.mark.asyncio
async def test_replace_for_user_deletes_then_inserts(fake_db):
    fake_db.row = {"id": 4}
    await repo.replace_for_user(**_identity_kwargs(linked_via="admin", linked_by_user_id=1))
    statements = [sql.lstrip().split()[0] for sql, _ in fake_db.calls]
    assert statements.index("DELETE") < statements.index("INSERT")
    delete_sql, delete_params = fake_db.calls[0]
    assert "DELETE FROM user_sso_identities" in delete_sql and delete_params == (7, "m365")
    insert_sql, insert_params = next(c for c in fake_db.calls if c[0].lstrip().startswith("INSERT"))
    assert "%s" in insert_sql and insert_params[-2:] == ("admin", 1)


@pytest.mark.asyncio
async def test_delete_for_user_returns_deleted_row(fake_db):
    fake_db.row = {"id": 9, "user_id": 7}
    deleted = await repo.delete_for_user(7)
    assert deleted == {"id": 9, "user_id": 7}
    assert fake_db.calls[0][1] == (7, "m365")
    assert fake_db.calls[-1][0].lstrip().startswith("DELETE")
    assert fake_db.calls[-1][1] == (7, "m365")


@pytest.mark.asyncio
async def test_delete_for_user_without_link_skips_delete(fake_db):
    assert await repo.delete_for_user(7) is None
    assert len(fake_db.calls) == 1


@pytest.mark.asyncio
async def test_record_login_updates_naive_utc_timestamp(fake_db):
    when = datetime(2026, 5, 1, 12, 0, tzinfo=timezone.utc)
    await repo.record_login(5, upn="u@x", email=None, when=when)
    sql, params = fake_db.calls[-1]
    assert sql.lstrip().startswith("UPDATE user_sso_identities")
    assert params == ("u@x", None, datetime(2026, 5, 1, 12, 0), 5)


@pytest.mark.asyncio
async def test_list_for_users_empty_skips_query(fake_db):
    assert await repo.list_for_users([]) == {}
    assert fake_db.calls == []


@pytest.mark.asyncio
async def test_list_for_users_builds_placeholder_list(fake_db):
    fake_db.rows = [{"user_id": 3, "upn": "a@x"}, {"user_id": 5, "upn": "b@x"}]
    result = await repo.list_for_users([3, 5])
    sql, params = fake_db.calls[-1]
    assert sql.count("%s") == 3 and params == ("m365", 3, 5)
    assert set(result) == {3, 5}


@pytest.mark.asyncio
async def test_count_for_company_binds_company_and_provider(fake_db):
    fake_db.row = {"count": 4}
    assert await repo.count_for_company(12) == 4
    sql, params = fake_db.calls[-1]
    assert sql.count("%s") == len(params)
    assert params == ("m365", 12, 12)
    assert "user_companies" in sql and "u.company_id" in sql


@pytest.mark.asyncio
async def test_count_for_company_defaults_to_zero(fake_db):
    assert await repo.count_for_company(12) == 0


@pytest.mark.asyncio
async def test_list_for_company_binds_params_and_normalises(fake_db):
    fake_db.rows = [
        {"user_id": 3, "email": "a@x.com", "upn": "a@x.com", "linked_via": "admin",
         "last_login_at": datetime(2026, 1, 1, 12, 0)},
    ]
    rows = await repo.list_for_company(12)
    sql, params = fake_db.calls[-1]
    assert sql.count("%s") == len(params)
    assert params == ("m365", 12, 12)
    assert "user_companies" in sql and "u.company_id" in sql
    assert rows[0]["last_login_at"].tzinfo is timezone.utc


def test_sso_mode_is_allowed_company_input():
    companies._validate_company_fields({"sso_mode": "optional"})


@pytest.mark.asyncio
async def test_list_sso_company_ids_for_tenant_filters_mode(fake_db):
    fake_db.rows = [{"company_id": 2}, {"company_id": 5}]
    result = await companies.list_sso_company_ids_for_tenant("Tenant-A")
    sql, params = fake_db.calls[-1]
    assert "sso_mode" in sql and params == ("tenant-a",)
    assert result == [2, 5]


@pytest.mark.asyncio
async def test_list_email_domains_for_companies_is_parameterised(fake_db):
    fake_db.rows = [{"domain": "Contoso.com "}, {"domain": "fabrikam.com"}, {"domain": None}]
    result = await companies.list_email_domains_for_companies([8, 7, 7])
    sql, params = fake_db.calls[-1]
    assert result == {"contoso.com", "fabrikam.com"}
    assert params == (7, 8) and sql.count("%s") == 2
    assert "company_email_domains" in sql and "IN (%s, %s)" in sql


@pytest.mark.asyncio
async def test_list_email_domains_for_companies_empty_skips_query(fake_db):
    assert await companies.list_email_domains_for_companies([]) == set()
    assert fake_db.calls == []


def _tenant_query_rows(sql: str, params: tuple) -> list[int]:
    import sqlite3

    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.executescript(
        """
        CREATE TABLE companies (id INTEGER PRIMARY KEY, csp_tenant_id TEXT, sso_mode TEXT);
        CREATE TABLE company_m365_credentials (company_id INTEGER, tenant_id TEXT);
        INSERT INTO companies VALUES (1, 'Tenant-A', 'optional');
        INSERT INTO companies VALUES (2, NULL, 'required');
        INSERT INTO company_m365_credentials VALUES (2, 'tenant-a');
        INSERT INTO companies VALUES (3, '', 'optional');
        INSERT INTO company_m365_credentials VALUES (3, 'tenant-a');
        INSERT INTO companies VALUES (4, 'tenant-a', 'off');
        INSERT INTO companies VALUES (5, 'tenant-b', 'optional');
        INSERT INTO company_m365_credentials VALUES (5, 'tenant-a');
        INSERT INTO companies VALUES (6, NULL, 'optional');
        """
    )
    rows = connection.execute(sql.replace("%s", "?"), params).fetchall()
    connection.close()
    return [int(row["company_id"]) for row in rows]


@pytest.mark.asyncio
async def test_list_sso_company_ids_for_tenant_counts_csp_tenant_without_credentials(fake_db):
    await companies.list_sso_company_ids_for_tenant("TENANT-A")
    sql, params = fake_db.calls[-1]
    # 1: CSP tenant only (no credentials row); 2 and 3: credential tenant;
    # 4: mode off; 5: CSP tenant wins over the credential tenant; 6: no tenant.
    assert _tenant_query_rows(sql, params) == [1, 2, 3]
