from __future__ import annotations

import json
from typing import Any
from urllib.parse import unquote

import pytest

from app.services import m365_mail


pytestmark = pytest.mark.anyio("asyncio")


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


# ---------------------------------------------------------------------------
# Filter context building
# ---------------------------------------------------------------------------


def test_build_filter_context_basic():
    """Verify filter context is built correctly from a Graph API message."""
    account: dict[str, Any] = {
        "id": 1,
        "name": "Test Mailbox",
        "company_id": 5,
    }
    graph_message: dict[str, Any] = {
        "id": "msg-001",
        "internetMessageId": "<msg001@example.com>",
        "subject": "Test Subject",
        "body": {"contentType": "html", "content": "<p>Hello</p>"},
        "from": {
            "emailAddress": {"name": "John Doe", "address": "john@example.com"}
        },
        "toRecipients": [
            {"emailAddress": {"name": "Support", "address": "support@contoso.com"}}
        ],
        "ccRecipients": [
            {"emailAddress": {"name": "Manager", "address": "mgr@contoso.com"}}
        ],
        "bccRecipients": [],
        "replyTo": [],
        "isRead": False,
        "internetMessageHeaders": [
            {"name": "X-Custom", "value": "test-value"},
        ],
    }

    context = m365_mail._build_filter_context(
        account=account,
        graph_message=graph_message,
        subject="Test Subject",
        body="<p>Hello</p>",
        from_address="John Doe <john@example.com>",
        folder="Inbox",
        is_unread=True,
        message_id="<msg001@example.com>",
    )

    assert context["account"]["id"] == 1
    assert context["account"]["name"] == "Test Mailbox"
    assert context["account"]["company_id"] == 5
    assert context["mailbox"]["folder"] == "Inbox"
    assert context["subject"] == "Test Subject"
    assert context["body"] == "<p>Hello</p>"
    assert context["message_id"] == "<msg001@example.com>"
    assert context["from"]["address"] == "john@example.com"
    assert context["from"]["domain"] == "example.com"
    assert "john@example.com" in context["from"]["addresses"]
    assert context["to"] == ["support@contoso.com"]
    assert context["cc"] == ["mgr@contoso.com"]
    assert context["bcc"] == []
    assert context["is_unread"] is True
    assert context["is_read"] is False
    assert context["headers"]["x-custom"] == "test-value"


def test_build_filter_context_empty_recipients():
    """Filter context handles empty recipient lists."""
    account: dict[str, Any] = {"id": 2, "name": "Empty", "company_id": None}
    graph_message: dict[str, Any] = {
        "toRecipients": [],
        "ccRecipients": [],
        "bccRecipients": [],
        "replyTo": [],
    }

    context = m365_mail._build_filter_context(
        account=account,
        graph_message=graph_message,
        subject="",
        body="",
        from_address="",
        folder="Inbox",
        is_unread=False,
        message_id="",
    )

    assert context["to"] == []
    assert context["cc"] == []
    assert context["bcc"] == []
    assert context["is_unread"] is False
    assert context["is_read"] is True


# ---------------------------------------------------------------------------
# Account management
# ---------------------------------------------------------------------------


async def test_create_account_accepts_none_company(monkeypatch):
    """create_account allows company_id to be None."""
    with pytest.raises(ValueError, match="User principal name"):
        await m365_mail.create_account({"name": "Test", "company_id": None})


async def test_create_account_requires_upn(monkeypatch):
    """create_account raises ValueError when user_principal_name is missing."""

    # Mock out the normalise/filter functions enough for the check
    with pytest.raises(ValueError, match="User principal name"):
        await m365_mail.create_account({
            "name": "Test",
            "company_id": 1,
            "user_principal_name": "",
        })


async def test_update_account_not_found(monkeypatch):
    """update_account raises ValueError for missing account."""

    async def fake_get_account(account_id: int):
        return None

    monkeypatch.setattr(m365_mail.mail_repo, "get_account", fake_get_account)

    with pytest.raises(ValueError, match="Account not found"):
        await m365_mail.update_account(999, {"name": "Updated"})


async def test_update_account_clears_company(monkeypatch):
    """update_account allows clearing company_id to None."""

    async def fake_get_account(account_id: int):
        return {
            "id": 1,
            "name": "Test",
            "company_id": 5,
            "user_principal_name": "test@example.com",
        }

    updated_fields: dict = {}

    async def fake_update_account(account_id: int, **fields):
        updated_fields.update(fields)
        return {
            "id": 1,
            "name": "Test",
            "company_id": None,
            "user_principal_name": "test@example.com",
        }

    async def fake_ensure_scheduled_task(account):
        return account

    monkeypatch.setattr(m365_mail.mail_repo, "get_account", fake_get_account)
    monkeypatch.setattr(m365_mail.mail_repo, "update_account", fake_update_account)
    monkeypatch.setattr(m365_mail, "_ensure_scheduled_task", fake_ensure_scheduled_task)

    result = await m365_mail.update_account(1, {"company_id": ""})
    assert updated_fields.get("company_id") is None


