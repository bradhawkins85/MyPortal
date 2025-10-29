from __future__ import annotations

from email.message import EmailMessage
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

import pytest

from app.services import imap


def test_extract_body_prefers_html_over_plain_text():
    message = EmailMessage()
    message["Subject"] = "Test"
    message.set_content("Plain text body")
    message.add_alternative("<p><strong>Formatted</strong> body</p>", subtype="html")

    body = imap._extract_body(message)

    assert "<p><strong>Formatted</strong> body</p>" in body
    assert "Plain text body" not in body


def test_extract_body_inlines_cid_images():
    root = MIMEMultipart("related")
    alternative = MIMEMultipart("alternative")
    alternative.attach(MIMEText("Plain text fallback", "plain"))
    cid = "image1"
    alternative.attach(MIMEText(f"<p><img src=\"cid:{cid}\" alt=\"Inline\"></p>", "html"))
    root.attach(alternative)

    image = MIMEImage(b"PNGDATA", _subtype="png")
    image.add_header("Content-ID", f"<{cid}>")
    image.add_header("Content-Disposition", "inline", filename="image.png")
    root.attach(image)

    body = imap._extract_body(root)

    assert "cid:image1" not in body
    assert "data:image/png;base64" in body


pytestmark = pytest.mark.anyio("asyncio")


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


async def test_resolve_ticket_entities_matches_company_and_staff(monkeypatch):
    async def fake_get_company_by_email_domain(domain: str):
        assert domain == "example.com"
        return {"id": 5}

    async def fake_get_staff_by_company_and_email(company_id: int, email: str):
        assert company_id == 5
        assert email == "user@example.com"
        return {"id": 42}

    async def fake_get_user_by_email(email: str):
        assert email == "user@example.com"
        return {"id": 77}

    monkeypatch.setattr(
        imap.company_repo,
        "get_company_by_email_domain",
        fake_get_company_by_email_domain,
    )
    monkeypatch.setattr(
        imap.staff_repo,
        "get_staff_by_company_and_email",
        fake_get_staff_by_company_and_email,
    )
    monkeypatch.setattr(
        imap.users_repo,
        "get_user_by_email",
        fake_get_user_by_email,
    )

    company_id, requester_id = await imap._resolve_ticket_entities("User <user@example.com>")

    assert company_id == 5
    assert requester_id == 77


async def test_resolve_ticket_entities_matches_company_without_staff(monkeypatch):
    async def fake_get_company_by_email_domain(domain: str):
        assert domain == "example.com"
        return {"id": "7"}

    async def fake_get_staff_by_company_and_email(company_id: int, email: str):
        assert company_id == 7
        assert email == "sender@example.com"
        return None

    async def fake_get_user_by_email(email: str):
        assert email == "sender@example.com"
        return None

    monkeypatch.setattr(
        imap.company_repo,
        "get_company_by_email_domain",
        fake_get_company_by_email_domain,
    )
    monkeypatch.setattr(
        imap.staff_repo,
        "get_staff_by_company_and_email",
        fake_get_staff_by_company_and_email,
    )
    monkeypatch.setattr(
        imap.users_repo,
        "get_user_by_email",
        fake_get_user_by_email,
    )

    company_id, requester_id = await imap._resolve_ticket_entities("Sender <sender@example.com>")

    assert company_id == 7
    assert requester_id is None


async def test_resolve_ticket_entities_falls_back_to_account_company(monkeypatch):
    async def fake_get_company_by_email_domain(domain: str):
        return None

    async def fake_get_staff_by_company_and_email(company_id: int, email: str):
        if email == "help@tenant.com":
            return {"id": "81"}
        return None

    async def fake_get_user_by_email(email: str):
        if email == "help@tenant.com":
            return {"id": "81"}
        return None

    monkeypatch.setattr(
        imap.company_repo,
        "get_company_by_email_domain",
        fake_get_company_by_email_domain,
    )
    monkeypatch.setattr(
        imap.staff_repo,
        "get_staff_by_company_and_email",
        fake_get_staff_by_company_and_email,
    )
    monkeypatch.setattr(
        imap.users_repo,
        "get_user_by_email",
        fake_get_user_by_email,
    )

    company_id, requester_id = await imap._resolve_ticket_entities(
        "Support <help@tenant.com>",
        default_company_id="11",
    )

    assert company_id == 11
    assert requester_id == 81


async def test_resolve_ticket_entities_handles_staff_without_user(monkeypatch):
    async def fake_get_company_by_email_domain(domain: str):
        assert domain == "example.com"
        return {"id": 15}

    async def fake_get_staff_by_company_and_email(company_id: int, email: str):
        assert company_id == 15
        assert email == "member@example.com"
        return {"id": 123}

    async def fake_get_user_by_email(email: str):
        assert email == "member@example.com"
        return None

    assert (11, "help@tenant.com") in checked


