"""Pasted reply images must be embedded in outgoing email as cid: parts."""

import asyncio

import pytest

from app.services import email as email_service
from app.services import m365_direct_delivery
from app.services import modules as modules_service
from app.services import smtp2go
from app.services import ticket_attachments as attachments_service
from app.services import webhook_monitor

PNG_BYTES = b"\x89PNG\r\n\x1a\nfake"


@pytest.fixture
def stored_attachments(monkeypatch, tmp_path):
    records = {
        7: {"id": 7, "ticket_id": 42, "filename": "a.png", "mime_type": "image/png"},
        8: {"id": 8, "ticket_id": 99, "filename": "b.png", "mime_type": "image/png"},
    }
    for record in records.values():
        (tmp_path / record["filename"]).write_bytes(PNG_BYTES)

    async def fake_get_attachment(attachment_id):
        return records.get(attachment_id)

    monkeypatch.setattr(attachments_service.attachments_repo, "get_attachment", fake_get_attachment)
    monkeypatch.setattr(
        attachments_service, "attachment_path", lambda record: tmp_path / record["filename"]
    )
    return records


def test_embed_rewrites_same_ticket_images_only(stored_attachments):
    html = (
        '<p>See</p><img src="/api/tickets/42/attachments/7/download" alt="x">'
        "<img src='https://portal.example.com/api/tickets/42/attachments/7/download'>"
        '<img src="/api/tickets/99/attachments/8/download">'
    )

    rewritten, inlines = asyncio.run(
        attachments_service.embed_ticket_images_for_email(42, html)
    )

    assert rewritten == (
        '<p>See</p><img src="cid:ticket-image-7.png" alt="x">'
        "<img src='cid:ticket-image-7.png'>"
        '<img src="/api/tickets/99/attachments/8/download">'
    )
    assert len(inlines) == 1
    assert inlines[0]["content"] == PNG_BYTES
    assert inlines[0]["content_id"] == "ticket-image-7.png"
    assert inlines[0]["mime_type"] == "image/png"


def test_invoke_smtp_passes_inline_images(monkeypatch, stored_attachments):
    captured = {}

    async def fake_send_email(**kwargs):
        captured.update(kwargs)
        return True, {"id": 1}

    async def fake_event(**kwargs):
        return {"id": 1}

    async def fake_record(*args, **kwargs):
        return {"id": 1, "status": "succeeded"}

    monkeypatch.setattr(email_service, "send_email", fake_send_email)
    monkeypatch.setattr(webhook_monitor, "create_manual_event", fake_event)
    monkeypatch.setattr(modules_service, "_record_success", fake_record)
    monkeypatch.setattr(modules_service, "_record_failure", fake_record)

    payload = {
        "to": ["customer@example.com"],
        "subject": "RE: Ticket",
        "html": '<img src="/api/tickets/42/attachments/7/download">',
        "context": {"ticket": {"id": 42}, "reply": {"id": 5, "attachments": []}},
    }
    asyncio.run(modules_service._invoke_smtp({}, payload))

    assert captured["html_body"] == '<img src="cid:ticket-image-7.png">'
    assert [item["content_id"] for item in captured["attachments"]] == ["ticket-image-7.png"]


def test_smtp2go_inline_payload():
    inlines = smtp2go._normalise_inline_payloads(
        [{"content_id": "ticket-image-7.png", "content": PNG_BYTES, "mime_type": "image/png"}]
    )
    assert inlines == [
        {
            "filename": "ticket-image-7.png",
            "fileblob": "iVBORw0KGgpmYWtl",
            "mimetype": "image/png",
        }
    ]


def test_m365_marks_inline_attachments():
    message = m365_direct_delivery._message(
        recipient="a@example.com",
        subject="s",
        html_body='<img src="cid:ticket-image-7.png">',
        text_body=None,
        sender=None,
        reply_to=None,
        attachments=[
            {"filename": "ticket-image-7.png", "content": PNG_BYTES,
             "mime_type": "image/png", "content_id": "ticket-image-7.png"}
        ],
    )
    attachment = message["attachments"][0]
    assert attachment["isInline"] is True
    assert attachment["contentId"] == "ticket-image-7.png"


def test_smtp_relay_builds_multipart_related(monkeypatch):
    sent = {}

    class DummySMTP:
        def __init__(self, *args, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def ehlo(self):
            pass

        def starttls(self, **kwargs):
            pass

        def login(self, *args):
            pass

        def send_message(self, message):
            sent["message"] = message

    class Settings:
        smtp_host = "smtp.example.com"
        smtp_port = 587
        smtp_user = None
        smtp_password = None
        smtp_use_tls = False
        smtp_from = "noreply@example.com"
        outbound_audit_bcc = None
        portal_url = None

    async def no_module(*args, **kwargs):
        return None

    async def allow_all(addresses):
        return addresses, []

    async def fake_event(**kwargs):
        return {"id": 1}

    async def fake_record(*args, **kwargs):
        return {"id": 1}

    from app.repositories import email_blocklist as email_blocklist_repo
    from app.services import modules as module_service

    monkeypatch.setattr(email_service, "get_settings", lambda: Settings())
    monkeypatch.setattr(email_service.smtplib, "SMTP", DummySMTP)
    monkeypatch.setattr(module_service, "get_module", no_module)
    monkeypatch.setattr(email_blocklist_repo, "filter_allowed", allow_all)
    monkeypatch.setattr(webhook_monitor, "create_manual_event", fake_event)
    monkeypatch.setattr(webhook_monitor, "record_manual_success", fake_record, raising=False)
    monkeypatch.setattr(webhook_monitor, "record_manual_failure", fake_record, raising=False)

    asyncio.run(
        email_service.send_email(
            subject="s",
            recipients=["a@example.com"],
            html_body='<img src="cid:ticket-image-7.png">',
            text_body="plain",
            attachments=[
                {"filename": "doc.pdf", "content": b"%PDF", "mime_type": "application/pdf"},
                {"filename": "ticket-image-7.png", "content": PNG_BYTES,
                 "mime_type": "image/png", "content_id": "ticket-image-7.png"},
            ],
        )
    )

    message = sent["message"]
    content_types = [part.get_content_type() for part in message.walk()]
    assert "multipart/related" in content_types
    image_part = next(part for part in message.walk() if part.get_content_type() == "image/png")
    assert image_part["Content-ID"] == "<ticket-image-7.png>"
    assert image_part.get_content_disposition() == "inline"
    assert "application/pdf" in content_types
