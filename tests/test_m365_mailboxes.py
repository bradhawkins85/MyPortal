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
        patch.object(m365_service, "_fetch_mailbox_usage_report", AsyncMock(return_value=report)),
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
async def test_sync_mailboxes_matches_user_to_report_by_mail_alias():
    """When report UPN differs from Entra UPN, user mailbox still maps via mail alias."""
    report = [
        _make_report_entry("alias@example.com", "Alice Alias", storage_bytes=250),
    ]
    users = [
        {
            "id": "u1",
            "userPrincipalName": "user@example.onmicrosoft.com",
            "mail": "alias@example.com",
            "displayName": "Alice",
        },
    ]

    upserted: list[dict[str, Any]] = []

    async def fake_upsert(**kwargs: Any) -> None:
        upserted.append(kwargs)

    with (
        patch.object(m365_service, "acquire_access_token", AsyncMock(return_value="tok")),
        patch.object(m365_service, "_fetch_mailbox_usage_report", AsyncMock(return_value=report)),
        patch.object(m365_service, "get_all_users", AsyncMock(return_value=users)),
        patch.object(m365_service, "_count_forwarding_rules", AsyncMock(return_value=0)),
        patch.object(m365_service.m365_repo, "upsert_mailbox", side_effect=fake_upsert),
        patch.object(m365_service.m365_repo, "delete_stale_mailboxes", AsyncMock()),
    ):
        total = await m365_service.sync_mailboxes(1)

    assert total == 1
    assert upserted[0]["mailbox_type"] == "UserMailbox"
    assert upserted[0]["user_principal_name"] == "user@example.onmicrosoft.com"
    assert upserted[0]["storage_used_bytes"] == 250


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
        patch.object(m365_service, "_fetch_mailbox_usage_report", AsyncMock(return_value=report)),
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
        patch.object(m365_service, "_fetch_mailbox_usage_report", AsyncMock(return_value=report)),
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
        patch.object(m365_service, "_fetch_mailbox_usage_report", AsyncMock(return_value=report)),
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
        patch.object(m365_service, "_fetch_mailbox_usage_report", AsyncMock(return_value=report)),
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
    fake_rows = [{"user_principal_name": "u@x.com", "mailbox_type": "UserMailbox", "display_name": "Alice"}]
    with patch.object(m365_service.m365_repo, "get_mailboxes", AsyncMock(return_value=fake_rows)) as mock_get:
        result = await m365_service.get_user_mailboxes(42)
    mock_get.assert_called_once_with(42, "UserMailbox")
    assert result == fake_rows


@pytest.mark.anyio("asyncio")
async def test_get_shared_mailboxes_delegates_to_repo():
    """get_shared_mailboxes calls m365_repo.get_mailboxes with 'SharedMailbox'."""
    fake_rows = [{"user_principal_name": "shared@x.com", "mailbox_type": "SharedMailbox", "display_name": "Shared Box"}]
    with patch.object(m365_service.m365_repo, "get_mailboxes", AsyncMock(return_value=fake_rows)) as mock_get:
        result = await m365_service.get_shared_mailboxes(42)
    mock_get.assert_called_once_with(42, "SharedMailbox")
    assert result == fake_rows


@pytest.mark.anyio("asyncio")
async def test_get_user_mailboxes_filters_package_mailboxes():
    """get_user_mailboxes excludes mailboxes whose display_name matches the package_ UUID pattern."""
    fake_rows = [
        {"user_principal_name": "u@x.com", "display_name": "Alice"},
        {"user_principal_name": "p@x.com", "display_name": "package_9024cbae-6e9a-4cee-934e-5f05143cd7ae"},
        {"user_principal_name": "p2@x.com", "display_name": "package_61fd5e57-4ae4-431b-9f34-16dfeed01fbb"},
    ]
    with patch.object(m365_service.m365_repo, "get_mailboxes", AsyncMock(return_value=fake_rows)):
        result = await m365_service.get_user_mailboxes(42)
    assert len(result) == 1
    assert result[0]["display_name"] == "Alice"


@pytest.mark.anyio("asyncio")
async def test_get_shared_mailboxes_filters_package_mailboxes():
    """get_shared_mailboxes excludes mailboxes whose display_name matches the package_ UUID pattern."""
    fake_rows = [
        {"user_principal_name": "shared@x.com", "display_name": "Shared Box"},
        {"user_principal_name": "pkg@x.com", "display_name": "package_9024cbae-6e9a-4cee-934e-5f05143cd7ae"},
    ]
    with patch.object(m365_service.m365_repo, "get_mailboxes", AsyncMock(return_value=fake_rows)):
        result = await m365_service.get_shared_mailboxes(42)
    assert len(result) == 1
    assert result[0]["display_name"] == "Shared Box"


