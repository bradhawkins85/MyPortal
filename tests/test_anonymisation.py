"""Tests for account anonymisation (issue #4554).

The repository tests run against a real SQLite database whose tables mirror
the production schema, including its NOT NULL constraints and the columns
later migrations dropped (``users.totp_secret``, ``call_recordings.caller_number``).
Migrations 458 and 459 are applied through the real SQLite adapter.
"""

import inspect
from pathlib import Path

import aiosqlite
import pytest

from app.core.database import Database
from app.repositories import anonymisation as anon_repo
from app.repositories import marketing_campaigns as campaign_repo
from app.repositories import users as users_repo
from app.services import anonymisation as anon_service
from app.services import audit as audit_service
from app.services import email as email_service

MIGRATIONS = Path(__file__).resolve().parent.parent / "migrations"

SCHEMA = """
CREATE TABLE users (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  email VARCHAR(255) NOT NULL UNIQUE,
  password_hash VARCHAR(255) NOT NULL,
  company_id INT NULL,
  first_name VARCHAR(255) NULL,
  last_name VARCHAR(255) NULL,
  mobile_phone VARCHAR(20) NULL,
  is_super_admin TINYINT NOT NULL DEFAULT 0,
  booking_link_url VARCHAR(500) NULL,
  email_signature TEXT NULL,
  is_active TINYINT NOT NULL DEFAULT 1,
  matrix_user_id VARCHAR(255) NULL,
  passkey_user_handle VARCHAR(128) NULL,
  ai_opt_out TINYINT NOT NULL DEFAULT 0
);
CREATE TABLE staff (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  company_id INT NOT NULL,
  first_name VARCHAR(255) NOT NULL,
  last_name VARCHAR(255) NOT NULL,
  email VARCHAR(255) NOT NULL,
  mobile_phone VARCHAR(20) NULL,
  street VARCHAR(255) NULL,
  city VARCHAR(255) NULL,
  state VARCHAR(255) NULL,
  postcode VARCHAR(20) NULL,
  offboarding_email_forward_to VARCHAR(255) NULL,
  offboarding_mailbox_grant_emails TEXT NULL,
  offboarding_out_of_office TEXT NULL,
  requested_by_name VARCHAR(255) NULL,
  requested_by_email VARCHAR(255) NULL,
  portal_user_id INT NULL
);
CREATE TABLE tickets (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  requester_id INT NULL,
  requester_staff_id INT NULL,
  subject VARCHAR(255) NOT NULL,
  description TEXT NULL,
  ai_summary TEXT NULL
);
CREATE TABLE ticket_replies (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ticket_id INT NOT NULL,
  author_id INT NULL,
  author_email VARCHAR(255) NULL,
  author_display_name VARCHAR(255) NULL,
  body TEXT NOT NULL,
  email_tracking_id VARCHAR(64) NULL
);
CREATE TABLE ticket_attachments (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ticket_id INT NOT NULL,
  filename VARCHAR(255) NOT NULL,
  original_filename VARCHAR(255) NOT NULL,
  mime_type VARCHAR(127) NULL
);
CREATE TABLE email_tracking_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  tracking_id VARCHAR(64) NOT NULL,
  ip_address VARCHAR(45) NULL
);
CREATE TABLE chat_messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  room_id INT NOT NULL,
  sender_matrix_id VARCHAR(255) NOT NULL,
  sender_user_id INT NULL,
  sender_display_name VARCHAR(255) NULL,
  body TEXT
);
CREATE TABLE chat_user_links (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  user_id INT NULL,
  email VARCHAR(255) NULL,
  matrix_user_id VARCHAR(255) NOT NULL
);
CREATE TABLE sms_ticket_links (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ticket_id INT NOT NULL,
  from_number VARCHAR(64) NOT NULL,
  from_number_normalized VARCHAR(32) NOT NULL
);
CREATE TABLE call_recordings (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  file_path VARCHAR(500) NOT NULL,
  phone_number VARCHAR(50) NULL,
  caller_staff_id INT NULL,
  callee_staff_id INT NULL,
  transcription TEXT NULL
);
CREATE TABLE user_sessions (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INT NOT NULL);
CREATE TABLE user_totp_authenticators (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INT NOT NULL, secret TEXT NOT NULL);
CREATE TABLE user_passkeys (id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INT NOT NULL);
CREATE TABLE passkey_challenges (challenge_id VARCHAR(64) PRIMARY KEY, user_id INT NULL);
CREATE TABLE password_tokens (token VARCHAR(64) PRIMARY KEY, user_id INT NOT NULL);
CREATE TABLE account_verification_tokens (token VARCHAR(64) PRIMARY KEY, user_id INT NOT NULL);
CREATE TABLE marketing_email_opt_outs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  email VARCHAR(255) NOT NULL,
  category VARCHAR(16) NOT NULL DEFAULT 'sales',
  campaign_id INT NULL
);
"""

