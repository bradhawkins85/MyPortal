"""Tests for M365 mailbox sync and reporting functionality.

Covers:
- _count_forwarding_rules returns correct count from inbox message rules
- _count_forwarding_rules returns 0 on M365Error (graceful degradation)
- sync_mailboxes correctly classifies user vs shared mailboxes
- sync_mailboxes writes forwarding rules count for user mailboxes
- get_user_mailboxes and get_shared_mailboxes delegate to the repository
"""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services import m365 as m365_service
from app.services.m365 import M365Error, _count_forwarding_rules


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


# ---------------------------------------------------------------------------
# _count_forwarding_rules
# ---------------------------------------------------------------------------


@pytest.mark.anyio("asyncio")
async def test_count_forwarding_rules_counts_forward_to():
    """Rules with forwardTo action are counted."""
    rules = [
        {"actions": {"forwardTo": [{"emailAddress": {"address": "a@b.com"}}]}},
        {"actions": {"moveToFolder": "inbox"}},
    ]
    with patch.object(m365_service, "_graph_get_all", AsyncMock(return_value=rules)):
        count = await _count_forwarding_rules("token", "user-id")
    assert count == 1


@pytest.mark.anyio("asyncio")
async def test_count_forwarding_rules_counts_redirect_to():
    """Rules with redirectTo action are counted."""
    rules = [
        {"actions": {"redirectTo": [{"emailAddress": {"address": "a@b.com"}}]}},
        {"actions": {"redirectTo": [{"emailAddress": {"address": "b@c.com"}}]}},
    ]
    with patch.object(m365_service, "_graph_get_all", AsyncMock(return_value=rules)):
        count = await _count_forwarding_rules("token", "user-id")
    assert count == 2


@pytest.mark.anyio("asyncio")
async def test_count_forwarding_rules_returns_zero_on_error():
    """Returns 0 when the Graph API raises M365Error (e.g. no mailbox)."""
    with patch.object(m365_service, "_graph_get_all", AsyncMock(side_effect=M365Error("403"))):
        count = await _count_forwarding_rules("token", "user-id")
    assert count == 0


@pytest.mark.anyio("asyncio")
async def test_count_forwarding_rules_zero_for_no_forwarding():
    """Rules that don't forward/redirect return a count of 0."""
    rules = [
        {"actions": {"moveToFolder": "archive"}},
        {"actions": {"markAsRead": True}},
    ]
    with patch.object(m365_service, "_graph_get_all", AsyncMock(return_value=rules)):
        count = await _count_forwarding_rules("token", "user-id")
    assert count == 0


# ---------------------------------------------------------------------------
# sync_mailboxes
# ---------------------------------------------------------------------------


def _make_report_entry(
    upn: str,
    display_name: str = "",
    storage_bytes: int = 0,
    archive_bytes: int = 0,
    is_deleted: bool = False,
) -> dict[str, Any]:
    return {
        "userPrincipalName": upn,
        "displayName": display_name or upn,
        "storageUsedInBytes": storage_bytes,
        "archiveMailboxStorageUsedInBytes": archive_bytes or None,
        "isDeleted": is_deleted,
    }


@pytest.mark.anyio("asyncio")
async def test_sync_mailboxes_classifies_user_vs_shared():
    """Enabled users become UserMailbox; others become SharedMailbox."""
    report = [
        _make_report_entry("user@example.com", "Alice", storage_bytes=100),
        _make_report_entry("shared@example.com", "Shared Box", storage_bytes=50),
    ]
    users = [
        {"id": "u1", "userPrincipalName": "user@example.com", "displayName": "Alice"},
    ]

    upserted: list[dict[str, Any]] = []

    async def fake_upsert(**kwargs: Any) -> None:
        upserted.append(kwargs)

    with (
        patch.object(m365_service, "acquire_access_token", AsyncMock(return_value="tok")),
        patch.object(m365_service, "_graph_get_all", AsyncMock(return_value=report)),
        patch.object(m365_service, "get_all_users", AsyncMock(return_value=users)),
        patch.object(m365_service, "_count_forwarding_rules", AsyncMock(return_value=0)),
        patch.object(m365_service.m365_repo, "upsert_mailbox", side_effect=fake_upsert),
        patch.object(m365_service.m365_repo, "delete_stale_mailboxes", AsyncMock()),
    ):
        total = await m365_service.sync_mailboxes(1)

    assert total == 2
    types_by_upn = {r["user_principal_name"]: r["mailbox_type"] for r in upserted}
    assert types_by_upn["user@example.com"] == "UserMailbox"
    assert types_by_upn["shared@example.com"] == "SharedMailbox"


@pytest.mark.anyio("asyncio")
async def test_sync_mailboxes_stores_forwarding_rule_count():
    """Forwarding rule count from _count_forwarding_rules is stored for user mailboxes."""
    report = [_make_report_entry("user@example.com", "Alice")]
    users = [{"id": "u1", "userPrincipalName": "user@example.com", "displayName": "Alice"}]

    upserted: list[dict[str, Any]] = []

    async def fake_upsert(**kwargs: Any) -> None:
        upserted.append(kwargs)

    with (
        patch.object(m365_service, "acquire_access_token", AsyncMock(return_value="tok")),
        patch.object(m365_service, "_graph_get_all", AsyncMock(return_value=report)),
        patch.object(m365_service, "get_all_users", AsyncMock(return_value=users)),
        patch.object(m365_service, "_count_forwarding_rules", AsyncMock(return_value=3)),
        patch.object(m365_service.m365_repo, "upsert_mailbox", side_effect=fake_upsert),
        patch.object(m365_service.m365_repo, "delete_stale_mailboxes", AsyncMock()),
    ):
        await m365_service.sync_mailboxes(1)

    assert upserted[0]["forwarding_rule_count"] == 3


