"""Tests for the per-user view preference API (table column layouts).

Covers the ``is_modified`` flag added in migration 588 and the ``seed`` mode
used to transfer a browser's legacy layout into the database.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.api.routes import users as users_routes

USER = {"id": 5, "email": "tech@example.com"}
KEY = "tables:tickets:visible-columns"


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_get_reports_missing_record(monkeypatch):
    monkeypatch.setattr(
        users_routes.user_preferences_repo, "get_preference_record", AsyncMock(return_value=None)
    )
    result = await users_routes.get_my_preference(key=KEY, _=None, current_user=USER)
    assert result == {"key": KEY, "value": None, "is_modified": False, "exists": False}


@pytest.mark.anyio
async def test_get_returns_modified_flag(monkeypatch):
    monkeypatch.setattr(
        users_routes.user_preferences_repo,
        "get_preference_record",
        AsyncMock(return_value={"value": {"visible": ["subject"]}, "is_modified": True}),
    )
    result = await users_routes.get_my_preference(key=KEY, _=None, current_user=USER)
    assert result == {
        "key": KEY,
        "value": {"visible": ["subject"]},
        "is_modified": True,
        "exists": True,
    }


@pytest.mark.anyio
async def test_put_defaults_to_modified(monkeypatch):
    setter = AsyncMock(side_effect=lambda user_id, key, value, is_modified: value)
    monkeypatch.setattr(users_routes.user_preferences_repo, "set_preference", setter)
    result = await users_routes.update_my_preference(
        payload={"key": KEY, "value": {"visible": ["subject"]}}, _=None, current_user=USER
    )
    setter.assert_awaited_once_with(5, KEY, {"visible": ["subject"]}, is_modified=True)
    assert result["is_modified"] is True


@pytest.mark.anyio
async def test_put_reset_clears_modified(monkeypatch):
    setter = AsyncMock(side_effect=lambda user_id, key, value, is_modified: value)
    monkeypatch.setattr(users_routes.user_preferences_repo, "set_preference", setter)
    result = await users_routes.update_my_preference(
        payload={"key": KEY, "value": {"visible": []}, "modified": False}, _=None, current_user=USER
    )
    setter.assert_awaited_once_with(5, KEY, {"visible": []}, is_modified=False)
    assert result["is_modified"] is False


@pytest.mark.anyio
async def test_put_seed_returns_existing_record(monkeypatch):
    seeder = AsyncMock(return_value={"value": {"visible": ["a"]}, "is_modified": True})
    setter = AsyncMock()
    monkeypatch.setattr(users_routes.user_preferences_repo, "seed_preference", seeder)
    monkeypatch.setattr(users_routes.user_preferences_repo, "set_preference", setter)
    result = await users_routes.update_my_preference(
        payload={"key": KEY, "value": {"visible": ["b"]}, "modified": False, "seed": True},
        _=None,
        current_user=USER,
    )
    seeder.assert_awaited_once_with(5, KEY, {"visible": ["b"]}, is_modified=False)
    setter.assert_not_awaited()
    assert result == {"key": KEY, "value": {"visible": ["a"]}, "is_modified": True}


@pytest.mark.anyio
@pytest.mark.parametrize(
    "payload",
    [
        {"key": KEY, "value": {}, "modified": "yes"},
        {"key": KEY, "value": {}, "seed": 1},
        {"key": "bad key", "value": {}},
    ],
)
async def test_put_rejects_invalid_payload(payload):
    with pytest.raises(HTTPException) as exc:
        await users_routes.update_my_preference(payload=payload, _=None, current_user=USER)
    assert exc.value.status_code == 400


def test_migration_adds_is_modified_flag():
    path = Path(__file__).resolve().parents[1] / "migrations" / "588_user_preferences_is_modified.sql"
    sql = path.read_text()
    assert "-- phase: expand" in sql
    assert "ALTER TABLE user_preferences ADD COLUMN IF NOT EXISTS is_modified" in sql
    # Existing rows were saved by explicit user changes, so they default to modified.
    assert "DEFAULT 1" in sql


def test_view_preferences_script_loaded_before_column_scripts():
    base = (Path(__file__).resolve().parents[1] / "app" / "templates" / "base.html").read_text()
    assert base.index("js/view_preferences.js") < base.index("js/table_columns.js")
    assert "data-user-id=" in base