EMAIL = "Jane.Doe@Example.com"
PHONE = "+61400111222"


class _PercentPlaceholderDb:
    """Runs a ``%s``-placeholder repository (marketing) on the SQLite test db."""

    def __init__(self, database: Database) -> None:
        self._db = database

    async def execute(self, sql, params=None):
        return await self._db.execute(sql.replace("%s", "?"), params)

    async def fetch_one(self, sql, params=None):
        return await self._db.fetch_one(sql.replace("%s", "?"), params)

    async def fetch_all(self, sql, params=None):
        return await self._db.fetch_all(sql.replace("%s", "?"), params)


@pytest.fixture
async def sqlite_db(monkeypatch):
    database = Database()
    database._use_sqlite = True
    conn = await aiosqlite.connect(":memory:")
    conn.row_factory = aiosqlite.Row
    database._sqlite_conn = conn
    try:
        await conn.executescript(SCHEMA + "CREATE TABLE migrations (name VARCHAR(255) PRIMARY KEY);")
        for name in ("458_account_anonymisation.sql", "459_account_anonymisation_workflow.sql"):
            await database._apply_migration_file(conn, MIGRATIONS / name)
        await conn.commit()
        monkeypatch.setattr(anon_repo, "db", database)
        monkeypatch.setattr(campaign_repo, "db", _PercentPlaceholderDb(database))
        yield database
    finally:
        await conn.close()


async def _seed(database: Database) -> None:
    conn = database._sqlite_conn
    await conn.executescript(
        f"""
        INSERT INTO users (id, email, password_hash, first_name, last_name, mobile_phone, email_signature,
                           matrix_user_id, passkey_user_handle, booking_link_url)
        VALUES (1, 'admin@example.com', 'x', 'Ada', 'Admin', NULL, NULL, NULL, NULL, NULL),
               (7, '{EMAIL}', '$2b$12$hash', 'Jane', 'Doe', '{PHONE}', 'Jane Doe, Example', '@jane:matrix', 'handle', 'https://book');
        INSERT INTO staff (id, company_id, first_name, last_name, email, mobile_phone, street, portal_user_id)
        VALUES (3, 1, 'Jane', 'Doe', 'jane.doe@example.com', '+61400999888', '1 Main St', NULL),
               (4, 1, 'Bob', 'Other', 'bob@example.com', '+61400000000', '2 Side St', NULL);
        INSERT INTO tickets (id, requester_id, requester_staff_id, subject, description, ai_summary)
        VALUES (10, 7, NULL, 'Printer', 'Call me on {PHONE} or {EMAIL}', 'Jane ({EMAIL}) reports a printer fault'),
               (11, 1, 3, 'Laptop', 'Raised for staff', NULL),
               (12, 1, NULL, 'Unrelated', 'Keep {PHONE} here', NULL);
        INSERT INTO ticket_replies (id, ticket_id, author_id, author_email, author_display_name, body, email_tracking_id)
        VALUES (20, 10, 7, '{EMAIL}', 'Jane Doe', 'Reach me at {EMAIL}', 'trk-own'),
               (21, 10, 1, 'admin@example.com', 'Ada Admin', 'We will call {PHONE}', 'trk-to-jane'),
               (22, 12, NULL, '{EMAIL}', 'Jane Doe', 'Emailed in reply', NULL),
               (23, 12, 1, 'admin@example.com', 'Ada Admin', 'Unrelated reply', 'trk-other');
        INSERT INTO ticket_attachments (id, ticket_id, filename, original_filename, mime_type)
        VALUES (30, 10, 'vm.wav', 'voicemail.wav', 'audio/wav'),
               (31, 10, 'doc.pdf', 'invoice.pdf', 'application/pdf');
        INSERT INTO email_tracking_events (tracking_id, ip_address)
        VALUES ('trk-own', '1.1.1.1'), ('trk-to-jane', '2.2.2.2'), ('trk-other', '3.3.3.3');
        INSERT INTO chat_messages (room_id, sender_matrix_id, sender_user_id, sender_display_name, body)
        VALUES (1, '@jane:matrix', 7, 'Jane Doe', 'My number is {PHONE}'),
               (1, '@ada:matrix', 1, 'Ada Admin', 'Thanks');
        INSERT INTO chat_user_links (user_id, email, matrix_user_id) VALUES (7, '{EMAIL}', '@jane:matrix');
        INSERT INTO sms_ticket_links (ticket_id, from_number, from_number_normalized)
        VALUES (10, '{PHONE}', '{PHONE}'), (12, '+61499999999', '+61499999999');
        INSERT INTO call_recordings (id, file_path, phone_number, caller_staff_id, transcription)
        VALUES (40, '/nonexistent/a.wav', '{PHONE}', NULL, 'Hi it is Jane'),
               (41, '/nonexistent/b.wav', '+61411111111', 3, 'Staff call'),
               (42, '/nonexistent/c.wav', '+61422222222', 4, 'Other');
        INSERT INTO user_sessions (user_id) VALUES (7), (1);
        INSERT INTO user_totp_authenticators (user_id, secret) VALUES (7, 's');
        INSERT INTO user_passkeys (user_id) VALUES (7);
        INSERT INTO password_tokens (token, user_id) VALUES ('p', 7);
        INSERT INTO account_verification_tokens (token, user_id) VALUES ('v', 7);
        """
    )
    await conn.commit()