async def test_clone_account_not_found(monkeypatch):
    """clone_account raises LookupError for missing account."""

    async def fake_get_account(account_id: int):
        return None

    monkeypatch.setattr(m365_mail.mail_repo, "get_account", fake_get_account)

    with pytest.raises(LookupError, match="Account not found"):
        await m365_mail.clone_account(999)


async def test_delete_account_no_op_for_missing(monkeypatch):
    """delete_account is a no-op when the account doesn't exist."""

    async def fake_get_account(account_id: int):
        return None

    monkeypatch.setattr(m365_mail.mail_repo, "get_account", fake_get_account)

    # Should not raise
    await m365_mail.delete_account(999)


# ---------------------------------------------------------------------------
# Sync account - status checks
# ---------------------------------------------------------------------------


async def test_sync_account_skips_when_restart_pending(monkeypatch):
    """sync_account returns skipped status when restart is pending."""
    monkeypatch.setattr(m365_mail.system_state, "is_restart_pending", lambda: True)

    result = await m365_mail.sync_account(1)

    assert result["status"] == "skipped"
    assert result["reason"] == "pending_restart"


async def test_sync_account_skips_when_module_disabled(monkeypatch):
    """sync_account returns skipped when module is disabled."""
    monkeypatch.setattr(m365_mail.system_state, "is_restart_pending", lambda: False)

    async def fake_get_module(slug: str, *, redact: bool = True):
        return {"enabled": False}

    monkeypatch.setattr(m365_mail.modules_service, "get_module", fake_get_module)

    result = await m365_mail.sync_account(1)

    assert result["status"] == "skipped"
    assert result["reason"] == "Module disabled"


async def test_sync_account_skips_inactive_account(monkeypatch):
    """sync_account returns skipped for inactive accounts."""
    monkeypatch.setattr(m365_mail.system_state, "is_restart_pending", lambda: False)

    async def fake_get_module(slug: str, *, redact: bool = True):
        return {"enabled": True}

    async def fake_get_account(account_id: int):
        return {"id": 1, "active": False}

    monkeypatch.setattr(m365_mail.modules_service, "get_module", fake_get_module)
    monkeypatch.setattr(m365_mail.mail_repo, "get_account", fake_get_account)

    result = await m365_mail.sync_account(1)

    assert result["status"] == "skipped"
    assert result["reason"] == "Account inactive"


async def test_sync_account_error_no_credentials(monkeypatch):
    """sync_account returns error when no company is linked and no M365 credentials exist."""
    monkeypatch.setattr(m365_mail.system_state, "is_restart_pending", lambda: False)

    async def fake_get_module(slug: str, *, redact: bool = True):
        return {"enabled": True}

    async def fake_get_account(account_id: int):
        return {
            "id": 1,
            "active": True,
            "company_id": None,
            "user_principal_name": "user@example.com",
        }

    async def fake_list_provisioned():
        return set()

    monkeypatch.setattr(m365_mail.modules_service, "get_module", fake_get_module)
    monkeypatch.setattr(m365_mail.mail_repo, "get_account", fake_get_account)
    monkeypatch.setattr(m365_mail.m365_repo, "list_provisioned_company_ids", fake_list_provisioned)

    result = await m365_mail.sync_account(1)

    assert result["status"] == "error"
    assert "credentials" in result["error"].lower()


async def test_sync_account_uses_provisioned_company_when_none(monkeypatch):
    """sync_account uses a provisioned company for auth when account has no company_id."""
    monkeypatch.setattr(m365_mail.system_state, "is_restart_pending", lambda: False)

    async def fake_get_module(slug: str, *, redact: bool = True):
        return {"enabled": True}

    async def fake_get_account(account_id: int):
        return {
            "id": 1,
            "active": True,
            "company_id": None,
            "user_principal_name": "user@example.com",
            "folder": "Inbox",
            "process_unread_only": True,
            "mark_as_read": False,
            "filter_query": None,
            "sync_known_only": False,
        }

    async def fake_list_provisioned():
        return {10, 20}

    acquired_company_ids: list[int] = []

    async def fake_acquire_token(company_id, **kwargs):
        acquired_company_ids.append(company_id)
        return "fake-token"

    async def fake_graph_get(access_token: str, url: str):
        return {"value": []}

    async def fake_update_account(account_id, **fields):
        return None

    monkeypatch.setattr(m365_mail.modules_service, "get_module", fake_get_module)
    monkeypatch.setattr(m365_mail.mail_repo, "get_account", fake_get_account)
    monkeypatch.setattr(m365_mail.m365_repo, "list_provisioned_company_ids", fake_list_provisioned)
    monkeypatch.setattr(m365_mail.m365_service, "acquire_access_token", fake_acquire_token)
    monkeypatch.setattr(m365_mail, "_graph_get", fake_graph_get)
    monkeypatch.setattr(m365_mail.mail_repo, "update_account", fake_update_account)

    result = await m365_mail.sync_account(1)

    assert result["status"] == "succeeded"
    assert acquired_company_ids == [10]  # Should pick min(provisioned)


