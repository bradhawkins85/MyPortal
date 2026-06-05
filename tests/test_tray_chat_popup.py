"""Tests for tray chat popup token repository functions and popup session helpers.

Follows the same SQLite-fixture pattern as ``test_tray_app.py``.
"""
from __future__ import annotations

import asyncio
import json
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Fixtures (same pattern as test_tray_app.py)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def popup_event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="module")
def popup_db(popup_event_loop):
    """Initialise the global ``db`` singleton against a fresh SQLite file
    and create the tables required for chat popup token tests.
    """
    tmp = tempfile.TemporaryDirectory()
    sqlite_path = Path(tmp.name) / "popup-tests.db"

    from app.core.database import db

    original_use_sqlite = db._use_sqlite
    original_get_path = db._get_sqlite_path
    db._use_sqlite = True
    db._get_sqlite_path = lambda: sqlite_path  # type: ignore[assignment]

    popup_event_loop.run_until_complete(db.connect())

    sqlite_ddl = [
        """CREATE TABLE IF NOT EXISTS tray_devices (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               company_id INTEGER NULL,
               asset_id INTEGER NULL,
               device_uid TEXT NOT NULL UNIQUE,
               enrolment_token_id INTEGER NULL,
               auth_token_hash TEXT NOT NULL,
               auth_token_prefix TEXT NOT NULL,
               os TEXT NULL,
               os_version TEXT NULL,
               hostname TEXT NULL,
               serial_number TEXT NULL,
               agent_version TEXT NULL,
               console_user TEXT NULL,
               last_ip TEXT NULL,
               last_seen_utc TEXT NULL,
               status TEXT NOT NULL DEFAULT 'pending',
               created_at TEXT DEFAULT (datetime('now')),
               updated_at TEXT DEFAULT (datetime('now'))
           )""",
        """CREATE TABLE IF NOT EXISTS tray_chat_tokens (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               device_id INTEGER NOT NULL,
               token_hash TEXT NOT NULL UNIQUE,
               room_id INTEGER NULL,
               created_at TEXT DEFAULT (datetime('now')),
               expires_at TEXT NOT NULL,
               used_at TEXT NULL
           )""",
        """INSERT OR IGNORE INTO tray_devices
               (device_uid, auth_token_hash, auth_token_prefix, status, company_id)
               VALUES ('test-uid-popup', 'hash1', 'pre1', 'enrolled', 1)""",
    ]

    async def _bootstrap():
        for stmt in sqlite_ddl:
            await db.execute(stmt)

    popup_event_loop.run_until_complete(_bootstrap())

    yield db

    popup_event_loop.run_until_complete(db.disconnect())
    db._use_sqlite = original_use_sqlite
    db._get_sqlite_path = original_get_path  # type: ignore[assignment]
    tmp.cleanup()


@pytest.fixture()
def run(popup_event_loop):
    def _run(coro):
        return popup_event_loop.run_until_complete(coro)
    return _run


# ---------------------------------------------------------------------------
# Migration file presence
# ---------------------------------------------------------------------------


def test_chat_token_migration_file_exists():
    assert Path("migrations/252_tray_chat_tokens.sql").exists(), (
        "migrations/252_tray_chat_tokens.sql is missing"
    )


# ---------------------------------------------------------------------------
# Repository: create / lookup / mark-used lifecycle
# ---------------------------------------------------------------------------


def test_create_chat_token_and_retrieve(popup_db, run):
    from app.repositories import tray as repo
    from app.services import tray as svc

    raw_token = svc.generate_install_token()  # reuse generate helper for a random token
    token_hash = svc.hash_token(raw_token)
    expires_at = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(minutes=5)

    record = run(
        repo.create_chat_token(
            device_id=1,
            token_hash=token_hash,
            room_id=None,
            expires_at=expires_at,
        )
    )
    assert record["id"]
    assert record["token_hash"] == token_hash
    assert record["room_id"] is None
    assert record["used_at"] is None


def test_get_chat_token_by_hash_returns_row(popup_db, run):
    from app.repositories import tray as repo
    from app.services import tray as svc

    raw_token = svc.generate_install_token()
    token_hash = svc.hash_token(raw_token)
    expires_at = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(minutes=5)

    run(
        repo.create_chat_token(
            device_id=1,
            token_hash=token_hash,
            room_id=42,
            expires_at=expires_at,
        )
    )

    found = run(repo.get_chat_token_by_hash(token_hash))
    assert found is not None
    assert found["token_hash"] == token_hash
    assert found["room_id"] == 42