async def _rows(database: Database, sql: str, params=()):
    return [dict(row) for row in await database.fetch_all(sql, params)]


def _patch_side_effects(monkeypatch, *, user_lookup):
    sent: list[dict] = []
    audits: list[dict] = []

    async def _send_email(**kwargs):
        sent.append(kwargs)
        return True, None

    async def _log_action(**kwargs):
        audits.append(kwargs)

    async def _get_user(user_id):
        result = user_lookup(user_id)
        return await result if inspect.isawaitable(result) else result

    async def _no_ticket(user, request_id, *, source):
        return None

    async def _no_rag(user_id):
        return {"tickets": 0}

    monkeypatch.setattr(email_service, "send_email", _send_email)
    monkeypatch.setattr(audit_service, "log_action", _log_action)
    monkeypatch.setattr(users_repo, "get_user_by_id", _get_user)
    monkeypatch.setattr(anon_service, "_create_support_ticket", _no_ticket)
    monkeypatch.setattr(anon_service, "_purge_rag", _no_rag)
    return sent, audits


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def test_placeholder_email_is_deterministic_and_undeliverable():
    assert anon_repo.placeholder_email_for(42) == "anonymised+42@invalid"
    assert anon_repo.placeholder_email_for(7) != anon_repo.placeholder_email_for(42)
    assert anon_repo.placeholder_staff_email_for(3) == "anonymised+staff3@invalid"


def test_is_anonymised_email_detects_placeholders():
    assert anon_repo.is_anonymised_email("anonymised+42@invalid")
    assert anon_repo.is_anonymised_email("ANONYMISED+42@INVALID")
    assert not anon_repo.is_anonymised_email("user@example.com")
    assert not anon_repo.is_anonymised_email(None)


def test_email_hash_is_case_insensitive_and_one_way():
    assert anon_repo.email_hash(EMAIL) == anon_repo.email_hash(" jane.doe@example.com ")
    assert EMAIL.lower() not in (anon_repo.email_hash(EMAIL) or "")
    assert anon_repo.email_hash("") is None


def test_migration_459_is_idempotent_ddl():
    sql = (MIGRATIONS / "459_account_anonymisation_workflow.sql").read_text()
    for line in sql.splitlines():
        if line.startswith("ALTER TABLE"):
            assert "ADD COLUMN IF NOT EXISTS" in line
    assert "CREATE INDEX IF NOT EXISTS" in sql