async def test_sync_account_error_no_upn(monkeypatch):
    """sync_account returns error when UPN is not configured."""
    monkeypatch.setattr(m365_mail.system_state, "is_restart_pending", lambda: False)

    async def fake_get_module(slug: str, *, redact: bool = True):
        return {"enabled": True}

    async def fake_get_account(account_id: int):
        return {
            "id": 1,
            "active": True,
            "company_id": 5,
            "user_principal_name": "",
        }

    monkeypatch.setattr(m365_mail.modules_service, "get_module", fake_get_module)
    monkeypatch.setattr(m365_mail.mail_repo, "get_account", fake_get_account)

    result = await m365_mail.sync_account(1)

    assert result["status"] == "error"
    assert "principal name" in result["error"].lower()


async def test_sync_account_error_on_token_failure(monkeypatch):
    """sync_account returns error when token acquisition fails."""
    monkeypatch.setattr(m365_mail.system_state, "is_restart_pending", lambda: False)

    async def fake_get_module(slug: str, *, redact: bool = True):
        return {"enabled": True}

    async def fake_get_account(account_id: int):
        return {
            "id": 1,
            "active": True,
            "company_id": 5,
            "user_principal_name": "user@example.com",
            "folder": "Inbox",
            "process_unread_only": True,
            "mark_as_read": True,
        }

    async def fake_acquire_token(company_id, **kwargs):
        raise RuntimeError("Token expired")

    async def fake_update_account(account_id, **fields):
        return None

    monkeypatch.setattr(m365_mail.modules_service, "get_module", fake_get_module)
    monkeypatch.setattr(m365_mail.mail_repo, "get_account", fake_get_account)
    monkeypatch.setattr(m365_mail.m365_service, "acquire_access_token", fake_acquire_token)
    monkeypatch.setattr(m365_mail.mail_repo, "update_account", fake_update_account)

    result = await m365_mail.sync_account(1)

    assert result["status"] == "error"
    assert "authenticate" in result["error"].lower()


async def test_sync_account_resolves_custom_folder(monkeypatch):
    """sync_account resolves non-default folder display names to folder IDs."""
    monkeypatch.setattr(m365_mail.system_state, "is_restart_pending", lambda: False)

    async def fake_get_module(slug: str, *, redact: bool = True):
        return {"enabled": True}

    async def fake_get_account(account_id: int):
        return {
            "id": 1,
            "active": True,
            "company_id": 5,
            "user_principal_name": "user@example.com",
            "folder": "Custom Folder",
            "process_unread_only": True,
            "mark_as_read": False,
            "filter_query": None,
            "sync_known_only": False,
        }

    async def fake_acquire_token(company_id, **kwargs):
        return "fake-access-token"

    graph_calls: list[str] = []

    async def fake_graph_get(access_token: str, url: str):
        graph_calls.append(url)
        if "mailFolders?" in url:
            decoded = unquote(url)
            assert "displayName eq 'Custom Folder'" in decoded
            return {"value": [{"id": "folder-id-123", "displayName": "Custom Folder"}]}
        assert "/mailFolders/folder-id-123/messages?" in url
        return {"value": []}

    async def fake_update_account(account_id: int, **fields):
        return None

    async def fake_get_message(account_id: int, message_uid: str):
        return None

    monkeypatch.setattr(m365_mail.modules_service, "get_module", fake_get_module)
    monkeypatch.setattr(m365_mail.mail_repo, "get_account", fake_get_account)
    monkeypatch.setattr(m365_mail.m365_service, "acquire_access_token", fake_acquire_token)
    monkeypatch.setattr(m365_mail, "_graph_get", fake_graph_get)
    monkeypatch.setattr(m365_mail.mail_repo, "update_account", fake_update_account)
    monkeypatch.setattr(m365_mail.mail_repo, "get_message", fake_get_message)

    result = await m365_mail.sync_account(1)

    assert result["status"] == "succeeded"
    assert any("mailFolders?" in call for call in graph_calls)
    assert any("/mailFolders/folder-id-123/messages?" in call for call in graph_calls)


