from types import SimpleNamespace
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock

import aiosqlite
import pytest
import pytest_asyncio

from app.repositories import sso_provisioning as repo


@pytest_asyncio.fixture
async def database(tmp_path, monkeypatch):
    path = tmp_path / "provisioning.db"
    monkeypatch.setattr(repo, "db", SimpleNamespace(is_sqlite=lambda: True, _get_sqlite_path=lambda: path))
    async with aiosqlite.connect(path) as conn:
        conn.row_factory = aiosqlite.Row
        await conn.executescript("""
            CREATE TABLE companies (id INTEGER PRIMARY KEY);
            INSERT INTO companies VALUES (7);
            CREATE TABLE roles (id INTEGER PRIMARY KEY, name TEXT UNIQUE);
            INSERT INTO roles VALUES (1, 'Admin'), (2, 'Member');
            CREATE TABLE users (
                id INTEGER PRIMARY KEY, email TEXT UNIQUE, password_hash TEXT,
                first_name TEXT, last_name TEXT, company_id INTEGER REFERENCES companies(id),
                is_super_admin INTEGER, is_active INTEGER, email_verified_at TEXT
            );
            CREATE TABLE user_companies (
                user_id INTEGER REFERENCES users(id), company_id INTEGER REFERENCES companies(id),
                is_admin INTEGER, UNIQUE(user_id, company_id)
            );
            CREATE TABLE company_memberships (
                company_id INTEGER REFERENCES companies(id), user_id INTEGER REFERENCES users(id),
                role_id INTEGER REFERENCES roles(id), status TEXT, invited_at TEXT, joined_at TEXT,
                UNIQUE(company_id, user_id)
            );
            CREATE TABLE user_sso_identities (
                id INTEGER PRIMARY KEY, user_id INTEGER REFERENCES users(id), provider TEXT,
                tenant_id TEXT, object_id TEXT, upn TEXT, email TEXT, linked_via TEXT,
                UNIQUE(provider, tenant_id, object_id), UNIQUE(user_id, provider)
            );
        """)
        yield conn


async def provision(**overrides):
    data = dict(email="alice@contoso.com", password_hash="random-password-hash",
                first_name="Alice", last_name="Smith", company_id=7,
                tenant_id="TENANT-A", object_id="OID-1", upn="alice@contoso.com",
                identity_email="alice@contoso.com")
    return await repo.create_user_with_identity(**(data | overrides))


@pytest.mark.asyncio
async def test_provisions_account_membership_and_identity_together(database):
    user, identity = await provision()
    assert user["is_super_admin"] == 0 and user["is_active"] == 1
    assert user["email_verified_at"] and user["company_id"] == 7
    assert identity["user_id"] == user["id"]
    assert identity["tenant_id"] == "tenant-a" and identity["object_id"] == "oid-1"
    assert identity["linked_via"] == "auto"
    cursor = await database.execute("SELECT * FROM company_memberships")
    membership = await cursor.fetchone()
    assert membership["role_id"] == 2 and membership["status"] == "active"
    cursor = await database.execute("SELECT * FROM user_companies")
    assert (await cursor.fetchone())["is_admin"] == 0


@pytest.mark.asyncio
async def test_identity_conflict_rolls_back_the_whole_new_account(database):
    await provision()
    with pytest.raises(aiosqlite.IntegrityError):
        await provision(email="another@contoso.com")
    for table in ("users", "user_companies", "company_memberships", "user_sso_identities"):
        # Fixed test schema names only; application values always use parameters.
        cursor = await database.execute("SELECT COUNT(*) FROM " + table)
        assert (await cursor.fetchone())[0] == 1


@pytest.mark.asyncio
async def test_missing_member_role_never_falls_back_to_admin(database):
    await database.execute("DELETE FROM roles WHERE name = 'Member'")
    await database.commit()
    with pytest.raises(RuntimeError, match="Member role"):
        await provision()
    cursor = await database.execute("SELECT COUNT(*) FROM users")
    assert (await cursor.fetchone())[0] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("fail_identity", [False, True])
async def test_mysql_transaction_commits_or_rolls_back_as_a_unit(monkeypatch, fail_identity):
    import sqlite3

    class Cursor:
        lastrowid = 42
        sql = ""

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        async def execute(self, sql, params):
            self.sql = sql
            assert "%s" in sql
            assert "alice@contoso.com" not in sql
            if fail_identity and sql.startswith("INSERT INTO user_sso_identities"):
                raise sqlite3.IntegrityError("identity conflict")

        async def fetchone(self):
            return {"id": 2} if "FROM roles" in self.sql else {"id": 42}

    connection = SimpleNamespace(
        begin=AsyncMock(), commit=AsyncMock(), rollback=AsyncMock(),
        cursor=lambda *args: Cursor(),
    )

    @asynccontextmanager
    async def acquire():
        yield connection

    monkeypatch.setattr(repo, "db", SimpleNamespace(is_sqlite=lambda: False, acquire=acquire))
    if fail_identity:
        with pytest.raises(sqlite3.IntegrityError):
            await provision()
        connection.rollback.assert_awaited_once()
        connection.commit.assert_not_awaited()
    else:
        await provision()
        connection.commit.assert_awaited_once()
        connection.rollback.assert_not_awaited()
    connection.begin.assert_awaited_once()