# ---------------------------------------------------------------------------
# Request lifecycle
# ---------------------------------------------------------------------------


@pytest.mark.anyio("asyncio")
async def test_create_request_prevents_duplicates(sqlite_db):
    await _seed(sqlite_db)
    first_id, created = await anon_repo.create_request(user_id=7, reason="Leaving")
    assert created
    again_id, created_again = await anon_repo.create_request(user_id=7, reason="Again")
    assert again_id == first_id
    assert not created_again
    rows = await _rows(sqlite_db, "SELECT status, reason FROM account_anonymisation_requests")
    assert rows == [{"status": "pending", "reason": "Leaving"}]


@pytest.mark.anyio("asyncio")
async def test_rejected_request_can_be_reopened(sqlite_db):
    await _seed(sqlite_db)
    request_id, _ = await anon_repo.create_request(user_id=7)
    assert await anon_repo.mark_rejected(request_id, decided_by=1, notes="Open invoices")
    reopened_id, created = await anon_repo.create_request(user_id=7, reason="Paid now")
    assert (reopened_id, created) == (request_id, True)
    row = await anon_repo.get_request(request_id)
    assert row["status"] == "pending"
    assert row["notes"] is None and row["decided_by"] is None


@pytest.mark.anyio("asyncio")
async def test_service_create_request_validates_and_notifies(sqlite_db, monkeypatch):
    await _seed(sqlite_db)
    sent, audits = _patch_side_effects(monkeypatch, user_lookup=lambda _id: None)
    user = {"id": 7, "email": EMAIL}

    with pytest.raises(anon_service.AnonymisationError):
        await anon_service.create_request(user=user, confirm_email=EMAIL, acknowledged=False)
    with pytest.raises(anon_service.AnonymisationError):
        await anon_service.create_request(user=user, confirm_email="wrong@example.com", acknowledged=True)

    result = await anon_service.create_request(
        user=user, reason="Leaving", confirm_email=EMAIL.lower(), acknowledged=True
    )
    assert result["created"] is True
    assert [a["action"] for a in audits] == ["account_anonymisation.request"]
    assert sent and sent[0]["recipients"] == [EMAIL]

    duplicate = await anon_service.create_request(user=user, confirm_email=EMAIL, acknowledged=True)
    assert duplicate["created"] is False
    assert len(sent) == 1 and len(audits) == 1


@pytest.mark.anyio("asyncio")
async def test_reject_requires_reason_and_emails_it(sqlite_db, monkeypatch):
    await _seed(sqlite_db)
    sent, audits = _patch_side_effects(monkeypatch, user_lookup=lambda _id: None)
    request_id, _ = await anon_repo.create_request(user_id=7)

    with pytest.raises(anon_service.AnonymisationError):
        await anon_service.reject_request(request_id=request_id, actor_user={"id": 1}, reason="  ")

    await anon_service.reject_request(request_id=request_id, actor_user={"id": 1}, reason="Open invoices")
    row = await anon_repo.get_request(request_id)
    assert row["status"] == "rejected"
    assert row["notes"] == "Open invoices"
    assert row["decided_by"] == 1
    assert sent[-1]["recipients"] == [EMAIL]
    assert "Open invoices" in sent[-1]["text_body"]
    assert audits[-1]["action"] == "account_anonymisation.reject"

    with pytest.raises(anon_service.AnonymisationError):
        await anon_service.approve_request(request_id=request_id, actor_user={"id": 1})


# ---------------------------------------------------------------------------
# Approval runs the anonymisation
# ---------------------------------------------------------------------------


async def _approve(sqlite_db, monkeypatch):
    await _seed(sqlite_db)

    async def _lookup(user_id):
        return await sqlite_db.fetch_one("SELECT * FROM users WHERE id = ?", (user_id,))

    sent, audits = _patch_side_effects(monkeypatch, user_lookup=_lookup)
    request_id, _ = await anon_repo.create_request(user_id=7, reason="Leaving")
    result = await anon_service.approve_request(request_id=request_id, actor_user={"id": 1})
    return request_id, result, sent, audits