async def test_sync_account_resolves_subfolder(monkeypatch):
    """sync_account resolves nested mailbox folders (e.g., Inbox/Subfolder)."""
    monkeypatch.setattr(m365_mail.system_state, "is_restart_pending", lambda: False)

    async def fake_get_module(slug: str, *, redact: bool = True):
        return {"enabled": True}

    async def fake_get_account(account_id: int):
        return {
            "id": 1,
            "active": True,
            "company_id": 5,
            "user_principal_name": "user@example.com",
            "folder": "Inbox/Support",
            "process_unread_only": True,
            "mark_as_read": False,
            "filter_query": None,
            "sync_known_only": False,
        }

    async def fake_acquire_token(company_id, **kwargs):
        return "fake-access-token"

    graph_calls: list[str] = []
    child_folder_requests: list[str] = []

    async def fake_graph_get(access_token: str, url: str):
        decoded = unquote(url)
        graph_calls.append(decoded)
        if "childFolders?" in decoded:
            child_folder_requests.append(decoded)
            return {"value": [{"id": "child-folder-id"}]}
        if "/mailFolders/child-folder-id/messages?" in decoded:
            return {"value": []}
        pytest.fail(f"Unexpected Graph call: {decoded}")

    async def fake_update_account(account_id: int, **fields):
        return None

    async def fake_get_message(account_id: int, message_uid: str):
        return None

    monkeypatch.setattr(m365_mail.modules_service, "get_module", fake_get_module)
    monkeypatch.setattr(m365_mail.mail_repo, "get_account", fake_get_account)
    monkeypatch.setattr(m365_mail.m365_service, "acquire_access_token", fake_acquire_token)
    monkeypatch.setattr(m365_mail, "_graph_get", fake_graph_get)
    monkeypatch.setattr(m365_mail.mail_repo, "update_account", fake_update_account)
    monkeypatch.setattr(m365_mail.mail_repo, "get_message", fake_get_message)

    result = await m365_mail.sync_account(1)

    assert result["status"] == "succeeded"
    assert graph_calls, "Expected at least one Graph request"
    assert child_folder_requests and child_folder_requests[0] == graph_calls[0]
    assert len(graph_calls) == 2, "Expected child folder lookup then messages fetch"
    assert "childFolders?" in graph_calls[0]
    assert "/mailFolders/child-folder-id/messages?" in graph_calls[1]
    assert any("/mailFolders/Inbox/childFolders?" in call for call in child_folder_requests)
    assert any("displayName eq 'Support'" in call for call in child_folder_requests)
    assert any("childFolders?" in call for call in graph_calls)
    assert any("/mailFolders/child-folder-id/messages?" in call for call in graph_calls)


async def test_sync_account_escapes_folder_name(monkeypatch):
    """sync_account escapes single quotes in folder display names."""
    monkeypatch.setattr(m365_mail.system_state, "is_restart_pending", lambda: False)

    async def fake_get_module(slug: str, *, redact: bool = True):
        return {"enabled": True}

    async def fake_get_account(account_id: int):
        return {
            "id": 1,
            "active": True,
            "company_id": 5,
            "user_principal_name": "user@example.com",
            "folder": "User's Folder",
            "process_unread_only": True,
            "mark_as_read": False,
            "filter_query": None,
            "sync_known_only": False,
        }

    async def fake_acquire_token(company_id, **kwargs):
        return "fake-access-token"

    async def fake_graph_get(access_token: str, url: str):
        decoded = unquote(url)
        if "mailFolders?" in decoded:
            assert "displayName eq 'User''s Folder'" in decoded
            return {"value": [{"id": "folder-id-456"}]}
        assert "/mailFolders/folder-id-456/messages?" in decoded
        return {"value": []}

    async def fake_update_account(account_id: int, **fields):
        return None

    async def fake_get_message(account_id: int, message_uid: str):
        return None

    monkeypatch.setattr(m365_mail.modules_service, "get_module", fake_get_module)
    monkeypatch.setattr(m365_mail.mail_repo, "get_account", fake_get_account)
    monkeypatch.setattr(m365_mail.m365_service, "acquire_access_token", fake_acquire_token)
    monkeypatch.setattr(m365_mail, "_graph_get", fake_graph_get)
    monkeypatch.setattr(m365_mail.mail_repo, "update_account", fake_update_account)
    monkeypatch.setattr(m365_mail.mail_repo, "get_message", fake_get_message)

    result = await m365_mail.sync_account(1)

    assert result["status"] == "succeeded"


async def test_sync_account_errors_when_folder_missing(monkeypatch):
    """sync_account reports an error when the folder cannot be resolved."""
    monkeypatch.setattr(m365_mail.system_state, "is_restart_pending", lambda: False)

    async def fake_get_module(slug: str, *, redact: bool = True):
        return {"enabled": True}

    async def fake_get_account(account_id: int):
        return {
            "id": 1,
            "active": True,
            "company_id": 5,
            "user_principal_name": "user@example.com",
            "folder": "MissingFolder",
            "process_unread_only": True,
            "mark_as_read": False,
            "filter_query": None,
            "sync_known_only": False,
        }

    async def fake_acquire_token(company_id, **kwargs):
        return "fake-access-token"

    async def fake_graph_get(access_token: str, url: str):
        if "mailFolders?" in url:
            return {"value": []}
        pytest.fail("Messages endpoint should not be called when folder is missing")

    async def fake_update_account(account_id: int, **fields):
        return None

    async def fake_get_message(account_id: int, message_uid: str):
        return None

    monkeypatch.setattr(m365_mail.modules_service, "get_module", fake_get_module)
    monkeypatch.setattr(m365_mail.mail_repo, "get_account", fake_get_account)
    monkeypatch.setattr(m365_mail.m365_service, "acquire_access_token", fake_acquire_token)
    monkeypatch.setattr(m365_mail, "_graph_get", fake_graph_get)
    monkeypatch.setattr(m365_mail.mail_repo, "update_account", fake_update_account)
    monkeypatch.setattr(m365_mail.mail_repo, "get_message", fake_get_message)

    result = await m365_mail.sync_account(1)

    assert result["status"] == "completed_with_errors"
    assert result["errors"]
    assert "missingfolder" in result["errors"][0]["error"].lower()


