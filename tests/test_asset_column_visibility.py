"""Admins can hide Assets list columns (and custom field values) from end users."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

import app.main as main_module
from app.features.assets import routes as assets_routes
from app.features.rmm import routes as rmm_routes
from app.features.tags import routes as tags_routes
from app.services import rmm_agent_deploy

ROOT = Path(__file__).resolve().parents[1]

DEFINITIONS = [
    {"id": 1, "name": "budget_code", "display_name": "Budget code", "field_type": "text"},
    {"id": 2, "name": "bitlocker_key", "display_name": "BitLocker key", "field_type": "text"},
]
ASSET = {"id": 10, "company_id": 11, "name": "PC-01", "serial_number": "SN-123", "status": "active"}


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _request():
    return SimpleNamespace(state=SimpleNamespace())


def _wire_assets_page(monkeypatch, *, technician: bool, hidden: set[str], rmm: bool = False):
    user = {"id": 7, "company_id": 11, "is_super_admin": False}
    membership = {"menu_permissions": {"menu.assets": "write", "menu.rmm_scripts": "read"}}
    captured: dict = {}

    async def fake_render(template, request, current_user, *, extra=None):
        captured.update(extra)
        return extra

    restricted = AsyncMock(return_value=set(hidden))
    monkeypatch.setattr(main_module, "_require_authenticated_user", AsyncMock(return_value=(user, None)))
    monkeypatch.setattr(main_module, "_get_effective_company_membership", AsyncMock(return_value=membership))
    monkeypatch.setattr(main_module, "_is_helpdesk_technician", AsyncMock(return_value=technician))
    monkeypatch.setattr(main_module, "_feature_pack_available", lambda slug: rmm)
    monkeypatch.setattr(main_module, "_render_template", fake_render)
    monkeypatch.setattr(assets_routes.company_repo, "get_company_by_id", AsyncMock(return_value={"id": 11}))
    monkeypatch.setattr(assets_routes.asset_repo, "list_company_assets", AsyncMock(return_value=[dict(ASSET)]))
    monkeypatch.setattr(assets_routes.asset_custom_fields_repo, "list_field_definitions",
                        AsyncMock(return_value=[dict(d) for d in DEFINITIONS]))
    monkeypatch.setattr(assets_routes.asset_custom_fields_repo, "get_all_asset_field_values",
                        AsyncMock(return_value={10: {1: "CC-42", 2: "secret-recovery-key"}}))
    monkeypatch.setattr(assets_routes.asset_column_restrictions_repo, "list_restricted_keys", restricted)
    monkeypatch.setattr(assets_routes.tray_repo, "list_active_devices_by_asset_ids", AsyncMock(return_value={}))
    monkeypatch.setattr(tags_routes, "can_edit_tags", AsyncMock(return_value=False))
    monkeypatch.setattr(rmm_routes, "company_agent_details", AsyncMock(return_value={}))
    monkeypatch.setattr(rmm_agent_deploy, "company_latest_attempts", AsyncMock(return_value={}))
    return captured, restricted


def _keys(captured):
    return [column["key"] for column in captured["columns"]]


def test_migration_creates_the_restrictions_table():
    sql = (ROOT / "migrations/555_asset_column_restrictions.sql").read_text()
    assert "-- phase: expand" in sql
    assert "CREATE TABLE IF NOT EXISTS asset_column_restrictions" in sql
    assert "column_key VARCHAR(64) NOT NULL PRIMARY KEY" in sql


@pytest.mark.anyio
async def test_end_users_do_not_get_hidden_columns_or_their_values(monkeypatch):
    captured, _ = _wire_assets_page(monkeypatch, technician=False, hidden={"serial_number", "cf_2"})

    await assets_routes.assets_page(_request())

    keys = _keys(captured)
    assert "serial_number" not in keys and "cf_2" not in keys
    assert "name" in keys and "cf_1" in keys
    record = captured["assets"][0]
    assert record["cf_1"] == "CC-42"
    assert "cf_2" not in record and "serial_number" not in record
    assert "secret-recovery-key" not in repr(captured["assets"])


@pytest.mark.anyio
async def test_name_column_can_never_be_hidden(monkeypatch):
    captured, _ = _wire_assets_page(monkeypatch, technician=False, hidden={"name"})

    await assets_routes.assets_page(_request())

    assert "name" in _keys(captured)
    assert captured["assets"][0]["name"] == "PC-01"


@pytest.mark.anyio
async def test_technicians_still_see_every_column(monkeypatch):
    captured, restricted = _wire_assets_page(monkeypatch, technician=True, hidden={"serial_number", "cf_2"})

    await assets_routes.assets_page(_request())

    keys = _keys(captured)
    assert {"serial_number", "cf_1", "cf_2"} <= set(keys)
    restricted.assert_not_awaited()


@pytest.mark.anyio
async def test_hidden_health_check_score_is_never_loaded(monkeypatch):
    from app.repositories import ai_health_checks

    scores = AsyncMock(return_value={10: {"id": 5, "health_score": 88, "overall_status": "healthy"}})
    monkeypatch.setattr(ai_health_checks, "latest_scores_for_company", scores)

    captured, _ = _wire_assets_page(monkeypatch, technician=False, hidden={"health_score"}, rmm=True)
    await assets_routes.assets_page(_request())
    assert "health_score" not in _keys(captured)
    assert "health_score" not in captured["assets"][0]
    scores.assert_not_awaited()

    captured, _ = _wire_assets_page(monkeypatch, technician=False, hidden=set(), rmm=True)
    await assets_routes.assets_page(_request())
    assert "health_score" in _keys(captured)
    assert captured["assets"][0]["health_score"] == 88


class _Form(dict):
    def getlist(self, key):
        return list(self.get(key, []))


@pytest.mark.anyio
async def test_settings_save_hides_listed_columns_left_unticked(monkeypatch):
    user = {"id": 1, "is_super_admin": True}
    monkeypatch.setattr(assets_routes, "_load_asset_context",
                        AsyncMock(return_value=(user, None, {"id": 11}, 11, None)))
    monkeypatch.setattr(assets_routes.asset_custom_fields_repo, "list_field_definitions",
                        AsyncMock(return_value=DEFINITIONS))
    monkeypatch.setattr(assets_routes.asset_column_restrictions_repo, "list_restricted_keys",
                        AsyncMock(return_value={"serial_number"}))
    replace = AsyncMock()
    monkeypatch.setattr(assets_routes.asset_column_restrictions_repo, "replace_restricted_keys", replace)
    audit = AsyncMock()
    monkeypatch.setattr(assets_routes.audit_service, "record", audit)
    monkeypatch.setattr(main_module, "flash_redirect", lambda url, message, level: url)
    form = _Form(
        known_columns=["serial_number", "health_score", "status", "cf_1", "cf_2", "cf_99", "name", "bogus"],
        visible_columns=["serial_number", "status", "cf_1"],
    )
    request = SimpleNamespace(form=AsyncMock(return_value=form))

    result = await assets_routes.save_asset_column_visibility(request)

    assert result == "/assets/settings#column-visibility"
    replace.assert_awaited_once_with(["cf_2", "health_score"])
    assert audit.await_args.kwargs["before"] == {"hidden_columns": ["serial_number"]}
    assert audit.await_args.kwargs["after"] == {"hidden_columns": ["cf_2", "health_score"]}


@pytest.mark.anyio
async def test_settings_save_requires_super_admin(monkeypatch):
    user = {"id": 2, "is_super_admin": False}
    monkeypatch.setattr(assets_routes, "_load_asset_context",
                        AsyncMock(return_value=(user, None, {"id": 11}, 11, None)))
    replace = AsyncMock()
    monkeypatch.setattr(assets_routes.asset_column_restrictions_repo, "replace_restricted_keys", replace)

    with pytest.raises(HTTPException) as excinfo:
        await assets_routes.save_asset_column_visibility(SimpleNamespace(form=AsyncMock()))

    assert excinfo.value.status_code == 403
    replace.assert_not_awaited()


@pytest.mark.anyio
async def test_saving_an_asset_leaves_hidden_custom_fields_untouched(monkeypatch):
    monkeypatch.setattr(assets_routes.asset_custom_fields_repo, "list_field_definitions",
                        AsyncMock(return_value=DEFINITIONS))
    setter = AsyncMock()
    monkeypatch.setattr(assets_routes.asset_custom_fields_repo, "set_asset_field_value", setter)

    await assets_routes._save_submitted_custom_fields(10, {"custom_1": "CC-43"}, {"cf_2"})

    setter.assert_awaited_once_with(10, 1, value_text="CC-43")


@pytest.mark.anyio
async def test_asset_forms_omit_hidden_custom_fields_for_end_users(monkeypatch):
    monkeypatch.setattr(main_module, "_is_helpdesk_technician", AsyncMock(return_value=False))
    monkeypatch.setattr(assets_routes.asset_custom_fields_repo, "list_field_definitions",
                        AsyncMock(return_value=DEFINITIONS))
    monkeypatch.setattr(assets_routes.asset_column_restrictions_repo, "list_restricted_keys",
                        AsyncMock(return_value={"cf_2"}))

    fields = await assets_routes._visible_custom_fields({"id": 7}, _request())

    assert [field["id"] for field in fields] == [1]
