from __future__ import annotations

import json

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
        assert domain == "tenant.com"
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
    checked: set[tuple[int, str]] = set()

    async def fake_get_company_by_email_domain(domain: str):
        assert domain == "tenant.com"
        return None

    async def fake_get_staff_by_company_and_email(company_id: int, email: str):
        checked.add((company_id, email))
        return {"id": 123}

    async def fake_get_user_by_email(email: str):
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

    await imap._resolve_ticket_entities("Support <help@tenant.com>", default_company_id="11")

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
        assert domain == "example.com"
        return {"id": 15}

    async def fake_get_staff_by_company_and_email(company_id: int, email: str):
        return None

    async def fake_get_user_by_email(email: str):
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
                assert args[1] == "(BODY.PEEK[] FLAGS)"
                raw_message = (
                    b"From: Sender <sender@example.com>\r\n"
                    b"Subject: Help\r\n"
                    b"Message-ID: <msg-1@example.com>\r\n"
                    b"\r\n"
                    b"Body"
                )
                return "OK", [(b"1 (FLAGS () BODY[] {5})", raw_message)]
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
    assert fetch_commands[0][1][1] == "(BODY.PEEK[] FLAGS)"
    assert mailbox.stored_flags == []

    assert recorded_messages
    assert recorded_messages[0]["status"] == "error"
    assert account_updates and account_updates[0][0] == 7


async def test_sync_account_skips_filtered_message(monkeypatch):
    async def fake_get_module(slug: str, *, redact: bool = True):
        assert slug == "imap"
        return {"enabled": True}

    monkeypatch.setattr(imap.modules_service, "get_module", fake_get_module)

    async def fake_get_account(account_id: int):
        assert account_id == 4
        return {
            "id": account_id,
            "host": "mail.example.com",
            "port": 993,
            "username": "filter",
            "password_encrypted": "encrypted",
            "folder": "INBOX",
            "process_unread_only": False,
            "mark_as_read": False,
            "active": True,
            "filter_query": {"field": "subject", "contains": "urgent"},
        }

    class FilterMailbox:
        def __init__(self) -> None:
            self.selected: tuple[str, bool] | None = None
            self.logged_out = False
            self.commands: list[tuple[str, tuple[object, ...]]] = []

        def login(self, username: str, password: str) -> None:
            assert username == "filter"
            assert password == "password"

        def select(self, folder: str, readonly: bool = False):
            self.selected = (folder, readonly)
            return "OK", []

        def uid(self, command: str, *args):
            self.commands.append((command, args))
            if command == "search":
                assert args == (None, "ALL")
                return "OK", [b"1"]
            if command == "fetch":
                assert args[1] == "(BODY.PEEK[] FLAGS)"
                message = (
                    b"From: Ops <ops@example.com>\r\n"
                    b"Subject: Routine update\r\n"
                    b"Message-ID: <msg@example.com>\r\n"
                    b"\r\n"
                    b"Body"
                )
                return "OK", [(b"1 (FLAGS (\\Seen) BODY[] {5})", message)]
            raise AssertionError(f"Unexpected command {command!r}")

        def logout(self) -> None:
            self.logged_out = True

    mailbox = FilterMailbox()
    monkeypatch.setattr(imap.imaplib, "IMAP4_SSL", lambda host, port: mailbox)
    monkeypatch.setattr(imap.imap_repo, "get_account", fake_get_account)
    async def fake_get_message(*_args, **_kwargs):
        return None

    async def fake_upsert_message(**_kwargs):
        pytest.fail("message recorded")

    async def fake_update_account(*_args, **_kwargs):
        return None

    monkeypatch.setattr(imap.imap_repo, "get_message", fake_get_message)
    monkeypatch.setattr(imap.imap_repo, "upsert_message", fake_upsert_message)
    monkeypatch.setattr(imap.imap_repo, "update_account", fake_update_account)
    monkeypatch.setattr(imap, "decrypt_secret", lambda value: "password")

    async def fake_create_ticket(**_kwargs):
        pytest.fail("ticket created")

    monkeypatch.setattr(imap.tickets_service, "create_ticket", fake_create_ticket)

    result = await imap.sync_account(4)

    assert result == {"status": "succeeded", "processed": 0, "errors": []}
    assert mailbox.logged_out
    fetch_commands = [cmd for cmd in mailbox.commands if cmd[0] == "fetch"]
    assert fetch_commands and fetch_commands[0][1][1] == "(BODY.PEEK[] FLAGS)"


async def test_sync_account_skips_when_restart_pending(monkeypatch):
    monkeypatch.setattr(imap.system_state, "is_restart_pending", lambda: True)

    result = await imap.sync_account(9)

    assert result == {"status": "skipped", "reason": "pending_restart"}