# ---------------------------------------------------------------------------
# Sync account - successful processing
# ---------------------------------------------------------------------------


async def test_sync_account_processes_messages(monkeypatch):
    """sync_account correctly processes messages from Graph API."""
    monkeypatch.setattr(m365_mail.system_state, "is_restart_pending", lambda: False)

    async def fake_get_module(slug: str, *, redact: bool = True):
        return {"enabled": True}

    async def fake_get_account(account_id: int):
        return {
            "id": 1,
            "active": True,
            "company_id": 5,
            "user_principal_name": "user@example.com",
            "folder": "Inbox",
            "process_unread_only": True,
            "mark_as_read": True,
            "filter_query": None,
            "sync_known_only": False,
        }

    async def fake_acquire_token(company_id, **kwargs):
        return "fake-access-token"

    graph_messages = {
        "value": [
            {
                "id": "msg-001",
                "internetMessageId": "<msg001@example.com>",
                "subject": "Test ticket",
                "body": {"contentType": "html", "content": "<p>Please help</p>"},
                "from": {
                    "emailAddress": {"name": "User", "address": "requester@example.com"}
                },
                "toRecipients": [
                    {"emailAddress": {"address": "support@contoso.com"}}
                ],
                "ccRecipients": [],
                "bccRecipients": [],
                "replyTo": [],
                "isRead": False,
                "receivedDateTime": "2026-01-15T10:30:00Z",
                "hasAttachments": False,
                "internetMessageHeaders": [],
            }
        ],
    }

    graph_call_count = 0

    async def fake_graph_get(access_token: str, url: str):
        nonlocal graph_call_count
        graph_call_count += 1
        return graph_messages

    async def fake_graph_patch(access_token: str, url: str, payload: dict):
        assert "isRead" in payload
        assert payload["isRead"] is True

    async def fake_get_message(account_id: int, message_uid: str):
        return None  # Not yet imported

    recorded_messages: list[dict] = []

    async def fake_upsert_message(**kwargs):
        recorded_messages.append(kwargs)

    async def fake_update_account(account_id: int, **fields):
        return None

    async def fake_resolve_ticket_entities(from_header, *, default_company_id=None):
        return 5, None  # company_id, requester_id

    async def fake_find_existing_ticket(subject, from_email, **kwargs):
        return None  # Create new ticket

    created_tickets: list[dict] = []

    async def fake_create_ticket(**kwargs):
        created_tickets.append(kwargs)
        return {"id": 100, "ticket_number": "T-100"}

    async def fake_refresh_ai_summary(ticket_id):
        pass

    async def fake_refresh_ai_tags(ticket_id):
        pass

    monkeypatch.setattr(m365_mail.modules_service, "get_module", fake_get_module)
    monkeypatch.setattr(m365_mail.mail_repo, "get_account", fake_get_account)
    monkeypatch.setattr(m365_mail.m365_service, "acquire_access_token", fake_acquire_token)
    monkeypatch.setattr(m365_mail, "_graph_get", fake_graph_get)
    monkeypatch.setattr(m365_mail, "_graph_patch", fake_graph_patch)
    monkeypatch.setattr(m365_mail.mail_repo, "get_message", fake_get_message)
    monkeypatch.setattr(m365_mail.mail_repo, "upsert_message", fake_upsert_message)
    monkeypatch.setattr(m365_mail.mail_repo, "update_account", fake_update_account)
    monkeypatch.setattr(m365_mail, "_resolve_ticket_entities", fake_resolve_ticket_entities)
    monkeypatch.setattr(m365_mail, "_find_existing_ticket_for_reply", fake_find_existing_ticket)
    monkeypatch.setattr(m365_mail.tickets_service, "create_ticket", fake_create_ticket)
    monkeypatch.setattr(m365_mail.tickets_service, "refresh_ticket_ai_summary", fake_refresh_ai_summary)
    monkeypatch.setattr(m365_mail.tickets_service, "refresh_ticket_ai_tags", fake_refresh_ai_tags)

    result = await m365_mail.sync_account(1)

    assert result["status"] == "succeeded"
    assert result["processed"] == 1
    assert len(created_tickets) == 1
    assert created_tickets[0]["subject"] == "Test ticket"
    assert created_tickets[0]["module_slug"] == "m365-mail"
    assert len(recorded_messages) == 1
    assert recorded_messages[0]["status"] == "imported"


