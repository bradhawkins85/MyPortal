"""Gitea's parallel proxy identity requests must not flood the query pool."""

import asyncio
from types import SimpleNamespace

import aiosqlite
import pytest

from app.repositories import rmm as rmm_repo
from app.services import gitea, gitea_sign_in


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def identity_backend(monkeypatch):
    calls = {"read": 0, "save": 0, "grant": 0, "authorization": 0}
    rows = {}
    level = ["write"]
    now = [0.0]

    async def access_level(user):
        calls["authorization"] += 1
        return level[0]

    async def get_account(user_id):
        calls["read"] += 1
        await asyncio.sleep(0)
        return rows.get(user_id)

    async def save_account(user_id, login, permission):
        calls["save"] += 1
        rows[user_id] = {"gitea_login": login, "permission": permission}

    async def load_settings():
        return object()

    async def sign_in(*args, **kwargs):
        calls["grant"] += 1
        await asyncio.sleep(0)

    async def noop(*args, **kwargs):
        pass

    monkeypatch.setattr(gitea_sign_in, "_granted", {})
    monkeypatch.setattr(gitea_sign_in, "access_level", access_level)
    monkeypatch.setattr(gitea_sign_in, "time", SimpleNamespace(monotonic=lambda: now[0]))
    monkeypatch.setattr(rmm_repo, "get_gitea_account", get_account)
    monkeypatch.setattr(rmm_repo, "save_gitea_account", save_account)
    monkeypatch.setattr(gitea, "load_settings", load_settings)
    monkeypatch.setattr(gitea, "sign_in_as", sign_in)
    monkeypatch.setattr(gitea, "set_collaborator", noop)
    monkeypatch.setattr(gitea_sign_in, "_public_access", noop)
    return calls, rows, level, now


@pytest.mark.anyio
async def test_parallel_identity_requests_share_account_lookup(identity_backend):
    calls, rows, level, now = identity_backend
    user = {"id": 9, "email": "tech@example.com"}
    identities = await asyncio.gather(*(gitea_sign_in.identity(user) for _ in range(30)))
    assert all(identity.login == "tech-9" for identity in identities)
    assert calls == {"read": 2, "save": 1, "grant": 1, "authorization": 30}

    # Metadata stays current without changing the stable, stored account name.
    identity = await gitea_sign_in.identity({**user, "email": "new@example.com", "first_name": "Zoë"})
    assert (identity.login, identity.email, identity.full_name) == ("tech-9", "new@example.com", "Zoe")
    assert calls["read"] == 2

    # Refresh external grants without rewriting an unchanged database record.
    now[0] += gitea_sign_in.GRANT_REFRESH_SECONDS
    await gitea_sign_in.identity(user)
    assert (calls["read"], calls["save"], calls["grant"]) == (3, 1, 2)


@pytest.mark.anyio
async def test_first_identity_uses_the_stable_login_created_by_another_worker(identity_backend, monkeypatch):
    calls, rows, level, now = identity_backend
    provisioned, removed = [], []

    async def concurrent_save(user_id, login, permission):
        # A different worker won the first INSERT using an older email snapshot.
        calls["save"] += 1
        rows[user_id] = {"gitea_login": "original-9", "permission": permission}

    async def sign_in(settings, login, **kwargs):
        provisioned.append(login)

    async def remove(settings, login):
        removed.append(login)

    monkeypatch.setattr(rmm_repo, "save_gitea_account", concurrent_save)
    monkeypatch.setattr(gitea, "sign_in_as", sign_in)
    monkeypatch.setattr(gitea, "remove_collaborator", remove)
    user = {"id": 9, "email": "renamed@example.com"}
    assert (await gitea_sign_in.identity(user)).login == "original-9"
    assert (await gitea_sign_in.identity(user)).login == "original-9"
    assert provisioned == ["renamed-9", "original-9"]
    assert removed == ["renamed-9"]
    assert calls["read"] == 2
    assert calls["save"] == 1


@pytest.mark.anyio
async def test_cached_identity_still_follows_permission_changes(identity_backend):
    calls, rows, level, now = identity_backend
    user = {"id": 9, "email": "tech@example.com"}
    await gitea_sign_in.identity(user)
    level[0] = "read"
    assert (await gitea_sign_in.identity(user)).permission == "read"
    assert rows[9]["permission"] == "read"
    level[0] = "none"
    assert await gitea_sign_in.identity(user) is None
    assert 9 not in gitea_sign_in._granted
    level[0] = "read"
    await gitea_sign_in.identity(user)
    assert calls["authorization"] == 4
    assert calls["grant"] == 3


@pytest.mark.anyio
async def test_identity_grant_cache_has_a_size_limit(identity_backend, monkeypatch):
    monkeypatch.setattr(gitea_sign_in, "MAX_CACHED_GRANTS", 2)
    for user_id in (1, 2, 3):
        await gitea_sign_in.identity({"id": user_id, "email": "tech@example.com"})
    assert set(gitea_sign_in._granted) == {2, 3}
    assert not gitea_sign_in._locks


@pytest.mark.anyio
async def test_save_account_is_atomic_and_keeps_the_original_login(monkeypatch):
    async with aiosqlite.connect(":memory:") as conn:
        await conn.execute(
            "CREATE TABLE rmm_gitea_accounts (user_id INTEGER PRIMARY KEY, gitea_login TEXT UNIQUE, "
            "permission TEXT, created_at TEXT, updated_at TEXT)"
        )

        class Database:
            is_sqlite = staticmethod(lambda: True)

            async def execute(self, sql, params):
                await conn.execute(sql, params)

        monkeypatch.setattr(rmm_repo, "db", Database())
        await asyncio.gather(*(rmm_repo.save_gitea_account(9, "original-9", "read") for _ in range(20)))
        await rmm_repo.save_gitea_account(9, "renamed-9", "write")
        async with conn.execute("SELECT user_id, gitea_login, permission FROM rmm_gitea_accounts") as cursor:
            assert await cursor.fetchall() == [(9, "original-9", "write")]


@pytest.mark.anyio
async def test_mysql_save_account_uses_one_write_without_a_lookup(monkeypatch):
    calls = []

    class Database:
        is_sqlite = staticmethod(lambda: False)

        async def execute(self, sql, params):
            calls.append((sql, params))

    monkeypatch.setattr(rmm_repo, "db", Database())
    await rmm_repo.save_gitea_account(9, "tech-9", "write")
    assert len(calls) == 1
    sql, params = calls[0]
    assert "ON DUPLICATE KEY UPDATE" in sql
    assert sql.count("%s") == len(params) == 5
    assert params[:3] == (9, "tech-9", "write")
