"""Assets & Network > Backups: register validation, access rules and wiring."""
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from fastapi.responses import RedirectResponse

from app.core.core_components import CORE_COMPONENTS_BY_SLUG
from app.features.backups import register as routes
from app.repositories import backup_register as repo
from app.repositories.sidebar_preferences import build_default_sidebar_preferences
from app.security.menu_permissions import MENU_PERMISSION_MAP, normalize_menu_permissions

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def anyio_backend():
    return "asyncio"


def test_clean_entry_parses_destinations_and_key_links():
    cleaned = repo.clean_entry({
        "name": " Nightly file server ",
        "backup_app_id": "3",
        "frequency": "Daily",
        "destinations": "NAS01\n\n  Wasabi bucket  \nNAS01",
        "key_links": "Recovery key | https://vault.example.com/k/1\nhttps://docs.example.com/keys",
        "credential_ids": ["4", "x", "4", "9"],
    })
    assert cleaned["name"] == "Nightly file server"
    assert cleaned["backup_app_id"] == 3
    assert cleaned["destinations"] == ["NAS01", "Wasabi bucket"]
    assert cleaned["key_links"] == [
        {"label": "Recovery key", "url": "https://vault.example.com/k/1"},
        {"label": "https://docs.example.com/keys", "url": "https://docs.example.com/keys"},
    ]
    assert cleaned["credential_ids"] == [4, 9]
    assert cleaned["source"] is None and cleaned["notes"] is None


@pytest.mark.parametrize("values", [
    {"name": ""},
    {"name": "x" * 201},
    {"name": "x", "key_links": "Key | javascript:alert(1)"},
    {"name": "x", "key_links": "ftp://example.com/key"},
    {"name": "x", "destinations": "\n".join(f"d{i}" for i in range(21))},
    {"name": "x", "new_backup_app": "a" * 121},
])
def test_invalid_entries_are_rejected(values):
    with pytest.raises(ValueError):
        repo.clean_entry(values)


def test_tracked_job_details_do_not_need_a_name():
    assert repo.clean_entry({}, require_name=False)["name"] is None


def test_stored_rows_drop_unsafe_links():
    entry = repo._normalise({
        "id": 1, "company_id": 2, "backup_job_id": None, "backup_app_id": None, "name": "x",
        "destinations": '["A"]',
        "encryption_keys": '{"credential_ids": [5], "links": [{"label": "bad", "url": "javascript:x"},'
                           ' {"label": "ok", "url": "https://k.example"}]}',
    })
    assert entry["destinations"] == ["A"]
    assert entry["credential_ids"] == [5]
    assert entry["key_links"] == [{"label": "ok", "url": "https://k.example"}]


@pytest.mark.anyio
async def test_typed_app_is_reused_or_created(monkeypatch):
    monkeypatch.setattr(repo.db, "fetch_one", AsyncMock(return_value={"id": 7}))
    insert = AsyncMock(return_value=8)
    monkeypatch.setattr(repo.db, "execute_returning_lastrowid", insert)
    assert await repo.resolve_app({"new_backup_app": "Veeam"}) == 7
    insert.assert_not_awaited()

    monkeypatch.setattr(repo.db, "fetch_one", AsyncMock(return_value=None))
    assert await repo.resolve_app({"new_backup_app": "Restic"}) == 8
    with pytest.raises(ValueError):
        await repo.resolve_app({"new_backup_app": None, "backup_app_id": 99})


def _request():
    return SimpleNamespace(query_params={}, url=SimpleNamespace(path="/backups"), state=SimpleNamespace())


def _patch_context(monkeypatch, *, can):
    main = routes._main()
    monkeypatch.setattr(main, "_require_authenticated_user",
                        AsyncMock(return_value=({"id": 1, "company_id": 5}, None)))
    monkeypatch.setattr(main, "_get_effective_company_membership", AsyncMock(return_value={}))
    monkeypatch.setattr(main, "_membership_menu_can", can)
    from app.repositories import companies as company_repo
    monkeypatch.setattr(company_repo, "get_company_by_id", AsyncMock(return_value={"id": 5}))


@pytest.mark.anyio
async def test_users_without_access_are_redirected(monkeypatch):
    _patch_context(monkeypatch, can=lambda *args, **kwargs: False)
    assert isinstance(await routes._context(_request()), RedirectResponse)


@pytest.mark.anyio
async def test_read_only_roles_cannot_write(monkeypatch):
    _patch_context(monkeypatch, can=lambda *args, write=False, **kwargs: not write)
    user, _membership, _company, company_id, can_edit = await routes._context(_request())
    assert company_id == 5 and can_edit is False
    with pytest.raises(HTTPException) as exc:
        await routes._context(_request(), write=True)
    assert exc.value.status_code == 403


@pytest.mark.anyio
async def test_tracked_jobs_from_other_companies_are_not_found(monkeypatch):
    from app.repositories import backup_jobs as backup_jobs_repo

    monkeypatch.setattr(backup_jobs_repo, "get_job", AsyncMock(return_value={"id": 3, "company_id": 9}))
    with pytest.raises(HTTPException) as exc:
        await routes._company_job(5, 3)
    assert exc.value.status_code == 404


@pytest.mark.anyio
async def test_tracked_job_tokens_never_reach_the_page(monkeypatch):
    from app.services import backup_jobs as backup_jobs_service

    monkeypatch.setattr(backup_jobs_service, "list_jobs_with_latest",
                        AsyncMock(return_value=[{"id": 1, "name": "Job", "token": "secret"}]))
    jobs = await routes._tracked_jobs(5)
    assert jobs == [{"id": 1, "name": "Job"}]


def test_permission_defaults_to_no_access():
    assert "menu.backups" in MENU_PERMISSION_MAP
    assert normalize_menu_permissions(None)["menu.backups"] == "none"


def test_menu_and_component_wiring():
    groups = {group["id"]: group for group in build_default_sidebar_preferences()["groups"]}
    assert "/backups" in groups["__group__:default-infrastructure"]["items"]
    assert CORE_COMPONENTS_BY_SLUG["backup_register"].parent_pack == "backups"
    base = (ROOT / "app/templates/base.html").read_text()
    assert 'href="/backups"' in base and "can_access_backups" in base
    migration = (ROOT / "migrations/450_backup_register.sql").read_text()
    assert "CREATE TABLE IF NOT EXISTS backup_register" in migration
    assert "CREATE TABLE IF NOT EXISTS backup_apps" in migration


@pytest.mark.anyio
async def test_tracked_details_follow_their_job(monkeypatch):
    fetch_all = AsyncMock(return_value=[])
    monkeypatch.setattr(repo.db, "fetch_all", fetch_all)
    await repo.list_entries(5, job_ids=[1, 2])
    sql, params = fetch_all.await_args.args
    assert "r.backup_job_id IS NULL" in sql and "r.backup_job_id IN (%s, %s)" in sql
    assert params == (5, 1, 2)
    await repo.list_entries(5)
    assert fetch_all.await_args.args[1] == (5,)