async def test_sync_account_no_company_resolves_from_email(monkeypatch):
    """sync_account with no company_id resolves ticket entities from email domain only."""
    monkeypatch.setattr(m365_mail.system_state, "is_restart_pending", lambda: False)

    async def fake_get_module(slug: str, *, redact: bool = True):
        return {"enabled": True}

    async def fake_get_account(account_id: int):
        return {
            "id": 1,
            "active": True,
            "company_id": None,
            "user_principal_name": "user@example.com",
            "folder": "Inbox",
            "process_unread_only": True,
            "mark_as_read": False,
            "filter_query": None,
            "sync_known_only": False,
        }

    async def fake_list_provisioned():
        return {7}

    async def fake_acquire_token(company_id, **kwargs):
        return "fake-access-token"

    graph_messages = {
        "value": [
            {
                "id": "msg-002",
                "internetMessageId": "<msg002@example.com>",
                "subject": "Help request",
                "body": {"contentType": "html", "content": "<p>Need help</p>"},
                "from": {
                    "emailAddress": {"name": "Contact", "address": "contact@customer.com"}
                },
                "toRecipients": [],
                "ccRecipients": [],
                "bccRecipients": [],
                "replyTo": [],
                "isRead": False,
                "receivedDateTime": "2026-01-20T14:00:00Z",
                "hasAttachments": False,
                "internetMessageHeaders": [],
            }
        ],
    }

    async def fake_graph_get(access_token: str, url: str):
        return graph_messages

    async def fake_get_message(account_id: int, message_uid: str):
        return None

    recorded_messages: list[dict] = []

    async def fake_upsert_message(**kwargs):
        recorded_messages.append(kwargs)

    async def fake_update_account(account_id: int, **fields):
        return None

    resolve_calls: list[dict] = []

    async def fake_resolve_ticket_entities(from_header, *, default_company_id=None):
        resolve_calls.append({"from_header": from_header, "default_company_id": default_company_id})
        return 42, 99  # Resolved from email domain

    async def fake_find_existing_ticket(subject, from_email, **kwargs):
        return None

    created_tickets: list[dict] = []

    async def fake_create_ticket(**kwargs):
        created_tickets.append(kwargs)
        return {"id": 200, "ticket_number": "T-200"}

    async def fake_refresh_ai_summary(ticket_id):
        pass

    async def fake_refresh_ai_tags(ticket_id):
        pass

    monkeypatch.setattr(m365_mail.modules_service, "get_module", fake_get_module)
    monkeypatch.setattr(m365_mail.mail_repo, "get_account", fake_get_account)
    monkeypatch.setattr(m365_mail.m365_repo, "list_provisioned_company_ids", fake_list_provisioned)
    monkeypatch.setattr(m365_mail.m365_service, "acquire_access_token", fake_acquire_token)
    monkeypatch.setattr(m365_mail, "_graph_get", fake_graph_get)
    monkeypatch.setattr(m365_mail.mail_repo, "get_message", fake_get_message)
    monkeypatch.setattr(m365_mail.mail_repo, "upsert_message", fake_upsert_message)
    monkeypatch.setattr(m365_mail.mail_repo, "update_account", fake_update_account)
    monkeypatch.setattr(m365_mail, "_resolve_ticket_entities", fake_resolve_ticket_entities)
    monkeypatch.setattr(m365_mail, "_find_existing_ticket_for_reply", fake_find_existing_ticket)
    monkeypatch.setattr(m365_mail.tickets_service, "create_ticket", fake_create_ticket)
    monkeypatch.setattr(m365_mail.tickets_service, "refresh_ticket_ai_summary", fake_refresh_ai_summary)
    monkeypatch.setattr(m365_mail.tickets_service, "refresh_ticket_ai_tags", fake_refresh_ai_tags)

    result = await m365_mail.sync_account(1)

    assert result["status"] == "succeeded"
    assert result["processed"] == 1
    # Entity resolution should have been called with default_company_id=None
    assert len(resolve_calls) == 1
    assert resolve_calls[0]["default_company_id"] is None
    # Ticket should use the company resolved from email domain
    assert len(created_tickets) == 1
    assert created_tickets[0]["company_id"] == 42
    assert created_tickets[0]["requester_id"] == 99


async def test_sync_account_skips_already_imported(monkeypatch):
    """sync_account skips messages already imported."""
    monkeypatch.setattr(m365_mail.system_state, "is_restart_pending", lambda: False)

    async def fake_get_module(slug: str, *, redact: bool = True):
        return {"enabled": True}

    async def fake_get_account(account_id: int):
        return {
            "id": 1,
            "active": True,
            "company_id": 5,
            "user_principal_name": "user@example.com",
            "folder": "Inbox",
            "process_unread_only": True,
            "mark_as_read": False,
            "filter_query": None,
            "sync_known_only": False,
        }

    async def fake_acquire_token(company_id, **kwargs):
        return "fake-token"

    async def fake_graph_get(access_token: str, url: str):
        return {
            "value": [
                {
                    "id": "msg-already",
                    "internetMessageId": "<already@example.com>",
                    "subject": "Old Message",
                    "body": {"content": "Old"},
                    "from": {"emailAddress": {"address": "old@example.com", "name": "Old"}},
                    "toRecipients": [],
                    "ccRecipients": [],
                    "bccRecipients": [],
                    "replyTo": [],
                    "isRead": False,
                    "hasAttachments": False,
                    "internetMessageHeaders": [],
                },
            ],
        }

    async def fake_get_message(account_id, message_uid):
        return {"status": "imported"}  # Already imported

    async def fake_update_account(account_id, **fields):
        return None

    monkeypatch.setattr(m365_mail.modules_service, "get_module", fake_get_module)
    monkeypatch.setattr(m365_mail.mail_repo, "get_account", fake_get_account)
    monkeypatch.setattr(m365_mail.m365_service, "acquire_access_token", fake_acquire_token)
    monkeypatch.setattr(m365_mail, "_graph_get", fake_graph_get)
    monkeypatch.setattr(m365_mail.mail_repo, "get_message", fake_get_message)
    monkeypatch.setattr(m365_mail.mail_repo, "update_account", fake_update_account)

    result = await m365_mail.sync_account(1)

    assert result["status"] == "succeeded"
    assert result["processed"] == 0


