from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

from app.services import email as email_service
from app.services import m365_direct_delivery


def test_deposit_message_creates_unread_inbox_item(monkeypatch):
    monkeypatch.setattr(m365_direct_delivery.m365, "acquire_access_token", AsyncMock(return_value="token"))
    post = AsyncMock(return_value={"id": "graph-message", "internetMessageId": "<message@example>"})
    monkeypatch.setattr(m365_direct_delivery.m365, "_graph_post", post)

    result = asyncio.run(m365_direct_delivery.deposit_message(
        company_id=7,
        recipient="tech+alerts@example.com",
        subject="Ticket updated",
        html_body="<p>Update</p>",
        sender="portal@example.com",
        reply_to="support@example.com",
    ))

    assert result["message_id"] == "graph-message"
    url = post.await_args.args[1]
    payload = post.await_args.args[2]
    assert "/users/tech%2Balerts%40example.com/mailFolders/inbox/messages" in url
    assert payload["isRead"] is False
    assert payload["from"]["emailAddress"]["address"] == "portal@example.com"


def test_send_email_uses_direct_delivery_without_smtp(monkeypatch):
    class Settings:
        smtp_host = ""

    monkeypatch.setattr(email_service, "get_settings", lambda: Settings())
    monkeypatch.setattr("app.repositories.email_blocklist.filter_allowed", AsyncMock(return_value=(["tech@example.com"], [])))
    monkeypatch.setattr(
        "app.services.modules.get_module",
        AsyncMock(return_value={
            "enabled": True,
            "settings": {"company_id": 4, "recipient_domains": ["example.com"]},
        }),
    )
    deposit = AsyncMock(return_value={"message_id": "graph-id"})
    monkeypatch.setattr(m365_direct_delivery, "deposit_message", deposit)
    record = AsyncMock()
    monkeypatch.setattr("app.services.email_recipients.record_m365_delivery", record)

    sent, metadata = asyncio.run(email_service.send_email(
        subject="Assigned", recipients=["tech@example.com"], html_body="<p>Ticket</p>",
        ticket_reply_id=11,
    ))

    assert sent is True
    assert metadata["provider"] == "m365-direct-delivery"
    deposit.assert_awaited_once()
    record.assert_awaited_once_with(
        reply_id=11, recipient_email="tech@example.com", company_id=4, message_id="graph-id"
    )