async def test_clone_account_creates_unique_copy(monkeypatch):
    original_account = {
        "id": 5,
        "name": "Support",
        "host": "mail.example.com",
        "port": 993,
        "username": "support",
        "password_encrypted": "encrypted",
        "folder": "INBOX",
        "schedule_cron": "*/10 * * * *",
        "process_unread_only": 1,
        "mark_as_read": 0,
        "active": 1,
        "company_id": 17,
        "priority": 10,
        "filter_query": {"field": "subject", "contains": "urgent"},
    }
    existing_accounts = [
        original_account,
        {"id": 6, "name": "Support (copy)", "priority": 10},
    ]
    created_account = dict(original_account)
    created_account.update({"id": 11, "name": "Support (copy 2)", "priority": 10})
    create_calls: list[dict[str, object]] = []

    async def fake_get_account(account_id: int):
        assert account_id == 5
        return original_account

    async def fake_list_accounts():
        return existing_accounts

    async def fake_create_account(**payload):
        create_calls.append(payload)
        assert payload["name"] == "Support (copy 2)"
        assert payload["password_encrypted"] == "encrypted"
        assert payload["priority"] == 10
        expected_filter = json.dumps(
            {"field": "subject", "contains": "urgent"}, separators=(",", ":"), sort_keys=True
        )
        assert payload.get("filter_query") == expected_filter
        return created_account

    async def fake_ensure_task(account: dict[str, object]):
        return account

    monkeypatch.setattr(imap.imap_repo, "get_account", fake_get_account)
    monkeypatch.setattr(imap.imap_repo, "list_accounts", fake_list_accounts)
    monkeypatch.setattr(imap.imap_repo, "create_account", fake_create_account)
    monkeypatch.setattr(imap, "_ensure_scheduled_task", fake_ensure_task)

    cloned = await imap.clone_account(5)

    assert cloned["name"] == "Support (copy 2)"
    assert cloned["priority"] == 10
    assert cloned["filter_query"] == {"field": "subject", "contains": "urgent"}
    assert create_calls


async def test_sync_all_active_respects_priority(monkeypatch):
    sync_order: list[int] = []

    async def fake_list_accounts():
        return [
            {"id": 3, "active": True, "priority": 50},
            {"id": 1, "active": False, "priority": 0},
            {"id": 2, "active": True, "priority": 5},
        ]

    async def fake_sync_account(account_id: int):
        sync_order.append(account_id)

    monkeypatch.setattr(imap.imap_repo, "list_accounts", fake_list_accounts)
    monkeypatch.setattr(imap, "sync_account", fake_sync_account)

    await imap.sync_all_active()

    assert sync_order == [2, 3]


async def test_is_email_address_known_returns_true_for_user_email(monkeypatch):
    async def fake_get_user_by_email(email: str):
        assert email == "user@example.com"
        return {"id": 123, "email": "user@example.com"}

    async def fake_list_staff_by_email(email: str):
        return []

    monkeypatch.setattr(imap.users_repo, "get_user_by_email", fake_get_user_by_email)
    monkeypatch.setattr(imap.staff_repo, "list_staff_by_email", fake_list_staff_by_email)

    result = await imap._is_email_address_known("user@example.com")

    assert result is True


async def test_is_email_address_known_returns_true_for_staff_email(monkeypatch):
    async def fake_get_user_by_email(email: str):
        assert email == "staff@example.com"
        return None

    async def fake_list_staff_by_email(email: str):
        assert email == "staff@example.com"
        return [{"id": 456, "email": "staff@example.com"}]

    monkeypatch.setattr(imap.users_repo, "get_user_by_email", fake_get_user_by_email)
    monkeypatch.setattr(imap.staff_repo, "list_staff_by_email", fake_list_staff_by_email)

    result = await imap._is_email_address_known("staff@example.com")

    assert result is True


async def test_is_email_address_known_returns_false_for_unknown_email(monkeypatch):
    async def fake_get_user_by_email(email: str):
        assert email == "unknown@example.com"
        return None

    async def fake_list_staff_by_email(email: str):
        assert email == "unknown@example.com"
        return []

    monkeypatch.setattr(imap.users_repo, "get_user_by_email", fake_get_user_by_email)
    monkeypatch.setattr(imap.staff_repo, "list_staff_by_email", fake_list_staff_by_email)

    result = await imap._is_email_address_known("unknown@example.com")

    assert result is False


async def test_is_email_address_known_returns_false_for_invalid_email(monkeypatch):
    result = await imap._is_email_address_known("not-an-email")

    assert result is False


async def test_is_email_address_known_returns_false_for_empty_email(monkeypatch):
    result = await imap._is_email_address_known("")

    assert result is False