async def test_sync_account_applies_filter(monkeypatch):
    """sync_account skips messages not matching filter rules."""
    monkeypatch.setattr(m365_mail.system_state, "is_restart_pending", lambda: False)

    async def fake_get_module(slug: str, *, redact: bool = True):
        return {"enabled": True}

    async def fake_get_account(account_id: int):
        return {
            "id": 1,
            "active": True,
            "company_id": 5,
            "user_principal_name": "user@example.com",
            "folder": "Inbox",
            "process_unread_only": False,
            "mark_as_read": False,
            "filter_query": {"field": "subject", "contains": "urgent"},
            "sync_known_only": False,
        }

    async def fake_acquire_token(company_id, **kwargs):
        return "fake-token"

    async def fake_graph_get(access_token: str, url: str):
        return {
            "value": [
                {
                    "id": "msg-skip",
                    "internetMessageId": "<skip@example.com>",
                    "subject": "Routine update",
                    "body": {"content": "Nothing urgent"},
                    "from": {"emailAddress": {"address": "ops@example.com", "name": "Ops"}},
                    "toRecipients": [],
                    "ccRecipients": [],
                    "bccRecipients": [],
                    "replyTo": [],
                    "isRead": True,
                    "hasAttachments": False,
                    "internetMessageHeaders": [],
                },
            ],
        }

    async def fake_get_message(account_id, message_uid):
        return None

    async def fake_update_account(account_id, **fields):
        return None

    monkeypatch.setattr(m365_mail.modules_service, "get_module", fake_get_module)
    monkeypatch.setattr(m365_mail.mail_repo, "get_account", fake_get_account)
    monkeypatch.setattr(m365_mail.m365_service, "acquire_access_token", fake_acquire_token)
    monkeypatch.setattr(m365_mail, "_graph_get", fake_graph_get)
    monkeypatch.setattr(m365_mail.mail_repo, "get_message", fake_get_message)
    monkeypatch.setattr(m365_mail.mail_repo, "update_account", fake_update_account)

    result = await m365_mail.sync_account(1)

    assert result["status"] == "succeeded"
    assert result["processed"] == 0  # Filtered out


async def test_sync_account_handles_empty_mailbox(monkeypatch):
    """sync_account succeeds with 0 processed when mailbox is empty."""
    monkeypatch.setattr(m365_mail.system_state, "is_restart_pending", lambda: False)

    async def fake_get_module(slug: str, *, redact: bool = True):
        return {"enabled": True}

    async def fake_get_account(account_id: int):
        return {
            "id": 1,
            "active": True,
            "company_id": 5,
            "user_principal_name": "user@example.com",
            "folder": "Inbox",
            "process_unread_only": True,
            "mark_as_read": False,
        }

    async def fake_acquire_token(company_id, **kwargs):
        return "fake-token"

    async def fake_graph_get(access_token: str, url: str):
        return {"value": []}

    async def fake_update_account(account_id, **fields):
        return None

    monkeypatch.setattr(m365_mail.modules_service, "get_module", fake_get_module)
    monkeypatch.setattr(m365_mail.mail_repo, "get_account", fake_get_account)
    monkeypatch.setattr(m365_mail.m365_service, "acquire_access_token", fake_acquire_token)
    monkeypatch.setattr(m365_mail, "_graph_get", fake_graph_get)
    monkeypatch.setattr(m365_mail.mail_repo, "update_account", fake_update_account)

    result = await m365_mail.sync_account(1)

    assert result["status"] == "succeeded"
    assert result["processed"] == 0


# ---------------------------------------------------------------------------
# Sync account - reply matching
# ---------------------------------------------------------------------------


