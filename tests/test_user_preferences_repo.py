"""Tests for the user_preferences repository (key validation, JSON
serialisation, size limits) and basic SQLite round-trip."""
from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from app.core.config import Settings
from app.core.database import Database, db as global_db
from app.repositories import user_preferences as repo


@pytest.fixture
def anyio_backend():
    return "asyncio"


def test_validate_key_accepts_typical_keys():
    for key in ("tables:tickets-history:columns", "ui:theme", "x", "a-b.c_d:1"):
        assert repo.validate_key(key) == key


def test_validate_key_rejects_bad_input():
    for bad in (None, "", " ", "/etc/passwd", "a b", "a" * 200, "@nope"):
        with pytest.raises(repo.InvalidPreferenceKey):
            repo.validate_key(bad)  # type: ignore[arg-type]


def test_serialise_value_rejects_non_jsonable():
    with pytest.raises(repo.InvalidPreferenceValue):
        repo._serialise_value({"bad": object()})  # type: ignore[arg-type]


def test_serialise_value_rejects_oversized():
    with pytest.raises(repo.InvalidPreferenceValue):
        repo._serialise_value({"big": "x" * (repo.MAX_VALUE_BYTES + 1)})


def test_deserialise_handles_str_dict_and_invalid():
    assert repo._deserialise_value(None) is None
    assert repo._deserialise_value({"a": 1}) == {"a": 1}
    assert repo._deserialise_value('{"a":1}') == {"a": 1}
    assert repo._deserialise_value("not json") is None
    assert repo._deserialise_value(b'{"a":1}') == {"a": 1}


@pytest.mark.anyio
async def test_set_get_delete_preference_sqlite_roundtrip(monkeypatch):
    """Round-trip through SQLite to exercise the upsert branch."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "prefs.db"
        test_settings = Settings(
            SESSION_SECRET="test-secret",
            TOTP_ENCRYPTION_KEY="A" * 64,
            DB_HOST=None, DB_USER=None, DB_PASSWORD=None, DB_NAME=None,
        )
        test_db = Database()
        test_db._settings = test_settings
        test_db._use_sqlite = True
        test_db._get_sqlite_path = lambda: db_path
        await test_db.connect()
        try:
            await test_db.execute(
                """
                CREATE TABLE user_preferences (
                  user_id INTEGER NOT NULL,
                  preference_key TEXT NOT NULL,
                  preference_value TEXT NOT NULL,
                  is_modified INTEGER NOT NULL DEFAULT 1,
                  created_at TEXT DEFAULT (datetime('now')),
                  updated_at TEXT DEFAULT (datetime('now')),
                  PRIMARY KEY (user_id, preference_key)
                )
                """
            )
            # Re-bind the module-level singleton so the repo writes to our test db.
            monkeypatch.setattr(repo, "db", test_db)

            assert await repo.get_preference(7, "tables:t:columns") is None

            await repo.set_preference(7, "tables:t:columns", {"hidden": ["a", "b"]})
            assert await repo.get_preference(7, "tables:t:columns") == {"hidden": ["a", "b"]}

            # Upsert overwrites.
            await repo.set_preference(7, "tables:t:columns", {"hidden": []})
            assert await repo.get_preference(7, "tables:t:columns") == {"hidden": []}

            await repo.delete_preference(7, "tables:t:columns")
            assert await repo.get_preference(7, "tables:t:columns") is None

            # The modified flag is stored and returned with the record.
            assert await repo.get_preference_record(7, "tables:t:columns") is None
            await repo.set_preference(7, "tables:t:columns", {"hidden": ["a"]})
            assert await repo.get_preference_record(7, "tables:t:columns") == {
                "value": {"hidden": ["a"]},
                "is_modified": True,
            }
            # Resetting to the default clears the flag.
            await repo.set_preference(7, "tables:t:columns", {"hidden": []}, is_modified=False)
            assert await repo.get_preference_record(7, "tables:t:columns") == {
                "value": {"hidden": []},
                "is_modified": False,
            }
        finally:
            await test_db.disconnect()


@pytest.mark.anyio
async def test_seed_preference_never_overwrites_existing(monkeypatch):
    """Seeding transfers browser settings only when nothing is stored yet."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "prefs.db"
        test_settings = Settings(
            SESSION_SECRET="test-secret",
            TOTP_ENCRYPTION_KEY="A" * 64,
            DB_HOST=None, DB_USER=None, DB_PASSWORD=None, DB_NAME=None,
        )
        test_db = Database()
        test_db._settings = test_settings
        test_db._use_sqlite = True
        test_db._get_sqlite_path = lambda: db_path
        await test_db.connect()
        try:
            await test_db.execute(
                """
                CREATE TABLE user_preferences (
                  user_id INTEGER NOT NULL,
                  preference_key TEXT NOT NULL,
                  preference_value TEXT NOT NULL,
                  is_modified INTEGER NOT NULL DEFAULT 1,
                  created_at TEXT DEFAULT (datetime('now')),
                  updated_at TEXT DEFAULT (datetime('now')),
                  PRIMARY KEY (user_id, preference_key)
                )
                """
            )
            monkeypatch.setattr(repo, "db", test_db)

            # A new user gets the default layout recorded as unmodified.
            seeded = await repo.seed_preference(
                3, "tables:t:columns", {"hidden": []}, is_modified=False
            )
            assert seeded == {"value": {"hidden": []}, "is_modified": False}

            # A second seed (e.g. another device's browser) does not replace it.
            again = await repo.seed_preference(
                3, "tables:t:columns", {"hidden": ["x"]}, is_modified=True
            )
            assert again == {"value": {"hidden": []}, "is_modified": False}

            # Once the user changes the layout the flag switches and sticks
            # through later seeds.
            await repo.set_preference(3, "tables:t:columns", {"hidden": ["y"]})
            after = await repo.seed_preference(
                3, "tables:t:columns", {"hidden": []}, is_modified=False
            )
            assert after == {"value": {"hidden": ["y"]}, "is_modified": True}

            # A transferred browser layout keeps its modified flag.
            migrated = await repo.seed_preference(
                4, "tables:t:columns", {"hidden": ["z"]}, is_modified=True
            )
            assert migrated == {"value": {"hidden": ["z"]}, "is_modified": True}
        finally:
            await test_db.disconnect()
