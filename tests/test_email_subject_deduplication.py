"""Exercise the shared IMAP/Graph subject matcher against actual SQL rows."""

import sqlite3

import pytest

from app.services import imap


SUBJECT = "New vulnerabilities notification from Microsoft Defender for Endpoint"


@pytest.fixture
def mail_db(monkeypatch):
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.executescript("""
        CREATE TABLE tickets (
            id INTEGER PRIMARY KEY, subject TEXT, status TEXT DEFAULT 'new',
            requester_id INTEGER, requester_staff_id INTEGER, description TEXT,
            updated_at TEXT DEFAULT '2026-01-01'
        );
        CREATE TABLE users (id INTEGER PRIMARY KEY, email TEXT);
        CREATE TABLE staff (id INTEGER PRIMARY KEY, email TEXT);
        CREATE TABLE ticket_watchers (ticket_id INTEGER, user_id INTEGER, email TEXT);
        CREATE TABLE ticket_replies (
            ticket_id INTEGER, author_email TEXT, is_internal INTEGER DEFAULT 0
        );
    """)

    async def fetch_all(query, params):
        return [dict(row) for row in connection.execute(query.replace("%s", "?"), params)]

    monkeypatch.setattr(imap.db, "fetch_all", fetch_all)
    yield connection
    connection.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("requester_id", [None, 99])
async def test_repeated_external_notifications_match_original_author(mail_db, requester_id):
    mail_db.execute("INSERT INTO tickets (id, subject) VALUES (1, ?)", (SUBJECT,))
    mail_db.execute(
        "INSERT INTO ticket_replies (ticket_id, author_email) VALUES (1, ?)",
        ("defender@example.com",),
    )

    # Independent deliveries have different message IDs and no reply headers.
    for _ in range(3):
        ticket = await imap._find_existing_ticket_for_reply(
            subject=SUBJECT,
            from_email="Defender@Example.COM",
            requester_id=requester_id,
        )
        assert ticket is not None
        assert ticket["id"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("requester_id", [None, 99])
async def test_cc_recipient_reply_matches_email_only_watcher(mail_db, requester_id):
    mail_db.execute("INSERT INTO tickets (id, subject) VALUES (1, ?)", (SUBJECT,))
    mail_db.executemany(
        "INSERT INTO ticket_watchers (ticket_id, email) VALUES (1, ?)",
        [("alice@example.com",), ("bob@example.com",)],
    )

    for address in ("Alice@Example.com", "bob@example.com"):
        ticket = await imap._find_existing_ticket_for_reply(
            subject="RE: [External] " + SUBJECT,
            from_email=address,
            requester_id=requester_id,
        )
        assert ticket is not None
        assert ticket["id"] == 1


@pytest.mark.asyncio
async def test_matching_subject_is_found_beyond_twenty_recent_tickets(mail_db):
    mail_db.execute("INSERT INTO users VALUES (5, 'alice@example.com')")
    mail_db.execute(
        "INSERT INTO tickets (id, subject, requester_id) VALUES (1, ?, 5)", (SUBJECT,)
    )
    mail_db.executemany(
        "INSERT INTO tickets (id, subject, requester_id, updated_at) VALUES (?, ?, 5, '2026-02-01')",
        [(number, f"Unrelated request {number}") for number in range(2, 27)],
    )

    ticket = await imap._find_existing_ticket_for_reply(
        subject=SUBJECT, from_email="alice@example.com", requester_id=5,
    )
    assert ticket is not None
    assert ticket["id"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("requester_id", [None, 99])
async def test_description_mentions_do_not_establish_participation(mail_db, requester_id):
    mail_db.execute(
        "INSERT INTO tickets (id, subject, description) VALUES (1, ?, ?)",
        (SUBJECT, "Please contact victim@example.com"),
    )
    ticket = await imap._find_existing_ticket_for_reply(
        subject=SUBJECT, from_email="victim@example.com", requester_id=requester_id,
    )
    assert ticket is None


@pytest.mark.asyncio
@pytest.mark.parametrize("status, incoming_subject, sender, internal", [
    ("closed", SUBJECT, "alice@example.com", 0),
    ("new", "An unrelated subject", "alice@example.com", 0),
    ("new", SUBJECT, "stranger@example.com", 0),
    ("new", SUBJECT, "alice@example.com", 1),
])
async def test_subject_matching_requires_eligible_ticket_and_participant(
    mail_db, status, incoming_subject, sender, internal,
):
    mail_db.execute(
        "INSERT INTO tickets (id, subject, status) VALUES (1, ?, ?)", (SUBJECT, status)
    )
    mail_db.execute(
        "INSERT INTO ticket_replies VALUES (1, 'alice@example.com', ?)", (internal,)
    )
    ticket = await imap._find_existing_ticket_for_reply(
        subject=incoming_subject, from_email=sender,
    )
    assert ticket is None


@pytest.mark.asyncio
async def test_staff_requester_email_can_match_subject(mail_db):
    mail_db.execute("INSERT INTO staff VALUES (7, 'alice@example.com')")
    mail_db.execute(
        "INSERT INTO tickets (id, subject, requester_staff_id) VALUES (1, ?, 7)", (SUBJECT,)
    )
    ticket = await imap._find_existing_ticket_for_reply(
        subject=SUBJECT, from_email="alice@example.com",
    )
    assert ticket is not None
    assert ticket["id"] == 1
