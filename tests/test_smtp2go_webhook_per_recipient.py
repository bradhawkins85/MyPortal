"""Per-recipient SMTP2Go webhook handling.

Verifies that delivered/opened/bounced webhooks for two different recipients
of the same SMTP2Go message id update each recipient's row independently
while still updating the aggregate ``ticket_replies`` columns the existing
single-status badge depends on.
"""

import pytest


@pytest.mark.asyncio
async def test_per_recipient_webhook_updates_independently(monkeypatch):
    from app.services import smtp2go
    from app.core import database

    # In-memory store of recipient rows, keyed by (smtp2go_message_id, email).
    recipient_rows: dict[tuple[str, str], dict] = {
        ("MSG-1", "alice@example.com"): {
            "id": 11,
            "ticket_reply_id": 99,
            "recipient_email": "alice@example.com",
            "recipient_role": "to",
            "tracking_id": "trk-1",
            "smtp2go_message_id": "MSG-1",
            "email_open_count": 0,
        },
        ("MSG-1", "bob@example.com"): {
            "id": 12,
            "ticket_reply_id": 99,
            "recipient_email": "bob@example.com",
            "recipient_role": "cc",
            "tracking_id": "trk-1",
            "smtp2go_message_id": "MSG-1",
            "email_open_count": 0,
        },
    }
    aggregate_updates: list[str] = []
    recipient_updates: list[dict] = []

    async def mock_fetch_one(query, params):
        if "FROM ticket_replies" in query:
            return {"id": 99, "email_tracking_id": "trk-1"}
        if "FROM ticket_reply_email_recipients" in query:
            email = params.get("email")
            msg_id = params.get("smtp2go_message_id")
            if email and msg_id:
                return recipient_rows.get((msg_id, email))
            tracking = params.get("tracking_id")
            if tracking and email:
                for row in recipient_rows.values():
                    if row["tracking_id"] == tracking and row["recipient_email"] == email:
                        return row
            return None
        return None

    async def mock_execute(query, params):
        if "INSERT INTO email_tracking_events" in query:
            return 1
        if "UPDATE ticket_replies" in query:
            aggregate_updates.append(query)
            return 1
        if "UPDATE ticket_reply_email_recipients" in query:
            recipient_updates.append({"query": query, "params": params})
            row_id = params.get("id")
            for row in recipient_rows.values():
                if row["id"] == row_id:
                    if "email_open_count = :open_count" in query:
                        row["email_open_count"] = params["open_count"]
            return 1
        return 1

    monkeypatch.setattr(database.db, "fetch_one", mock_fetch_one)
    monkeypatch.setattr(database.db, "execute", mock_execute)

    # Bob is delivered.
    await smtp2go.process_webhook_event("delivered", {
        "email_id": "MSG-1",
        "rcpt": "bob@example.com",
        "time": "2026-04-28T05:56:13Z",
    })
    # Alice opens.
    await smtp2go.process_webhook_event("open", {
        "email_id": "MSG-1",
        "rcpt": "alice@example.com",
        "time": "2026-04-28T05:56:31Z",
        "user-agent": "Apple Mail 605.1",
    })
    # Bob bounces (separate from delivered to test independent status).
    await smtp2go.process_webhook_event("bounce", {
        "email_id": "MSG-1",
        "rcpt": "bob@example.com",
        "time": "2026-04-28T05:57:00Z",
        "reason": "550 mailbox full",
    })

    # The aggregate ticket_replies must still be updated for each event so
    # the existing single-status badge keeps working.
    assert len(aggregate_updates) == 3, aggregate_updates

    # Recipient rows must each receive their own updates.
    bob_updates = [u for u in recipient_updates if u['params']['id'] == 12]
    alice_updates = [u for u in recipient_updates if u['params']['id'] == 11]
    assert bob_updates, recipient_updates
    assert alice_updates, recipient_updates

    # Alice's open event captured the user-agent in last_event_detail.
    open_update = next(u for u in alice_updates if u['params'].get('last_event_type') == 'open')
    assert open_update['params'].get('detail') == "Apple Mail 605.1"
    # And bumped the open count to 1.
    assert recipient_rows[("MSG-1", "alice@example.com")]["email_open_count"] == 1

    # Bob's bounce captured the bounce reason.
    bounce_update = next(u for u in bob_updates if u['params'].get('last_event_type') == 'bounce')
    assert bounce_update['params'].get('detail') == "550 mailbox full"