@pytest.mark.anyio("asyncio")
async def test_approve_anonymises_every_table(sqlite_db, monkeypatch):
    request_id, result, sent, audits = await _approve(sqlite_db, monkeypatch)
    assert result["status"] == "completed"

    user = (await _rows(sqlite_db, "SELECT * FROM users WHERE id = 7"))[0]
    assert user["first_name"] == "Anonymised" and user["last_name"] == "user"
    assert user["email"] == "anonymised+7@invalid"
    assert user["mobile_phone"] is None and user["email_signature"] is None
    assert user["matrix_user_id"] is None and user["passkey_user_handle"] is None
    assert user["booking_link_url"] is None
    assert user["is_active"] == 0 and user["ai_opt_out"] == 1
    assert user["password_hash"] == anon_repo.UNUSABLE_PASSWORD_HASH

    for table in ("user_sessions", "user_totp_authenticators", "user_passkeys",
                  "password_tokens", "account_verification_tokens", "chat_user_links"):
        assert await _rows(sqlite_db, "SELECT * FROM " + table + " WHERE user_id = 7") == [], table
    assert len(await _rows(sqlite_db, "SELECT * FROM user_sessions WHERE user_id = 1")) == 1

    staff = {row["id"]: row for row in await _rows(sqlite_db, "SELECT * FROM staff")}
    assert staff[3]["first_name"] == "Anonymised"
    assert staff[3]["email"] == "anonymised+staff3@invalid"
    assert staff[3]["mobile_phone"] is None and staff[3]["street"] is None
    assert staff[4]["email"] == "bob@example.com"

    tickets = {row["id"]: row for row in await _rows(sqlite_db, "SELECT * FROM tickets")}
    assert tickets[10]["requester_id"] == 7  # business record kept
    assert PHONE not in tickets[10]["description"] and EMAIL not in tickets[10]["description"]
    assert EMAIL not in tickets[10]["ai_summary"]
    assert tickets[12]["description"] == "Keep " + PHONE + " here"

    replies = {row["id"]: row for row in await _rows(sqlite_db, "SELECT * FROM ticket_replies")}
    assert replies[20]["author_id"] == 7
    assert replies[20]["author_display_name"] == "Anonymised user"
    assert replies[20]["author_email"] == "anonymised+7@invalid"
    assert EMAIL not in replies[20]["body"]
    assert PHONE not in replies[21]["body"]
    assert replies[21]["author_email"] == "admin@example.com"
    assert replies[22]["author_email"] == "anonymised+7@invalid"
    assert replies[23]["author_display_name"] == "Ada Admin"

    tracking = {row["tracking_id"] for row in await _rows(sqlite_db, "SELECT * FROM email_tracking_events")}
    assert tracking == {"trk-other"}

    chats = await _rows(sqlite_db, "SELECT * FROM chat_messages ORDER BY id")
    assert chats[0]["sender_user_id"] == 7
    assert chats[0]["sender_matrix_id"] == "anonymised+7@invalid"
    assert chats[0]["sender_display_name"] == "Anonymised user"
    assert PHONE not in chats[0]["body"]
    assert chats[1]["sender_display_name"] == "Ada Admin"

    sms = await _rows(sqlite_db, "SELECT from_number FROM sms_ticket_links")
    assert sms == [{"from_number": "+61499999999"}]

    recordings = {row["id"] for row in await _rows(sqlite_db, "SELECT id FROM call_recordings")}
    assert recordings == {42}
    attachments = {row["id"] for row in await _rows(sqlite_db, "SELECT id FROM ticket_attachments")}
    assert attachments == {31}

    opt_outs = {row["email"] for row in await _rows(sqlite_db, "SELECT email FROM marketing_email_opt_outs")}
    assert opt_outs == {EMAIL.lower(), "anonymised+7@invalid"}

    request = await anon_repo.get_request(request_id)
    assert request["status"] == "completed"
    assert request["decided_by"] == 1 and request["completed_at"] is not None
    assert request["original_email_hash"] == anon_repo.email_hash(EMAIL)
    assert request["ip_address"] is None


