"""Tests for app.services.email_recipients.record_recipients."""

import pytest


@pytest.mark.asyncio
async def test_record_recipients_inserts_one_row_per_address(monkeypatch):
    from app.services import email_recipients
    from app.core import database

    fetch_calls = []
    execute_calls = []

    async def mock_fetch_one(query, params):
        fetch_calls.append((query, params))
        return None  # No existing row

    async def mock_execute(query, params):
        execute_calls.append({'query': query, 'params': params})
        return 1

    monkeypatch.setattr(database.db, "fetch_one", mock_fetch_one)
    monkeypatch.setattr(database.db, "execute", mock_execute)

    inserted = await email_recipients.record_recipients(
        reply_id=42,
        tracking_id="track-1",
        smtp2go_message_id="smtp-1",
        to=["alice@example.com"],
        cc=["Bob <bob@example.com>", "carol@example.com"],
        bcc=["dave@example.com"],
        names_by_email={"alice@example.com": "Alice"},
    )

    assert inserted == 4
    assert len(execute_calls) == 4

    # Inserts should normalise email casing/angle brackets.
    inserted_addresses = {c['params']['email'] for c in execute_calls}
    assert inserted_addresses == {
        "alice@example.com",
        "bob@example.com",
        "carol@example.com",
        "dave@example.com",
    }

    # Roles must reflect the To/CC/BCC channels.
    by_email = {c['params']['email']: c['params'] for c in execute_calls}
    assert by_email["alice@example.com"]['role'] == "to"
    assert by_email["alice@example.com"]['name'] == "Alice"
    assert by_email["bob@example.com"]['role'] == "cc"
    assert by_email["carol@example.com"]['role'] == "cc"
    assert by_email["dave@example.com"]['role'] == "bcc"

    # Tracking identifiers are stamped on every row.
    for params in by_email.values():
        assert params['tracking_id'] == "track-1"
        assert params['smtp2go_message_id'] == "smtp-1"
        assert params['reply_id'] == 42


@pytest.mark.asyncio
async def test_record_recipients_is_idempotent_for_same_recipient(monkeypatch):
    """A second send for the same (reply_id, recipient, role) does not duplicate."""
    from app.services import email_recipients
    from app.core import database

    existing_rows = {
        ("alice@example.com", "to"): {
            "id": 1,
            "tracking_id": None,
            "smtp2go_message_id": None,
        },
    }
    execute_calls = []

    async def mock_fetch_one(query, params):
        return existing_rows.get((params.get("email"), params.get("role")))

    async def mock_execute(query, params):
        execute_calls.append({'query': query, 'params': params})
        return 1

    monkeypatch.setattr(database.db, "fetch_one", mock_fetch_one)
    monkeypatch.setattr(database.db, "execute", mock_execute)

    inserted = await email_recipients.record_recipients(
        reply_id=42,
        tracking_id="track-2",
        smtp2go_message_id="smtp-2",
        to=["alice@example.com"],
    )

    # No new INSERT should happen because the row already exists.
    assert inserted == 0
    assert all("INSERT" not in c['query'] for c in execute_calls), execute_calls
    # But tracking identifiers should be backfilled on the existing row.
    assert any("UPDATE" in c['query'] for c in execute_calls)
    update = next(c for c in execute_calls if "UPDATE" in c['query'])
    assert update['params']['tracking_id'] == "track-2"
    assert update['params']['smtp2go_message_id'] == "smtp-2"


@pytest.mark.asyncio
async def test_record_recipients_handles_no_addresses(monkeypatch):
    from app.services import email_recipients

    inserted = await email_recipients.record_recipients(
        reply_id=1,
        tracking_id="t",
        smtp2go_message_id=None,
        to=[],
    )
    assert inserted == 0