@pytest.mark.anyio("asyncio")
async def test_get_user_mailboxes_keeps_non_package_similar_names():
    """Mailboxes with names that look similar but don't match the pattern are kept."""
    fake_rows = [
        {"user_principal_name": "a@x.com", "display_name": "package_not-a-uuid"},
        {"user_principal_name": "b@x.com", "display_name": "Package_9024cbae-6e9a-4cee-934e-5f05143cd7ae"},  # case-insensitive match → filtered
        {"user_principal_name": "c@x.com", "display_name": "my_package_9024cbae-6e9a-4cee-934e-5f05143cd7ae"},  # prefix doesn't match
    ]
    with patch.object(m365_service.m365_repo, "get_mailboxes", AsyncMock(return_value=fake_rows)):
        result = await m365_service.get_user_mailboxes(42)
    upns = {r["user_principal_name"] for r in result}
    assert "b@x.com" not in upns  # filtered (case-insensitive)
    assert "a@x.com" in upns  # not a valid UUID
    assert "c@x.com" in upns  # not a package_ prefix


@pytest.mark.anyio("asyncio")
async def test_fetch_mailbox_usage_report_falls_back_to_csv_on_400():
    """When JSON format is rejected with 400, mailbox report falls back to CSV export."""

    class _FakeResponse:
        def __init__(self, status_code: int, text: str = "", headers: dict[str, str] | None = None):
            self.status_code = status_code
            self.text = text
            self.content = text.encode("utf-8")
            self.headers = headers or {}

    class _FakeAsyncClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            self.calls = 0

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url: str, headers: dict[str, str] | None = None):
            self.calls += 1
            if self.calls == 1:
                return _FakeResponse(302, headers={"Location": "https://download.example/report.csv"})
            csv_text = (
                "Report Refresh Date,User Principal Name,Display Name,Is Deleted,Storage Used (Byte),"
                "Archive Mailbox Storage Used (Byte)\n"
                "2024-01-01,user@example.com,Alice,false,123,45\n"
            )
            return _FakeResponse(200, text=csv_text)

    with (
        patch.object(
            m365_service,
            "_graph_get_all",
            AsyncMock(side_effect=M365Error("Microsoft Graph request failed (400)", http_status=400)),
        ),
        patch("app.services.m365.httpx.AsyncClient", _FakeAsyncClient),
    ):
        rows = await m365_service._fetch_mailbox_usage_report("tok")

    assert len(rows) == 1
    assert rows[0]["userPrincipalName"] == "user@example.com"
    assert rows[0]["storageUsedInBytes"] == 123
    assert rows[0]["archiveMailboxStorageUsedInBytes"] == 45
    assert rows[0]["isDeleted"] is False


@pytest.mark.anyio("asyncio")
async def test_fetch_mailbox_usage_report_csv_handles_header_variants_and_invalid_numbers():
    """CSV fallback tolerates header case/BOM drift and invalid numeric fields."""

    class _FakeResponse:
        def __init__(self, status_code: int, text: str = "", headers: dict[str, str] | None = None):
            self.status_code = status_code
            self.text = text
            self.content = text.encode("utf-8")
            self.headers = headers or {}

    class _FakeAsyncClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            self.calls = 0

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url: str, headers: dict[str, str] | None = None):
            self.calls += 1
            if self.calls == 1:
                return _FakeResponse(302, headers={"Location": "https://download.example/report.csv"})
            csv_text = (
                "\ufeffUser principal name, display name ,is deleted,storage used (byte),archive mailbox storage used (byte)\n"
                "ALIAS@EXAMPLE.COM,Alice Alias,false,abc,\n"
            )
            return _FakeResponse(200, text=csv_text)

    with (
        patch.object(
            m365_service,
            "_graph_get_all",
            AsyncMock(side_effect=M365Error("Microsoft Graph request failed (400)", http_status=400)),
        ),
        patch("app.services.m365.httpx.AsyncClient", _FakeAsyncClient),
    ):
        rows = await m365_service._fetch_mailbox_usage_report("tok")

    assert rows == [
        {
            "userPrincipalName": "alias@example.com",
            "displayName": "Alice Alias",
            "storageUsedInBytes": 0,
            "archiveMailboxStorageUsedInBytes": 0,
            "isDeleted": False,
        }
    ]


@pytest.mark.anyio("asyncio")
async def test_fetch_mailbox_usage_report_uses_json_when_available():
    """JSON mailbox report is returned directly when Graph accepts $format=application/json."""
    report = [{"userPrincipalName": "user@example.com", "storageUsedInBytes": 5}]
    with patch.object(m365_service, "_graph_get_all", AsyncMock(return_value=report)):
        rows = await m365_service._fetch_mailbox_usage_report("tok")
    assert rows == [
        {
            "userPrincipalName": "user@example.com",
            "displayName": "user@example.com",
            "storageUsedInBytes": 5,
            "archiveMailboxStorageUsedInBytes": 0,
            "isDeleted": False,
        }
    ]


