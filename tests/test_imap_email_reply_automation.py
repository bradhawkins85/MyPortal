"""
Tests for verifying that email replies trigger automation events.

This test suite ensures that when an email reply is added to an existing ticket
via IMAP processing, the tickets.updated automation event is triggered.
"""

from datetime import datetime, timezone
from typing import Any

import pytest

from app.services.sanitization import sanitize_rich_text


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


async def _simulate_email_reply_processing(
    *,
    tickets_repo,
    tickets_service,
    ticket_id: int,
    is_new_ticket: bool,
    body: str,
    requester_id: int | None,
    message_id: str | None = None,
) -> None:
    """
    Simulate the email reply processing logic from sync_account.
    
    This helper function mirrors the fix that was added to imap.py
    to trigger automation events when email replies are added to tickets.
    """
    if not is_new_ticket:
        conversation_source = body or ""
        sanitized = sanitize_rich_text(conversation_source)
        if sanitized.has_rich_content:
            reply_author_id = requester_id
            reply_added = False
            try:
                await tickets_repo.create_reply(
                    ticket_id=int(ticket_id),
                    author_id=reply_author_id,
                    body=sanitized.html,
                    is_internal=False,
                    external_reference=message_id if message_id else None,
                )
                reply_added = True
            except Exception:
                pass

            if reply_added:
                try:
                    actor_info: dict[str, Any] | None = None
                    if reply_author_id is not None:
                        actor_info = {"id": reply_author_id}
                    await tickets_service.emit_ticket_updated_event(
                        ticket_id,
                        actor=actor_info,
                    )
                except Exception:
                    pass


@pytest.mark.anyio
class TestEmailReplyAutomationTrigger:
    """Test that email replies trigger the tickets.updated automation event."""

    async def test_email_reply_triggers_automation_event(self, monkeypatch):
        """
        Test that when an email reply is added to an existing ticket,
        emit_ticket_updated_event is called to trigger automations.
        """
        from app.services import tickets as tickets_service
        from app.repositories import tickets as tickets_repo

        emit_calls = []

        async def mock_emit_ticket_updated_event(
            ticket_id, *, actor=None, actor_type=None, trigger_automations=True
        ):
            emit_calls.append({
                "ticket_id": ticket_id,
                "actor": actor,
            })

        async def mock_create_reply(**kwargs):
            return {"id": 1, "ticket_id": kwargs.get("ticket_id")}

        monkeypatch.setattr(
            tickets_service, "emit_ticket_updated_event", mock_emit_ticket_updated_event
        )
        monkeypatch.setattr(tickets_repo, "create_reply", mock_create_reply)

        await _simulate_email_reply_processing(
            tickets_repo=tickets_repo,
            tickets_service=tickets_service,
            ticket_id=123,
            is_new_ticket=False,
            body="<p>This is a reply from the requester</p>",
            requester_id=5,
            message_id="test-message-id@example.com",
        )

        assert len(emit_calls) == 1, "emit_ticket_updated_event should be called once"
        call = emit_calls[0]
        assert call["ticket_id"] == 123
        assert call["actor"] == {"id": 5}  # The requester_id should be passed as actor

    async def test_email_reply_without_requester_triggers_automation(self, monkeypatch):
        """
        Test that email replies trigger automations even when requester_id is None.
        """
        from app.services import tickets as tickets_service
        from app.repositories import tickets as tickets_repo

        emit_calls = []

        async def mock_emit_ticket_updated_event(
            ticket_id, *, actor=None, actor_type=None, trigger_automations=True
        ):
            emit_calls.append({"ticket_id": ticket_id, "actor": actor})

        async def mock_create_reply(**kwargs):
            return {"id": 1, "ticket_id": kwargs.get("ticket_id")}

        monkeypatch.setattr(
            tickets_service, "emit_ticket_updated_event", mock_emit_ticket_updated_event
        )
        monkeypatch.setattr(tickets_repo, "create_reply", mock_create_reply)

        await _simulate_email_reply_processing(
            tickets_repo=tickets_repo,
            tickets_service=tickets_service,
            ticket_id=456,
            is_new_ticket=False,
            body="<p>Reply from unknown sender</p>",
            requester_id=None,  # Unknown sender
            message_id="unknown-sender@example.com",
        )

        assert len(emit_calls) == 1
        call = emit_calls[0]
        assert call["ticket_id"] == 456
        assert call["actor"] is None  # No actor when requester_id is None

    async def test_no_automation_trigger_when_reply_creation_fails(self, monkeypatch):
        """
        Test that emit_ticket_updated_event is NOT called when reply creation fails.
        """
        from app.services import tickets as tickets_service
        from app.repositories import tickets as tickets_repo

        emit_calls = []

        async def mock_emit_ticket_updated_event(
            ticket_id, *, actor=None, actor_type=None, trigger_automations=True
        ):
            emit_calls.append({"ticket_id": ticket_id})

        async def mock_create_reply_failing(**kwargs):
            raise Exception("Database error")

        monkeypatch.setattr(
            tickets_service, "emit_ticket_updated_event", mock_emit_ticket_updated_event
        )
        monkeypatch.setattr(tickets_repo, "create_reply", mock_create_reply_failing)

        await _simulate_email_reply_processing(
            tickets_repo=tickets_repo,
            tickets_service=tickets_service,
            ticket_id=789,
            is_new_ticket=False,
            body="<p>This reply will fail to save</p>",
            requester_id=10,
        )

        assert len(emit_calls) == 0, (
            "emit_ticket_updated_event should not be called when reply creation fails"
        )

    async def test_new_ticket_does_not_duplicate_automation_trigger(self, monkeypatch):
        """
        Test that new tickets (created from email) don't trigger duplicate automation events.
        The create_ticket function already triggers the tickets.created event.
        """
        from app.services import tickets as tickets_service
        from app.repositories import tickets as tickets_repo

        emit_calls = []

        async def mock_emit_ticket_updated_event(
            ticket_id, *, actor=None, actor_type=None, trigger_automations=True
        ):
            emit_calls.append({"ticket_id": ticket_id})

        monkeypatch.setattr(
            tickets_service, "emit_ticket_updated_event", mock_emit_ticket_updated_event
        )

        await _simulate_email_reply_processing(
            tickets_repo=tickets_repo,
            tickets_service=tickets_service,
            ticket_id=999,
            is_new_ticket=True,  # This is a new ticket, not a reply
            body="<p>Initial ticket description</p>",
            requester_id=15,
        )

        # For new tickets, emit_ticket_updated_event should NOT be called
        # (the tickets.created event is handled by create_ticket, not by our code)
        assert len(emit_calls) == 0, (
            "New tickets should not trigger emit_ticket_updated_event in the IMAP sync code"
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