async def test_sync_account_matches_existing_ticket(monkeypatch):
    """sync_account adds a reply to an existing ticket when matched."""
    monkeypatch.setattr(m365_mail.system_state, "is_restart_pending", lambda: False)

    async def fake_get_module(slug: str, *, redact: bool = True):
        return {"enabled": True}

    async def fake_get_account(account_id: int):
        return {
            "id": 1,
            "active": True,
            "company_id": 5,
            "user_principal_name": "user@example.com",
            "folder": "Inbox",
            "process_unread_only": True,
            "mark_as_read": False,
            "filter_query": None,
            "sync_known_only": False,
        }

    async def fake_acquire_token(company_id, **kwargs):
        return "fake-token"

    async def fake_graph_get(access_token: str, url: str):
        return {
            "value": [
                {
                    "id": "msg-reply",
                    "internetMessageId": "<reply@example.com>",
                    "subject": "Re: Original ticket",
                    "body": {"content": "<p>Thanks for the update</p>"},
                    "from": {"emailAddress": {"address": "user@example.com", "name": "User"}},
                    "toRecipients": [],
                    "ccRecipients": [],
                    "bccRecipients": [],
                    "replyTo": [],
                    "isRead": False,
                    "receivedDateTime": "2026-01-15T12:00:00Z",
                    "hasAttachments": False,
                    "internetMessageHeaders": [
                        {"name": "In-Reply-To", "value": "<original@example.com>"},
                    ],
                },
            ],
        }

    async def fake_get_message(account_id, message_uid):
        return None

    async def fake_resolve_ticket_entities(from_header, *, default_company_id=None):
        return 5, 42  # company_id, requester_id

    async def fake_find_existing_ticket(subject, from_email, **kwargs):
        return {"id": 50, "status": "open"}  # Existing ticket found

    replies_added: list[dict] = []

    async def fake_create_reply(**kwargs):
        replies_added.append(kwargs)

    async def fake_emit_ticket_updated_event(ticket_id, actor=None):
        pass

    async def fake_upsert_message(**kwargs):
        pass

    async def fake_update_account(account_id, **fields):
        return None

    class FakeSanitized:
        has_rich_content = True
        html = "<p>Thanks for the update</p>"

    monkeypatch.setattr(m365_mail.modules_service, "get_module", fake_get_module)
    monkeypatch.setattr(m365_mail.mail_repo, "get_account", fake_get_account)
    monkeypatch.setattr(m365_mail.m365_service, "acquire_access_token", fake_acquire_token)
    monkeypatch.setattr(m365_mail, "_graph_get", fake_graph_get)
    monkeypatch.setattr(m365_mail.mail_repo, "get_message", fake_get_message)
    monkeypatch.setattr(m365_mail, "_resolve_ticket_entities", fake_resolve_ticket_entities)
    monkeypatch.setattr(m365_mail, "_find_existing_ticket_for_reply", fake_find_existing_ticket)
    monkeypatch.setattr(m365_mail.tickets_repo, "create_reply", fake_create_reply)
    monkeypatch.setattr(m365_mail.tickets_service, "emit_ticket_updated_event", fake_emit_ticket_updated_event)
    monkeypatch.setattr(m365_mail.mail_repo, "upsert_message", fake_upsert_message)
    monkeypatch.setattr(m365_mail.mail_repo, "update_account", fake_update_account)
    monkeypatch.setattr(m365_mail, "sanitize_rich_text", lambda text: FakeSanitized())

    result = await m365_mail.sync_account(1)

    assert result["status"] == "succeeded"
    assert result["processed"] == 1
    assert len(replies_added) == 1
    assert replies_added[0]["ticket_id"] == 50


# ---------------------------------------------------------------------------
# Schema validation
# ---------------------------------------------------------------------------


def test_m365_mail_account_create_schema():
    """Verify M365MailAccountCreate schema validates correctly."""
    from app.schemas.m365_mail import M365MailAccountCreate

    account = M365MailAccountCreate(
        name="Test Mailbox",
        company_id=1,
        user_principal_name="user@contoso.com",
        mailbox_type="shared",
        folder="Inbox",
        schedule_cron="*/15 * * * *",
    )

    assert account.name == "Test Mailbox"
    assert account.company_id == 1
    assert account.user_principal_name == "user@contoso.com"
    assert account.mailbox_type == "shared"
    assert account.process_unread_only is True
    assert account.mark_as_read is True
    assert account.sync_known_only is False
    assert account.active is True
    assert account.priority == 100


def test_m365_mail_account_update_schema_partial():
    """Verify M365MailAccountUpdate allows partial updates."""
    from app.schemas.m365_mail import M365MailAccountUpdate

    update = M365MailAccountUpdate(name="New Name")
    data = update.model_dump(exclude_unset=True)

    assert data == {"name": "New Name"}


def test_m365_mail_account_response_schema():
    """Verify M365MailAccountResponse validates correctly."""
    from app.schemas.m365_mail import M365MailAccountResponse

    response = M365MailAccountResponse.model_validate({
        "id": 1,
        "name": "Test",
        "company_id": 5,
        "user_principal_name": "user@example.com",
        "mailbox_type": "user",
        "folder": "Inbox",
        "schedule_cron": "*/15 * * * *",
        "filter_query": None,
        "process_unread_only": True,
        "mark_as_read": True,
        "sync_known_only": False,
        "active": True,
        "priority": 100,
        "last_synced_at": None,
        "scheduled_task_id": None,
    })

    assert response.id == 1
    assert response.user_principal_name == "user@example.com"