@pytest.mark.anyio("asyncio")
async def test_fetch_mailbox_usage_report_normalises_non_canonical_json_keys():
    """JSON projection rows with CSV-style keys are normalised into expected schema."""
    report = [
        {
            "User Principal Name": "USER@EXAMPLE.COM",
            "Display Name": "User One",
            "Storage Used (Byte)": "2048",
            "Archive Mailbox Storage Used (Byte)": "1024",
            "Is Deleted": "false",
        }
    ]
    with patch.object(m365_service, "_graph_get_all", AsyncMock(return_value=report)):
        rows = await m365_service._fetch_mailbox_usage_report("tok")
    assert rows == [
        {
            "userPrincipalName": "user@example.com",
            "displayName": "User One",
            "storageUsedInBytes": 2048,
            "archiveMailboxStorageUsedInBytes": 1024,
            "isDeleted": False,
        }
    ]


@pytest.mark.anyio("asyncio")
async def test_fetch_mailbox_usage_report_csv_decodes_utf16_downloads():
    """CSV fallback decodes UTF-16 report payloads without losing mailbox rows."""

    class _FakeResponse:
        def __init__(
            self,
            status_code: int,
            text: str = "",
            headers: dict[str, str] | None = None,
            content: bytes | None = None,
        ):
            self.status_code = status_code
            self.text = text
            self.content = content if content is not None else text.encode("utf-8")
            self.headers = headers or {}

    class _FakeAsyncClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            self.calls = 0

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url: str, headers: dict[str, str] | None = None):
            self.calls += 1
            if self.calls == 1:
                return _FakeResponse(302, headers={"Location": "https://download.example/report.csv"})
            csv_text = (
                "User Principal Name,Display Name,Is Deleted,Storage Used (Byte),Archive Mailbox Storage Used (Byte)\n"
                "shared@example.com,Shared Team,false,2048,0\n"
            )
            # Simulate httpx incorrectly exposing text with embedded NULs when
            # the payload is UTF-16 but no charset is supplied.
            return _FakeResponse(200, text="\x00bad\x00decode", content=csv_text.encode("utf-16"))

    with (
        patch.object(
            m365_service,
            "_graph_get_all",
            AsyncMock(side_effect=M365Error("Microsoft Graph request failed (400)", http_status=400)),
        ),
        patch("app.services.m365.httpx.AsyncClient", _FakeAsyncClient),
    ):
        rows = await m365_service._fetch_mailbox_usage_report("tok")

    assert rows == [
        {
            "userPrincipalName": "shared@example.com",
            "displayName": "Shared Team",
            "storageUsedInBytes": 2048,
            "archiveMailboxStorageUsedInBytes": 0,
            "isDeleted": False,
        }
    ]


@pytest.mark.anyio("asyncio")
async def test_fetch_mailbox_usage_report_csv_archive_storage_used_without_mailbox():
    """CSV fallback correctly reads archive size when the column is named
    'Archive Storage Used (Byte)' (without 'Mailbox'), which is the column
    name used by the Microsoft Graph CSV export for getMailboxUsageDetail."""

    class _FakeResponse:
        def __init__(self, status_code: int, text: str = "", headers: dict[str, str] | None = None):
            self.status_code = status_code
            self.text = text
            self.content = text.encode("utf-8")
            self.headers = headers or {}

    class _FakeAsyncClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            self.calls = 0

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url: str, headers: dict[str, str] | None = None):
            self.calls += 1
            if self.calls == 1:
                return _FakeResponse(302, headers={"Location": "https://download.example/report.csv"})
            # Microsoft Graph CSV uses "Archive Storage Used (Byte)" (without "Mailbox")
            csv_text = (
                "Report Refresh Date,User Principal Name,Display Name,Is Deleted,"
                "Storage Used (Byte),Archive Storage Used (Byte)\n"
                "2024-01-01,user@example.com,Alice,false,1073741824,524288000\n"
            )
            return _FakeResponse(200, text=csv_text)

    with (
        patch.object(
            m365_service,
            "_graph_get_all",
            AsyncMock(side_effect=M365Error("Microsoft Graph request failed (400)", http_status=400)),
        ),
        patch("app.services.m365.httpx.AsyncClient", _FakeAsyncClient),
    ):
        rows = await m365_service._fetch_mailbox_usage_report("tok")

    assert len(rows) == 1
    assert rows[0]["userPrincipalName"] == "user@example.com"
    assert rows[0]["storageUsedInBytes"] == 1073741824
    assert rows[0]["archiveMailboxStorageUsedInBytes"] == 524288000
    assert rows[0]["isDeleted"] is False
