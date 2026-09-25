from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from app.services import email as email_service
from app.services import m365_direct_delivery


def test_deposit_message_creates_unread_inbox_item(monkeypatch):
    monkeypatch.setattr(m365_direct_delivery.m365, "acquire_access_token", AsyncMock(return_value="token"))
    post = AsyncMock(return_value={"id": "graph-message", "isDraft": True,
                                  "internetMessageId": "<message@example>"})
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
    assert result["state"] == "created"
    assert result["is_draft"] is True
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
    deposit = AsyncMock(return_value={"message_id": "graph-id", "operation": "inbox_item",
                                      "state": "created"})
    monkeypatch.setattr(m365_direct_delivery, "deliver_message", deposit)
    record = AsyncMock()
    monkeypatch.setattr("app.services.email_recipients.record_m365_operation", record)
    monkeypatch.setattr("app.services.email_recipients.get_m365_terminal_operation",
                        AsyncMock(return_value=None))

    sent, metadata = asyncio.run(email_service.send_email(
        subject="Assigned", recipients=["tech@example.com"], html_body="<p>Ticket</p>",
        ticket_reply_id=11,
    ))

    assert sent is True
    assert metadata["provider"] == "m365-direct-delivery"
    deposit.assert_awaited_once()
    record.assert_awaited_once_with(reply_id=11, recipient_email="tech@example.com",
        company_id=4, message_id="graph-id", operation="inbox_item", state="created")


def test_create_without_message_id_is_not_success(monkeypatch):
    monkeypatch.setattr(m365_direct_delivery.m365, "acquire_access_token",
                        AsyncMock(return_value="token"))
    monkeypatch.setattr(m365_direct_delivery.m365, "_graph_post", AsyncMock(return_value={"isDraft": True}))
    with pytest.raises(m365_direct_delivery.DirectDeliveryError, match="no message id"):
        asyncio.run(m365_direct_delivery.deposit_message(company_id=1, recipient="a@example.com",
                    subject="x", html_body="x"))


def test_sendmail_submits_from_configured_sender_with_attachments(monkeypatch):
    monkeypatch.setattr(m365_direct_delivery.m365, "acquire_access_token",
                        AsyncMock(return_value="token"))
    post = AsyncMock(return_value={})
    monkeypatch.setattr(m365_direct_delivery.m365, "_graph_post", post)
    result = asyncio.run(m365_direct_delivery.deliver_message(company_id=1,
        recipient="recipient@example.com", sender="sender@example.com", subject="x",
        html_body="x", mode="send_mail", attachments=[{"name": "a.txt", "content": b"ok"}]))
    assert result["state"] == "submitted"
    assert post.await_args.args[1].endswith("/users/sender%40example.com/sendMail")
    assert post.await_args.args[2]["message"]["attachments"][0]["contentBytes"] == "b2s="


def test_sendmail_failure_and_timeout_are_not_retried(monkeypatch):
    monkeypatch.setattr(m365_direct_delivery.m365, "acquire_access_token",
                        AsyncMock(return_value="token"))
    post = AsyncMock(side_effect=RuntimeError("timeout after submit"))
    monkeypatch.setattr(m365_direct_delivery.m365, "_graph_post", post)
    with pytest.raises(RuntimeError, match="timeout"):
        asyncio.run(m365_direct_delivery.deliver_message(company_id=1,
            recipient="recipient@example.com", sender="sender@example.com", subject="x",
            html_body="x", mode="send_mail"))
    assert post.await_count == 1


def test_attachment_limit_is_enforced_before_graph(monkeypatch):
    monkeypatch.setattr(m365_direct_delivery.m365, "acquire_access_token",
                        AsyncMock(return_value="token"))
    post = AsyncMock()
    monkeypatch.setattr(m365_direct_delivery.m365, "_graph_post", post)
    with pytest.raises(ValueError, match="3 MiB"):
        asyncio.run(m365_direct_delivery.deposit_message(company_id=1, recipient="a@example.com",
            subject="x", html_body="x", attachments=[{"content": b"x" * (3 * 1024 * 1024 + 1)}]))
    post.assert_not_awaited()


def test_read_response_missing_state_is_unknown(monkeypatch):
    monkeypatch.setattr(m365_direct_delivery.m365, "acquire_access_token",
                        AsyncMock(return_value="token"))
    monkeypatch.setattr(m365_direct_delivery.m365, "_graph_get", AsyncMock(return_value={}))
    with pytest.raises(m365_direct_delivery.DirectDeliveryError, match="read status"):
        asyncio.run(m365_direct_delivery.get_read_status(company_id=1,
                    recipient="a@example.com", message_id="moved-or-deleted"))