def test_get_chat_token_by_hash_returns_none_for_unknown(popup_db, run):
    from app.repositories import tray as repo

    result = run(repo.get_chat_token_by_hash("doesnotexist"))
    assert result is None


def test_mark_chat_token_used_sets_used_at(popup_db, run):
    from app.repositories import tray as repo
    from app.services import tray as svc

    raw_token = svc.generate_install_token()
    token_hash = svc.hash_token(raw_token)
    expires_at = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(minutes=5)

    record = run(
        repo.create_chat_token(
            device_id=1,
            token_hash=token_hash,
            room_id=None,
            expires_at=expires_at,
        )
    )
    assert record["used_at"] is None

    run(repo.mark_chat_token_used(int(record["id"])))

    updated = run(repo.get_chat_token_by_hash(token_hash))
    assert updated is not None
    assert updated["used_at"] is not None


# ---------------------------------------------------------------------------
# Popup session cookie helpers
# Tests invoke the encryption helpers directly to avoid loading all API routes,
# which would require the full dependency set (pyotp, apscheduler, etc.).
# ---------------------------------------------------------------------------

_POPUP_SESSION_COOKIE = "tray_popup"
_POPUP_SESSION_TTL_SECONDS = 7200


def _build_popup_session(*, device_id, room_id, company_id, csrf_token):
    """Mirror of _build_popup_session_payload in tray.py for direct testing."""
    from app.security.encryption import encrypt_secret

    exp = (
        datetime.now(timezone.utc) + timedelta(seconds=_POPUP_SESSION_TTL_SECONDS)
    ).isoformat()
    raw = json.dumps(
        {
            "device_id": device_id,
            "room_id": room_id,
            "company_id": company_id,
            "csrf": csrf_token,
            "exp": exp,
        }
    )
    return encrypt_secret(raw)


def _parse_popup_session_cookie(cookie_val):
    """Mirror of _parse_popup_session in tray.py for direct testing."""
    from app.security.encryption import decrypt_secret

    if not cookie_val:
        return None
    try:
        decoded = decrypt_secret(cookie_val)
        payload = json.loads(decoded)
    except Exception:
        return None
    exp_str = payload.get("exp", "")
    try:
        exp = datetime.fromisoformat(exp_str)
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=timezone.utc)
        if exp < datetime.now(timezone.utc):
            return None
    except Exception:
        return None
    return payload


def test_popup_session_round_trip(monkeypatch):
    """_build_popup_session / _parse_popup_session_cookie round-trip."""
    monkeypatch.setenv("SECRET_KEY", "test-secret-key-32-bytes-padding!")

    cookie_val = _build_popup_session(
        device_id=7,
        room_id=99,
        company_id=3,
        csrf_token="test-csrf",
    )
    assert isinstance(cookie_val, str)
    assert len(cookie_val) > 10

    payload = _parse_popup_session_cookie(cookie_val)
    assert payload is not None
    assert payload["device_id"] == 7
    assert payload["room_id"] == 99
    assert payload["company_id"] == 3
    assert payload["csrf"] == "test-csrf"


def test_popup_session_rejects_expired_cookie(monkeypatch):
    """An expired popup session cookie must be rejected."""
    monkeypatch.setenv("SECRET_KEY", "test-secret-key-32-bytes-padding!")

    from app.security.encryption import encrypt_secret

    # Build a cookie with an expiry in the past.
    past_exp = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    raw = json.dumps(
        {
            "device_id": 1,
            "room_id": 5,
            "company_id": 2,
            "csrf": "csrf-val",
            "exp": past_exp,
        }
    )
    expired_cookie = encrypt_secret(raw)

    result = _parse_popup_session_cookie(expired_cookie)
    assert result is None


def test_popup_session_rejects_missing_cookie():
    """A missing cookie value returns None."""
    result = _parse_popup_session_cookie(None)
    assert result is None

    result = _parse_popup_session_cookie("")
    assert result is None


def test_popup_session_rejects_tampered_cookie(monkeypatch):
    """A corrupted/tampered cookie value returns None."""
    monkeypatch.setenv("SECRET_KEY", "test-secret-key-32-bytes-padding!")

    result = _parse_popup_session_cookie("this-is-not-valid-encrypted-data")
    assert result is None