@pytest.mark.anyio("asyncio")
async def test_sync_mailboxes_archive_populated_when_present():
    """archive_storage_used_bytes is populated when non-zero in the report."""
    report = [_make_report_entry("user@example.com", "Alice", storage_bytes=500, archive_bytes=200)]
    users = [{"id": "u1", "userPrincipalName": "user@example.com", "displayName": "Alice"}]

    upserted: list[dict[str, Any]] = []

    async def fake_upsert(**kwargs: Any) -> None:
        upserted.append(kwargs)

    with (
        patch.object(m365_service, "acquire_access_token", AsyncMock(return_value="tok")),
        patch.object(m365_service, "_graph_get_all", AsyncMock(return_value=report)),
        patch.object(m365_service, "get_all_users", AsyncMock(return_value=users)),
        patch.object(m365_service, "_count_forwarding_rules", AsyncMock(return_value=0)),
        patch.object(m365_service.m365_repo, "upsert_mailbox", side_effect=fake_upsert),
        patch.object(m365_service.m365_repo, "delete_stale_mailboxes", AsyncMock()),
    ):
        await m365_service.sync_mailboxes(1)

    assert upserted[0]["has_archive"] is True
    assert upserted[0]["archive_storage_used_bytes"] == 200


@pytest.mark.anyio("asyncio")
async def test_sync_mailboxes_archive_none_when_absent():
    """archive_storage_used_bytes is None when not present in the report."""
    report = [_make_report_entry("user@example.com", "Alice", storage_bytes=100)]
    users = [{"id": "u1", "userPrincipalName": "user@example.com", "displayName": "Alice"}]

    upserted: list[dict[str, Any]] = []

    async def fake_upsert(**kwargs: Any) -> None:
        upserted.append(kwargs)

    with (
        patch.object(m365_service, "acquire_access_token", AsyncMock(return_value="tok")),
        patch.object(m365_service, "_graph_get_all", AsyncMock(return_value=report)),
        patch.object(m365_service, "get_all_users", AsyncMock(return_value=users)),
        patch.object(m365_service, "_count_forwarding_rules", AsyncMock(return_value=0)),
        patch.object(m365_service.m365_repo, "upsert_mailbox", side_effect=fake_upsert),
        patch.object(m365_service.m365_repo, "delete_stale_mailboxes", AsyncMock()),
    ):
        await m365_service.sync_mailboxes(1)

    assert upserted[0]["has_archive"] is False
    assert upserted[0]["archive_storage_used_bytes"] is None


@pytest.mark.anyio("asyncio")
async def test_sync_mailboxes_skips_deleted_entries():
    """Deleted mailbox entries in the report are skipped."""
    report = [
        _make_report_entry("user@example.com", "Alice", storage_bytes=100),
        _make_report_entry("deleted@example.com", "Gone", is_deleted=True),
    ]
    users = [{"id": "u1", "userPrincipalName": "user@example.com", "displayName": "Alice"}]

    upserted: list[dict[str, Any]] = []

    async def fake_upsert(**kwargs: Any) -> None:
        upserted.append(kwargs)

    with (
        patch.object(m365_service, "acquire_access_token", AsyncMock(return_value="tok")),
        patch.object(m365_service, "_graph_get_all", AsyncMock(return_value=report)),
        patch.object(m365_service, "get_all_users", AsyncMock(return_value=users)),
        patch.object(m365_service, "_count_forwarding_rules", AsyncMock(return_value=0)),
        patch.object(m365_service.m365_repo, "upsert_mailbox", side_effect=fake_upsert),
        patch.object(m365_service.m365_repo, "delete_stale_mailboxes", AsyncMock()),
    ):
        total = await m365_service.sync_mailboxes(1)

    assert total == 1
    assert upserted[0]["user_principal_name"] == "user@example.com"


# ---------------------------------------------------------------------------
# get_user_mailboxes / get_shared_mailboxes
# ---------------------------------------------------------------------------


@pytest.mark.anyio("asyncio")
async def test_get_user_mailboxes_delegates_to_repo():
    """get_user_mailboxes calls m365_repo.get_mailboxes with 'UserMailbox'."""
    fake_rows = [{"user_principal_name": "u@x.com", "mailbox_type": "UserMailbox"}]
    with patch.object(m365_service.m365_repo, "get_mailboxes", AsyncMock(return_value=fake_rows)) as mock_get:
        result = await m365_service.get_user_mailboxes(42)
    mock_get.assert_called_once_with(42, "UserMailbox")
    assert result == fake_rows


@pytest.mark.anyio("asyncio")
async def test_get_shared_mailboxes_delegates_to_repo():
    """get_shared_mailboxes calls m365_repo.get_mailboxes with 'SharedMailbox'."""
    fake_rows = [{"user_principal_name": "shared@x.com", "mailbox_type": "SharedMailbox"}]
    with patch.object(m365_service.m365_repo, "get_mailboxes", AsyncMock(return_value=fake_rows)) as mock_get:
        result = await m365_service.get_shared_mailboxes(42)
    mock_get.assert_called_once_with(42, "SharedMailbox")
    assert result == fake_rows