@pytest.mark.anyio("asyncio")
async def test_approve_emails_original_address_and_audits_without_pii(sqlite_db, monkeypatch):
    _, _, sent, audits = await _approve(sqlite_db, monkeypatch)
    assert [mail["recipients"] for mail in sent] == [[EMAIL]]
    assert audits[-1]["action"] == "account_anonymisation.complete"
    serialised = repr(audits).lower()
    for secret in (EMAIL.lower(), PHONE, "jane"):
        assert secret.lower() not in serialised


@pytest.mark.anyio("asyncio")
async def test_anonymised_user_cannot_sign_in(sqlite_db, monkeypatch):
    from app.security.passwords import verify_password

    await _approve(sqlite_db, monkeypatch)
    user = (await _rows(sqlite_db, "SELECT * FROM users WHERE id = 7"))[0]
    assert not verify_password("anything", user["password_hash"])
    assert user["is_active"] == 0


@pytest.mark.anyio("asyncio")
async def test_anonymise_user_is_idempotent(sqlite_db, monkeypatch):
    request_id, _, _, _ = await _approve(sqlite_db, monkeypatch)
    snapshot = await _rows(sqlite_db, "SELECT * FROM users WHERE id = 7")
    # A second run (as after a crash before the request was marked complete).
    await anon_repo.anonymise_user(7, original_email=None, phones=[], staff_ids=[3])
    assert await _rows(sqlite_db, "SELECT * FROM users WHERE id = 7") == snapshot