async def test_sync_account_does_not_mark_as_read_on_ticket_failure(monkeypatch):
    recorded_messages: list[dict[str, object]] = []
    account_updates: list[tuple[int, dict[str, object]]] = []

    async def fake_get_module(slug: str, *, redact: bool = True):
        assert slug == "imap"
        return {"enabled": True}

    async def fake_get_account(account_id: int):
        assert account_id == 7
        return {
            "id": account_id,
            "host": "mail.example.com",
            "port": 993,
            "username": "inbox",
            "password_encrypted": "encrypted",
            "folder": "INBOX",
            "process_unread_only": True,
            "mark_as_read": True,
            "active": True,
        }

    async def fake_get_message(account_id: int, uid: str):
        assert account_id == 7
        assert uid == "1"
        return None

    async def fake_upsert_message(**payload):
        recorded_messages.append(payload)

    async def fake_update_account(account_id: int, **payload):
        account_updates.append((account_id, payload))
        return None

    def fake_decrypt_secret(value: str) -> str:
        assert value == "encrypted"
        return "password"

    async def fake_create_ticket(**_payload):
        raise RuntimeError("Ticket creation failed")

    async def fake_get_company_by_email_domain(domain: str):
        return None

    async def fake_get_staff_by_company_and_email(company_id: int, email: str):
        return None

    class FakeMailbox:
        def __init__(self):
            self.commands: list[tuple[str, tuple[object, ...]]] = []
            self.stored_flags: list[tuple[object, ...]] = []
            self.logged_out = False
            self.selected = None

        def login(self, username: str, password: str) -> None:
            assert username == "inbox"
            assert password == "password"

        def select(self, folder: str, readonly: bool = False) -> tuple[str, list[bytes]]:
            self.selected = (folder, readonly)
            return "OK", []

        def uid(self, command: str, *args):
            self.commands.append((command, args))
            if command == "search":
                assert args == (None, "UNSEEN")
                return "OK", [b"1"]
            if command == "fetch":
                assert args[1] == "(BODY.PEEK[])"
                raw_message = (
                    b"From: Sender <sender@example.com>\r\n"
                    b"Subject: Help\r\n"
                    b"Message-ID: <msg-1@example.com>\r\n"
                    b"\r\n"
                    b"Body"
                )
                return "OK", [(b"1 (RFC822 {5})", raw_message)]
            if command == "store":
                self.stored_flags.append(args)
                return "OK", []
            raise AssertionError(f"Unexpected command {command!r}")

        def logout(self) -> None:
            self.logged_out = True

    mailboxes: list[FakeMailbox] = []

    def fake_imap4_ssl(host: str, port: int):
        assert host == "mail.example.com"
        assert port == 993
        mailbox = FakeMailbox()
        mailboxes.append(mailbox)
        return mailbox

    monkeypatch.setattr(imap.modules_service, "get_module", fake_get_module)
    monkeypatch.setattr(imap.imap_repo, "get_account", fake_get_account)
    monkeypatch.setattr(imap.imap_repo, "get_message", fake_get_message)
    monkeypatch.setattr(imap.imap_repo, "upsert_message", fake_upsert_message)
    monkeypatch.setattr(imap.imap_repo, "update_account", fake_update_account)
    monkeypatch.setattr(imap, "decrypt_secret", fake_decrypt_secret)
    monkeypatch.setattr(imap.tickets_service, "create_ticket", fake_create_ticket)
    monkeypatch.setattr(
        imap.company_repo,
        "get_company_by_email_domain",
        fake_get_company_by_email_domain,
    )
    monkeypatch.setattr(
        imap.staff_repo,
        "get_staff_by_company_and_email",
        fake_get_staff_by_company_and_email,
    )
    monkeypatch.setattr(
        imap.users_repo,
        "get_user_by_email",
        fake_get_user_by_email,
    )

    company_id, requester_id = await imap._resolve_ticket_entities("Member <member@example.com>")

    assert company_id == 15
    assert requester_id is None
    monkeypatch.setattr(imap.imaplib, "IMAP4_SSL", fake_imap4_ssl)

    result = await imap.sync_account(7)

    assert result["status"] == "completed_with_errors"
    assert result["processed"] == 0
    assert result["errors"] and result["errors"][0]["uid"] == "1"

    assert mailboxes, "Expected IMAP connection"
    mailbox = mailboxes[0]
    fetch_commands = [cmd for cmd in mailbox.commands if cmd[0] == "fetch"]
    assert fetch_commands, "Expected fetch command"
    assert fetch_commands[0][1][1] == "(BODY.PEEK[])"
    assert mailbox.stored_flags == []

    assert recorded_messages
    assert recorded_messages[0]["status"] == "error"
    assert account_updates and account_updates[0][0] == 7


async def test_sync_account_skips_when_restart_pending(monkeypatch):
    monkeypatch.setattr(imap.system_state, "is_restart_pending", lambda: True)

    result = await imap.sync_account(9)

    assert result == {"status": "skipped", "reason": "pending_restart"}