@pytest.mark.anyio("asyncio")
async def test_failed_run_stays_approved_and_can_be_retried(sqlite_db, monkeypatch):
    await _seed(sqlite_db)

    async def _lookup(user_id):
        return await sqlite_db.fetch_one("SELECT * FROM users WHERE id = ?", (user_id,))

    _patch_side_effects(monkeypatch, user_lookup=_lookup)
    request_id, _ = await anon_repo.create_request(user_id=7)
    real_run = anon_repo.anonymise_user
    calls = {"n": 0}

    async def _flaky(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("boom " + EMAIL)
        return await real_run(*args, **kwargs)

    monkeypatch.setattr(anon_repo, "anonymise_user", _flaky)
    with pytest.raises(anon_service.AnonymisationError):
        await anon_service.approve_request(request_id=request_id, actor_user={"id": 1})
    row = await anon_repo.get_request(request_id)
    assert row["status"] == "approved"
    assert EMAIL not in (row["error_message"] or "")

    result = await anon_service.approve_request(request_id=request_id, actor_user={"id": 1})
    assert result["status"] == "completed"


@pytest.mark.anyio("asyncio")
async def test_cannot_approve_own_request(sqlite_db, monkeypatch):
    await _seed(sqlite_db)
    _patch_side_effects(monkeypatch, user_lookup=lambda _id: None)
    request_id, _ = await anon_repo.create_request(user_id=7)
    with pytest.raises(anon_service.AnonymisationError):
        await anon_service.approve_request(request_id=request_id, actor_user={"id": 7})


@pytest.mark.anyio("asyncio")
async def test_start_for_user_anonymises_immediately(sqlite_db, monkeypatch):
    await _seed(sqlite_db)

    async def _lookup(user_id):
        return await sqlite_db.fetch_one("SELECT * FROM users WHERE id = ?", (user_id,))

    _patch_side_effects(monkeypatch, user_lookup=_lookup)
    result = await anon_service.start_for_user(user_id=7, actor_user={"id": 1}, notes="Phone call")
    assert result["status"] == "completed"
    row = await anon_repo.get_request(result["request_id"])
    assert row["requested_by_user_id"] == 1
    assert row["reason"] == "Phone call"


@pytest.mark.anyio("asyncio")
async def test_marketing_excludes_anonymised_email_by_hash(sqlite_db, monkeypatch):
    await _approve(sqlite_db, monkeypatch)
    await sqlite_db.execute("DELETE FROM marketing_email_opt_outs")
    opted_out = await campaign_repo.list_opted_out([EMAIL.lower(), "bob@example.com"], "sales")
    assert opted_out == {EMAIL.lower()}


@pytest.mark.anyio("asyncio")
async def test_optional_steps_tolerate_missing_tables(sqlite_db):
    await _seed(sqlite_db)
    await sqlite_db.execute("DROP TABLE chat_messages")
    await sqlite_db.execute("DROP TABLE sms_ticket_links")
    summary = await anon_repo.anonymise_user(7, original_email=EMAIL, phones=[PHONE])
    assert summary["users"] == 1


# ---------------------------------------------------------------------------
# Routes and SQL hygiene
# ---------------------------------------------------------------------------


def test_anonymisation_routes_are_registered():
    from app.main import app

    expected = {
        ("POST", "/admin/profile/anonymisation"),
        ("GET", "/admin/anonymisation"),
        ("GET", "/admin/anonymisation/{request_id}"),
        ("POST", "/admin/anonymisation/{request_id}/approve"),
        ("POST", "/admin/anonymisation/{request_id}/reject"),
        ("POST", "/admin/users/{user_id}/{action}"),
    }
    registered = {
        (method, route.path)
        for route in app.routes
        for method in (getattr(route, "methods", None) or [])
        if hasattr(route, "path")
    }
    assert not expected - registered


def test_repository_sql_is_not_built_with_fstrings():
    source = Path(anon_repo.__file__).read_text()
    for marker in ('f"SELECT', 'f"UPDATE', 'f"DELETE', 'f"INSERT', "f'SELECT", ".format("):
        assert marker not in source


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------


def _render(template: str, **context) -> str:
    from datetime import datetime
    from types import SimpleNamespace

    from app import main as main_module

    values = {
        "request": SimpleNamespace(url=SimpleNamespace(path="/admin/anonymisation", query=""), query_params={}),
        "app_name": "MyPortal", "csrf_token": "t", "is_super_admin": True,
        "has_authenticated_user": True, "active_membership": {}, "available_companies": [],
        "module_enabled": {}, "enabled_module_slugs": [],
        "current_user": {"id": 1, "email": "admin@example.com"},
        "now": datetime(2026, 10, 3),
    }
    values.update(context)
    return main_module.templates.env.get_template(template).render(**values)


def _sample_request(status: str) -> dict:
    from datetime import datetime

    return {
        "id": 5, "user_id": 7, "status": status, "reason": "Leaving",
        "requested_at": datetime(2026, 10, 1, 9, 0), "requested_by_user_id": 7,
        "request_user_email": EMAIL, "support_ticket_id": 9, "decided_by": None,
        "decided_at": None, "notes": None, "completed_at": None, "error_message": None,
    }


def test_admin_list_uses_table_macros_and_status_filter():
    html = _render(
        "admin/anonymisation.html",
        anonymisation_request=None,
        anonymisation_requests=[_sample_request("pending"), _sample_request("completed")],
        anonymisation_status_filter="pending",
    )
    assert 'data-table-id="anonymisation-requests"' in html
    assert 'status status--warning">Pending' in html
    assert 'href="/admin/anonymisation/5"' in html


def test_admin_detail_offers_approve_and_reject_modals_only_when_pending():
    pending = _render("admin/anonymisation.html", anonymisation_request=_sample_request("pending"), anonymisation_requests=[])
    assert 'action="/admin/anonymisation/5/approve"' in pending
    assert 'action="/admin/anonymisation/5/reject"' in pending
    assert 'name="reason"' in pending and "required" in pending
    assert '<div class="modal" id="anonymise-reject-modal" role="dialog"' in pending

    done = _render("admin/anonymisation.html", anonymisation_request=_sample_request("completed"), anonymisation_requests=[])
    assert "/approve" not in done and "/reject" not in done


def test_users_page_offers_anonymise_modal():
    html = _render(
        "admin/users.html",
        users=[{"id": 7, "email": EMAIL, "first_name": "Jane", "last_name": "Doe", "is_super_admin": False,
                "last_login_at": None, "ai_opt_out": False, "company_name": "Acme"}],
    )
    assert 'data-anonymise-user-id="7"' in html
    assert 'id="anonymise-user-modal"' in html and 'name="confirm"' in html
    assert "anonymisation_admin.js" in html


def test_anonymise_modal_script_builds_action_from_numeric_id_only():
    source = (Path(__file__).resolve().parent.parent / "app/static/js/anonymisation_admin.js").read_text()
    assert "data-anonymise-action" not in source
    assert "Number.parseInt" in source and "/admin/users/${userId}/anonymise" in source
