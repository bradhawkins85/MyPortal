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
from app.services.m365 import (
    M365Error,
    _count_forwarding_rules,
    _parse_exo_mailbox_permission_records,
)


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
async def test_count_forwarding_rules_returns_zero_on_non_403_error():
    """Returns 0 when the Graph API raises a non-403 M365Error (e.g. no mailbox)."""
    with patch.object(
        m365_service, "_graph_get_all", AsyncMock(side_effect=M365Error("not found", http_status=404))
    ):
        count = await _count_forwarding_rules("token", "user-id")
    assert count == 0


@pytest.mark.anyio("asyncio")
async def test_count_forwarding_rules_reraises_403():
    """A 403 from the Graph API is re-raised so the caller can detect missing permissions."""
    with patch.object(
        m365_service, "_graph_get_all", AsyncMock(side_effect=M365Error("access denied", http_status=403))
    ):
        with pytest.raises(M365Error) as exc_info:
            await _count_forwarding_rules("token", "user-id")
    assert exc_info.value.http_status == 403


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
    has_archive: bool | None = None,
) -> dict[str, Any]:
    # When has_archive is not explicitly provided, infer it from archive_bytes
    # (mirroring real report data where Has Archive follows the storage field).
    inferred_has_archive = archive_bytes > 0 if has_archive is None else has_archive
    return {
        "userPrincipalName": upn,
        "displayName": display_name or upn,
        "storageUsedInBytes": storage_bytes,
        "archiveMailboxStorageUsedInBytes": archive_bytes or None,
        "hasArchive": inferred_has_archive,
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
        patch.object(
            m365_service, "acquire_access_token", AsyncMock(return_value="tok")
        ),
        patch.object(
            m365_service, "_fetch_mailbox_usage_report", AsyncMock(return_value=report)
        ),
        patch.object(m365_service, "get_all_users", AsyncMock(return_value=users)),
        patch.object(
            m365_service, "_count_forwarding_rules", AsyncMock(return_value=0)
        ),
        patch.object(m365_service.m365_repo, "upsert_mailbox", side_effect=fake_upsert),
        patch.object(m365_service.m365_repo, "delete_stale_mailboxes", AsyncMock()),
        patch.object(
            m365_service, "_get_user_mail_enabled_groups", AsyncMock(return_value=[])
        ),
        patch.object(m365_service.m365_repo, "upsert_mailbox_member", AsyncMock()),
        patch.object(
            m365_service.m365_repo, "delete_stale_mailbox_members", AsyncMock()
        ),
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
        patch.object(
            m365_service, "acquire_access_token", AsyncMock(return_value="tok")
        ),
        patch.object(
            m365_service, "_fetch_mailbox_usage_report", AsyncMock(return_value=report)
        ),
        patch.object(m365_service, "get_all_users", AsyncMock(return_value=users)),
        patch.object(
            m365_service, "_count_forwarding_rules", AsyncMock(return_value=0)
        ),
        patch.object(m365_service.m365_repo, "upsert_mailbox", side_effect=fake_upsert),
        patch.object(m365_service.m365_repo, "delete_stale_mailboxes", AsyncMock()),
        patch.object(
            m365_service, "_get_user_mail_enabled_groups", AsyncMock(return_value=[])
        ),
        patch.object(m365_service.m365_repo, "upsert_mailbox_member", AsyncMock()),
        patch.object(
            m365_service.m365_repo, "delete_stale_mailbox_members", AsyncMock()
        ),
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
    users = [
        {"id": "u1", "userPrincipalName": "user@example.com", "displayName": "Alice"}
    ]

    upserted: list[dict[str, Any]] = []

    async def fake_upsert(**kwargs: Any) -> None:
        upserted.append(kwargs)

    with (
        patch.object(
            m365_service, "acquire_access_token", AsyncMock(return_value="tok")
        ),
        patch.object(
            m365_service, "_fetch_mailbox_usage_report", AsyncMock(return_value=report)
        ),
        patch.object(m365_service, "get_all_users", AsyncMock(return_value=users)),
        patch.object(
            m365_service, "_count_forwarding_rules", AsyncMock(return_value=3)
        ),
        patch.object(m365_service.m365_repo, "upsert_mailbox", side_effect=fake_upsert),
        patch.object(m365_service.m365_repo, "delete_stale_mailboxes", AsyncMock()),
        patch.object(
            m365_service, "_get_user_mail_enabled_groups", AsyncMock(return_value=[])
        ),
        patch.object(m365_service.m365_repo, "upsert_mailbox_member", AsyncMock()),
        patch.object(
            m365_service.m365_repo, "delete_stale_mailbox_members", AsyncMock()
        ),
    ):
        await m365_service.sync_mailboxes(1)

    assert upserted[0]["forwarding_rule_count"] == 3


@pytest.mark.anyio("asyncio")
async def test_sync_mailboxes_archive_populated_when_present():
    """archive_storage_used_bytes is populated when non-zero in the report."""
    report = [
        _make_report_entry(
            "user@example.com", "Alice", storage_bytes=500, archive_bytes=200
        )
    ]
    users = [
        {"id": "u1", "userPrincipalName": "user@example.com", "displayName": "Alice"}
    ]

    upserted: list[dict[str, Any]] = []

    async def fake_upsert(**kwargs: Any) -> None:
        upserted.append(kwargs)

    with (
        patch.object(
            m365_service, "acquire_access_token", AsyncMock(return_value="tok")
        ),
        patch.object(
            m365_service, "_fetch_mailbox_usage_report", AsyncMock(return_value=report)
        ),
        patch.object(m365_service, "get_all_users", AsyncMock(return_value=users)),
        patch.object(
            m365_service, "_count_forwarding_rules", AsyncMock(return_value=0)
        ),
        patch.object(m365_service.m365_repo, "upsert_mailbox", side_effect=fake_upsert),
        patch.object(m365_service.m365_repo, "delete_stale_mailboxes", AsyncMock()),
        patch.object(
            m365_service, "_get_user_mail_enabled_groups", AsyncMock(return_value=[])
        ),
        patch.object(m365_service.m365_repo, "upsert_mailbox_member", AsyncMock()),
        patch.object(
            m365_service.m365_repo, "delete_stale_mailbox_members", AsyncMock()
        ),
    ):
        await m365_service.sync_mailboxes(1)

    assert upserted[0]["has_archive"] is True
    assert upserted[0]["archive_storage_used_bytes"] == 200


@pytest.mark.anyio("asyncio")
async def test_sync_mailboxes_archive_none_when_absent():
    """archive_storage_used_bytes is None when not present in the report."""
    report = [_make_report_entry("user@example.com", "Alice", storage_bytes=100)]
    users = [
        {"id": "u1", "userPrincipalName": "user@example.com", "displayName": "Alice"}
    ]

    upserted: list[dict[str, Any]] = []

    async def fake_upsert(**kwargs: Any) -> None:
        upserted.append(kwargs)

    with (
        patch.object(
            m365_service, "acquire_access_token", AsyncMock(return_value="tok")
        ),
        patch.object(
            m365_service, "_fetch_mailbox_usage_report", AsyncMock(return_value=report)
        ),
        patch.object(m365_service, "get_all_users", AsyncMock(return_value=users)),
        patch.object(
            m365_service, "_count_forwarding_rules", AsyncMock(return_value=0)
        ),
        patch.object(m365_service.m365_repo, "upsert_mailbox", side_effect=fake_upsert),
        patch.object(m365_service.m365_repo, "delete_stale_mailboxes", AsyncMock()),
        patch.object(
            m365_service, "_get_user_mail_enabled_groups", AsyncMock(return_value=[])
        ),
        patch.object(m365_service.m365_repo, "upsert_mailbox_member", AsyncMock()),
        patch.object(
            m365_service.m365_repo, "delete_stale_mailbox_members", AsyncMock()
        ),
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
    users = [
        {"id": "u1", "userPrincipalName": "user@example.com", "displayName": "Alice"}
    ]

    upserted: list[dict[str, Any]] = []

    async def fake_upsert(**kwargs: Any) -> None:
        upserted.append(kwargs)

    with (
        patch.object(
            m365_service, "acquire_access_token", AsyncMock(return_value="tok")
        ),
        patch.object(
            m365_service, "_fetch_mailbox_usage_report", AsyncMock(return_value=report)
        ),
        patch.object(m365_service, "get_all_users", AsyncMock(return_value=users)),
        patch.object(
            m365_service, "_count_forwarding_rules", AsyncMock(return_value=0)
        ),
        patch.object(m365_service.m365_repo, "upsert_mailbox", side_effect=fake_upsert),
        patch.object(m365_service.m365_repo, "delete_stale_mailboxes", AsyncMock()),
        patch.object(
            m365_service, "_get_user_mail_enabled_groups", AsyncMock(return_value=[])
        ),
        patch.object(m365_service.m365_repo, "upsert_mailbox_member", AsyncMock()),
        patch.object(
            m365_service.m365_repo, "delete_stale_mailbox_members", AsyncMock()
        ),
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
    fake_rows = [
        {
            "user_principal_name": "u@x.com",
            "mailbox_type": "UserMailbox",
            "display_name": "Alice",
        }
    ]
    with patch.object(
        m365_service.m365_repo, "get_mailboxes", AsyncMock(return_value=fake_rows)
    ) as mock_get:
        result = await m365_service.get_user_mailboxes(42)
    mock_get.assert_called_once_with(42, "UserMailbox")
    assert result == fake_rows


@pytest.mark.anyio("asyncio")
async def test_get_shared_mailboxes_delegates_to_repo():
    """get_shared_mailboxes calls m365_repo.get_mailboxes with 'SharedMailbox'."""
    fake_rows = [
        {
            "user_principal_name": "shared@x.com",
            "mailbox_type": "SharedMailbox",
            "display_name": "Shared Box",
        }
    ]
    with patch.object(
        m365_service.m365_repo, "get_mailboxes", AsyncMock(return_value=fake_rows)
    ) as mock_get:
        result = await m365_service.get_shared_mailboxes(42)
    mock_get.assert_called_once_with(42, "SharedMailbox")
    assert result == fake_rows


@pytest.mark.anyio("asyncio")
async def test_get_user_mailboxes_filters_package_mailboxes():
    """get_user_mailboxes excludes mailboxes whose display_name matches the package_ UUID pattern."""
    fake_rows = [
        {"user_principal_name": "u@x.com", "display_name": "Alice"},
        {
            "user_principal_name": "p@x.com",
            "display_name": "package_9024cbae-6e9a-4cee-934e-5f05143cd7ae",
        },
        {
            "user_principal_name": "p2@x.com",
            "display_name": "package_61fd5e57-4ae4-431b-9f34-16dfeed01fbb",
        },
    ]
    with patch.object(
        m365_service.m365_repo, "get_mailboxes", AsyncMock(return_value=fake_rows)
    ):
        result = await m365_service.get_user_mailboxes(42)
    assert len(result) == 1
    assert result[0]["display_name"] == "Alice"


@pytest.mark.anyio("asyncio")
async def test_get_shared_mailboxes_filters_package_mailboxes():
    """get_shared_mailboxes excludes mailboxes whose display_name matches the package_ UUID pattern."""
    fake_rows = [
        {"user_principal_name": "shared@x.com", "display_name": "Shared Box"},
        {
            "user_principal_name": "pkg@x.com",
            "display_name": "package_9024cbae-6e9a-4cee-934e-5f05143cd7ae",
        },
    ]
    with patch.object(
        m365_service.m365_repo, "get_mailboxes", AsyncMock(return_value=fake_rows)
    ):
        result = await m365_service.get_shared_mailboxes(42)
    assert len(result) == 1
    assert result[0]["display_name"] == "Shared Box"


@pytest.mark.anyio("asyncio")
async def test_get_user_mailboxes_keeps_non_package_similar_names():
    """Mailboxes with names that look similar but don't match the pattern are kept."""
    fake_rows = [
        {"user_principal_name": "a@x.com", "display_name": "package_not-a-uuid"},
        {
            "user_principal_name": "b@x.com",
            "display_name": "Package_9024cbae-6e9a-4cee-934e-5f05143cd7ae",
        },  # case-insensitive match → filtered
        {
            "user_principal_name": "c@x.com",
            "display_name": "my_package_9024cbae-6e9a-4cee-934e-5f05143cd7ae",
        },  # prefix doesn't match
    ]
    with patch.object(
        m365_service.m365_repo, "get_mailboxes", AsyncMock(return_value=fake_rows)
    ):
        result = await m365_service.get_user_mailboxes(42)
    upns = {r["user_principal_name"] for r in result}
    assert "b@x.com" not in upns  # filtered (case-insensitive)
    assert "a@x.com" in upns  # not a valid UUID
    assert "c@x.com" in upns  # not a package_ prefix


@pytest.mark.anyio("asyncio")
async def test_fetch_mailbox_usage_report_includes_archive_size():
    """Mailbox report collects archive mailbox size from the CSV export."""

    class _FakeResponse:
        def __init__(
            self,
            status_code: int,
            text: str = "",
            headers: dict[str, str] | None = None,
        ):
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
                return _FakeResponse(
                    302, headers={"Location": "https://download.example/report.csv"}
                )
            csv_text = (
                "Report Refresh Date,User Principal Name,Display Name,Is Deleted,Storage Used (Byte),"
                "Archive Mailbox Storage Used (Byte)\n"
                "2024-01-01,user@example.com,Alice,false,123,45\n"
            )
            return _FakeResponse(200, text=csv_text)

    with patch("app.services.m365.httpx.AsyncClient", _FakeAsyncClient):
        rows = await m365_service._fetch_mailbox_usage_report("tok")

    assert len(rows) == 1
    assert rows[0]["userPrincipalName"] == "user@example.com"
    assert rows[0]["storageUsedInBytes"] == 123
    assert rows[0]["archiveMailboxStorageUsedInBytes"] == 45
    assert rows[0]["isDeleted"] is False


@pytest.mark.anyio("asyncio")
async def test_fetch_mailbox_usage_report_uses_d180_period():
    """_fetch_mailbox_usage_report requests the D180 period from the Graph API.

    Microsoft's getMailboxUsageDetail report only populates Archive Storage
    Used (Byte) for archives that had archiving activity within the reporting
    window.  The 7-day (D7) window therefore returns 0 for most archives —
    which are not actively receiving new items every week — even though the
    archive contains data.  D180 covers the past six months and produces
    accurate archive sizes for virtually all real-world deployments.
    """
    captured_urls: list[str] = []

    class _FakeResponse:
        def __init__(
            self,
            status_code: int,
            text: str = "",
            headers: dict[str, str] | None = None,
        ):
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
            captured_urls.append(url)
            self.calls += 1
            if self.calls == 1:
                return _FakeResponse(
                    302, headers={"Location": "https://download.example/report.csv"}
                )
            csv_text = (
                "User Principal Name,Display Name,Is Deleted,"
                "Storage Used (Byte),Archive Storage Used (Byte)\n"
                "user@example.com,Alice,false,1073741824,524288000\n"
            )
            return _FakeResponse(200, text=csv_text)

    with patch("app.services.m365.httpx.AsyncClient", _FakeAsyncClient):
        await m365_service._fetch_mailbox_usage_report("tok")

    assert captured_urls, "No HTTP requests were made"
    first_url = captured_urls[0]
    assert "D180" in first_url, (
        f"Expected D180 period in report URL, got: {first_url!r}. "
        "Archive Storage Used (Byte) is only populated for archives with "
        "activity in the reporting window; D7 returns 0 for inactive archives."
    )
    assert "D7" not in first_url, (
        f"D7 period must not be used (returns 0 archive bytes for inactive archives): {first_url!r}"
    )


@pytest.mark.anyio("asyncio")
async def test_fetch_mailbox_usage_report_csv_handles_header_variants_and_invalid_numbers():
    """CSV export tolerates header case/BOM drift and invalid numeric fields."""

    class _FakeResponse:
        def __init__(
            self,
            status_code: int,
            text: str = "",
            headers: dict[str, str] | None = None,
        ):
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
                return _FakeResponse(
                    302, headers={"Location": "https://download.example/report.csv"}
                )
            csv_text = (
                "\ufeffUser principal name, display name ,is deleted,storage used (byte),archive mailbox storage used (byte)\n"
                "ALIAS@EXAMPLE.COM,Alice Alias,false,abc,\n"
            )
            return _FakeResponse(200, text=csv_text)

    with patch("app.services.m365.httpx.AsyncClient", _FakeAsyncClient):
        rows = await m365_service._fetch_mailbox_usage_report("tok")

    assert rows == [
        {
            "userPrincipalName": "alias@example.com",
            "displayName": "Alice Alias",
            "storageUsedInBytes": 0,
            "archiveMailboxStorageUsedInBytes": 0,
            "hasArchive": False,
            "isDeleted": False,
        }
    ]


@pytest.mark.anyio("asyncio")
async def test_fetch_mailbox_usage_report_returns_archive_size_from_csv():
    """Archive mailbox size is collected from the CSV export."""

    class _FakeResponse:
        def __init__(
            self,
            status_code: int,
            text: str = "",
            headers: dict[str, str] | None = None,
        ):
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
                return _FakeResponse(
                    302, headers={"Location": "https://download.example/report.csv"}
                )
            csv_text = (
                "User Principal Name,Display Name,Is Deleted,Storage Used (Byte),"
                "Archive Mailbox Storage Used (Byte)\n"
                "user@example.com,User One,false,5,1024\n"
            )
            return _FakeResponse(200, text=csv_text)

    with patch("app.services.m365.httpx.AsyncClient", _FakeAsyncClient):
        rows = await m365_service._fetch_mailbox_usage_report("tok")

    assert rows == [
        {
            "userPrincipalName": "user@example.com",
            "displayName": "User One",
            "storageUsedInBytes": 5,
            "archiveMailboxStorageUsedInBytes": 1024,
            "hasArchive": False,
            "isDeleted": False,
        }
    ]


@pytest.mark.anyio("asyncio")
async def test_fetch_mailbox_usage_report_csv_decodes_utf16_downloads():
    """CSV export decodes UTF-16 report payloads without losing mailbox rows."""

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
                return _FakeResponse(
                    302, headers={"Location": "https://download.example/report.csv"}
                )
            csv_text = (
                "User Principal Name,Display Name,Is Deleted,Storage Used (Byte),Archive Mailbox Storage Used (Byte)\n"
                "shared@example.com,Shared Team,false,2048,0\n"
            )
            # Simulate httpx incorrectly exposing text with embedded NULs when
            # the payload is UTF-16 but no charset is supplied.
            return _FakeResponse(
                200, text="\x00bad\x00decode", content=csv_text.encode("utf-16")
            )

    with patch("app.services.m365.httpx.AsyncClient", _FakeAsyncClient):
        rows = await m365_service._fetch_mailbox_usage_report("tok")

    assert rows == [
        {
            "userPrincipalName": "shared@example.com",
            "displayName": "Shared Team",
            "storageUsedInBytes": 2048,
            "archiveMailboxStorageUsedInBytes": 0,
            "hasArchive": False,
            "isDeleted": False,
        }
    ]


@pytest.mark.anyio("asyncio")
async def test_fetch_mailbox_usage_report_csv_archive_storage_used_without_mailbox():
    """CSV fallback correctly reads archive size when the column is named
    'Archive Storage Used (Byte)' (without 'Mailbox'), which is the column
    name used by the Microsoft Graph CSV export for getMailboxUsageDetail."""

    class _FakeResponse:
        def __init__(
            self,
            status_code: int,
            text: str = "",
            headers: dict[str, str] | None = None,
        ):
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
                return _FakeResponse(
                    302, headers={"Location": "https://download.example/report.csv"}
                )
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
            AsyncMock(
                side_effect=M365Error(
                    "Microsoft Graph request failed (400)", http_status=400
                )
            ),
        ),
        patch("app.services.m365.httpx.AsyncClient", _FakeAsyncClient),
    ):
        rows = await m365_service._fetch_mailbox_usage_report("tok")

    assert len(rows) == 1
    assert rows[0]["userPrincipalName"] == "user@example.com"
    assert rows[0]["storageUsedInBytes"] == 1073741824
    assert rows[0]["archiveMailboxStorageUsedInBytes"] == 524288000
    assert rows[0]["isDeleted"] is False


@pytest.mark.anyio("asyncio")
async def test_fetch_mailbox_usage_report_csv_archive_float_string():
    """Archive storage values returned as floating-point strings (e.g.
    '5368709120.0') are parsed correctly."""

    class _FakeResponse:
        def __init__(
            self,
            status_code: int,
            text: str = "",
            headers: dict[str, str] | None = None,
        ):
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
                return _FakeResponse(
                    302, headers={"Location": "https://download.example/report.csv"}
                )
            # Microsoft Graph can return storage values with a decimal suffix
            # (e.g. "5368709120.0") for large archive mailboxes.
            csv_text = (
                "Report Refresh Date,User Principal Name,Display Name,Is Deleted,"
                "Storage Used (Byte),Archive Storage Used (Byte)\n"
                "2024-01-01,user@example.com,Alice,false,1073741824.0,5368709120.0\n"
            )
            return _FakeResponse(200, text=csv_text)

    with patch("app.services.m365.httpx.AsyncClient", _FakeAsyncClient):
        rows = await m365_service._fetch_mailbox_usage_report("tok")

    assert len(rows) == 1
    assert rows[0]["storageUsedInBytes"] == 1073741824
    assert rows[0]["archiveMailboxStorageUsedInBytes"] == 5368709120


@pytest.mark.anyio("asyncio")
async def test_fetch_mailbox_usage_report_csv_has_archive_column():
    """'Has Archive' column is read and returned as hasArchive in the
    normalised entry, even when the archive storage byte count is zero."""

    class _FakeResponse:
        def __init__(
            self,
            status_code: int,
            text: str = "",
            headers: dict[str, str] | None = None,
        ):
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
                return _FakeResponse(
                    302, headers={"Location": "https://download.example/report.csv"}
                )
            csv_text = (
                "Report Refresh Date,User Principal Name,Display Name,Is Deleted,"
                "Storage Used (Byte),Archive Storage Used (Byte),Has Archive\n"
                "2024-01-01,user@example.com,Alice,false,1073741824,0,True\n"
                "2024-01-01,other@example.com,Bob,false,524288000,0,False\n"
            )
            return _FakeResponse(200, text=csv_text)

    with patch("app.services.m365.httpx.AsyncClient", _FakeAsyncClient):
        rows = await m365_service._fetch_mailbox_usage_report("tok")

    assert len(rows) == 2
    alice = next(r for r in rows if r["userPrincipalName"] == "user@example.com")
    bob = next(r for r in rows if r["userPrincipalName"] == "other@example.com")
    assert alice["hasArchive"] is True
    assert alice["archiveMailboxStorageUsedInBytes"] == 0
    assert bob["hasArchive"] is False


@pytest.mark.anyio("asyncio")
async def test_fetch_mailbox_usage_report_csv_has_archive_mailbox_column():
    """CSV fallback reads archive column/header variants used across tenants."""

    class _FakeResponse:
        def __init__(
            self,
            status_code: int,
            text: str = "",
            headers: dict[str, str] | None = None,
        ):
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
                return _FakeResponse(
                    302, headers={"Location": "https://download.example/report.csv"}
                )
            csv_text = (
                "User Principal Name,Display Name,Is Deleted,Storage Used (Byte),"
                "Archive Mailbox Size (Byte),Has Archive Mailbox,In-Place Archive Status\n"
                "user@example.com,User One,false,1024,0,true,Inactive\n"
            )
            return _FakeResponse(200, text=csv_text)

    with patch("app.services.m365.httpx.AsyncClient", _FakeAsyncClient):
        rows = await m365_service._fetch_mailbox_usage_report("tok")

    assert rows == [
        {
            "userPrincipalName": "user@example.com",
            "displayName": "User One",
            "storageUsedInBytes": 1024,
            "archiveMailboxStorageUsedInBytes": 0,
            "hasArchive": True,
            "isDeleted": False,
        }
    ]


@pytest.mark.anyio("asyncio")
async def test_sync_mailboxes_has_archive_from_report_flag():
    """has_archive is set True when the report's hasArchive flag is True even
    if archive_storage_used_bytes is zero (e.g. newly provisioned archive)."""
    report = [
        _make_report_entry(
            "user@example.com",
            "Alice",
            storage_bytes=500,
            archive_bytes=0,
            has_archive=True,
        )
    ]
    users = [
        {"id": "u1", "userPrincipalName": "user@example.com", "displayName": "Alice"}
    ]

    upserted: list[dict[str, Any]] = []

    async def fake_upsert(**kwargs: Any) -> None:
        upserted.append(kwargs)

    with (
        patch.object(
            m365_service, "acquire_access_token", AsyncMock(return_value="tok")
        ),
        patch.object(
            m365_service, "_fetch_mailbox_usage_report", AsyncMock(return_value=report)
        ),
        patch.object(m365_service, "get_all_users", AsyncMock(return_value=users)),
        patch.object(
            m365_service, "_count_forwarding_rules", AsyncMock(return_value=0)
        ),
        patch.object(m365_service.m365_repo, "upsert_mailbox", side_effect=fake_upsert),
        patch.object(m365_service.m365_repo, "delete_stale_mailboxes", AsyncMock()),
        patch.object(
            m365_service, "_get_user_mail_enabled_groups", AsyncMock(return_value=[])
        ),
        patch.object(m365_service.m365_repo, "upsert_mailbox_member", AsyncMock()),
        patch.object(
            m365_service.m365_repo, "delete_stale_mailbox_members", AsyncMock()
        ),
    ):
        await m365_service.sync_mailboxes(1)

    assert upserted[0]["has_archive"] is True
    assert upserted[0]["archive_storage_used_bytes"] == 0


@pytest.mark.anyio("asyncio")
async def test_sync_mailboxes_has_archive_fallback_from_bytes():
    """has_archive is set True when archive_bytes > 0, even if the report's
    hasArchive flag is False (CSV did not include the Has Archive column)."""
    # Simulate a CSV without a 'Has Archive' column: hasArchive=False but bytes non-zero
    report = [
        _make_report_entry(
            "user@example.com",
            "Alice",
            storage_bytes=500,
            archive_bytes=200,
            has_archive=False,
        )
    ]
    users = [
        {"id": "u1", "userPrincipalName": "user@example.com", "displayName": "Alice"}
    ]

    upserted: list[dict[str, Any]] = []

    async def fake_upsert(**kwargs: Any) -> None:
        upserted.append(kwargs)

    with (
        patch.object(
            m365_service, "acquire_access_token", AsyncMock(return_value="tok")
        ),
        patch.object(
            m365_service, "_fetch_mailbox_usage_report", AsyncMock(return_value=report)
        ),
        patch.object(m365_service, "get_all_users", AsyncMock(return_value=users)),
        patch.object(
            m365_service, "_count_forwarding_rules", AsyncMock(return_value=0)
        ),
        patch.object(m365_service.m365_repo, "upsert_mailbox", side_effect=fake_upsert),
        patch.object(m365_service.m365_repo, "delete_stale_mailboxes", AsyncMock()),
        patch.object(
            m365_service, "_get_user_mail_enabled_groups", AsyncMock(return_value=[])
        ),
        patch.object(m365_service.m365_repo, "upsert_mailbox_member", AsyncMock()),
        patch.object(
            m365_service.m365_repo, "delete_stale_mailbox_members", AsyncMock()
        ),
    ):
        await m365_service.sync_mailboxes(1)

    # has_archive should be True because archive_bytes > 0 (fallback)
    assert upserted[0]["has_archive"] is True
    assert upserted[0]["archive_storage_used_bytes"] == 200


@pytest.mark.anyio("asyncio")
async def test_fetch_mailbox_usage_report_csv_archive_status_active():
    """'Archive Status' column set to 'Active' causes hasArchive=True even when
    'Has Archive' column is absent and archive storage is zero.

    Microsoft 365 includes an 'Archive Status' column in the
    getMailboxUsageDetail CSV export.  When its value is 'Active', the archive
    is provisioned regardless of whether the 'Has Archive' boolean column is
    present or whether any archive storage has been consumed yet."""

    class _FakeResponse:
        def __init__(
            self,
            status_code: int,
            text: str = "",
            headers: dict[str, str] | None = None,
        ):
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
                return _FakeResponse(
                    302, headers={"Location": "https://download.example/report.csv"}
                )
            # CSV includes Archive Status column but no Has Archive column.
            # Alice has Active archive, Bob has no archive.
            csv_text = (
                "Report Refresh Date,User Principal Name,Display Name,Is Deleted,"
                "Storage Used (Byte),Archive Storage Used (Byte),Archive Status\n"
                "2024-01-01,alice@example.com,Alice,false,1073741824,0,Active\n"
                "2024-01-01,bob@example.com,Bob,false,524288000,0,\n"
            )
            return _FakeResponse(200, text=csv_text)

    with patch("app.services.m365.httpx.AsyncClient", _FakeAsyncClient):
        rows = await m365_service._fetch_mailbox_usage_report("tok")

    assert len(rows) == 2
    alice = next(r for r in rows if r["userPrincipalName"] == "alice@example.com")
    bob = next(r for r in rows if r["userPrincipalName"] == "bob@example.com")
    # Alice has Archive Status = Active so hasArchive must be True
    assert alice["hasArchive"] is True
    assert alice["archiveMailboxStorageUsedInBytes"] == 0
    # Bob has no archive status so hasArchive should be False
    assert bob["hasArchive"] is False


@pytest.mark.anyio("asyncio")
async def test_sync_mailboxes_has_archive_from_archive_status_column():
    """has_archive is set True for both user and shared mailboxes when the report
    contains 'Archive Status: Active', even when Has Archive is False and archive
    storage bytes is zero."""
    # Report entries where Archive Status is Active but Has Archive is False
    # and archive_bytes is 0 – this is the pattern that caused the bug where
    # archive size always showed '-'.
    report = [
        {
            "userPrincipalName": "user@example.com",
            "displayName": "Alice",
            "storageUsedInBytes": 500,
            "archiveMailboxStorageUsedInBytes": 0,
            "hasArchive": True,  # set True via Archive Status = Active
            "isDeleted": False,
        },
        {
            "userPrincipalName": "shared@example.com",
            "displayName": "Shared Box",
            "storageUsedInBytes": 200,
            "archiveMailboxStorageUsedInBytes": 0,
            "hasArchive": True,  # set True via Archive Status = Active
            "isDeleted": False,
        },
    ]
    users = [
        {"id": "u1", "userPrincipalName": "user@example.com", "displayName": "Alice"}
    ]

    upserted: list[dict[str, Any]] = []

    async def fake_upsert(**kwargs: Any) -> None:
        upserted.append(kwargs)

    with (
        patch.object(
            m365_service, "acquire_access_token", AsyncMock(return_value="tok")
        ),
        patch.object(
            m365_service, "_fetch_mailbox_usage_report", AsyncMock(return_value=report)
        ),
        patch.object(m365_service, "get_all_users", AsyncMock(return_value=users)),
        patch.object(
            m365_service, "_count_forwarding_rules", AsyncMock(return_value=0)
        ),
        patch.object(m365_service.m365_repo, "upsert_mailbox", side_effect=fake_upsert),
        patch.object(m365_service.m365_repo, "delete_stale_mailboxes", AsyncMock()),
        patch.object(
            m365_service, "_get_user_mail_enabled_groups", AsyncMock(return_value=[])
        ),
        patch.object(m365_service.m365_repo, "upsert_mailbox_member", AsyncMock()),
        patch.object(
            m365_service.m365_repo, "delete_stale_mailbox_members", AsyncMock()
        ),
    ):
        await m365_service.sync_mailboxes(1)

    by_upn = {r["user_principal_name"]: r for r in upserted}
    # Both user mailbox and shared mailbox should have has_archive=True
    assert by_upn["user@example.com"]["has_archive"] is True
    assert by_upn["user@example.com"]["archive_storage_used_bytes"] == 0
    assert by_upn["shared@example.com"]["has_archive"] is True
    assert by_upn["shared@example.com"]["archive_storage_used_bytes"] == 0


# ---------------------------------------------------------------------------
# sync_mailboxes – mailbox member sync (m365_mailbox_members table)
# ---------------------------------------------------------------------------


@pytest.mark.anyio("asyncio")
async def test_sync_mailboxes_upserts_member_rows_for_user_groups():
    """For each mail-enabled group a user belongs to, a member row is upserted."""
    report = [_make_report_entry("user@example.com", "Alice", storage_bytes=100)]
    users = [
        {"id": "u1", "userPrincipalName": "user@example.com", "displayName": "Alice"}
    ]
    mail_groups = [
        {
            "id": "g1",
            "displayName": "Shared Box",
            "mail": "shared@example.com",
            "mailEnabled": True,
        },
    ]

    upserted_members: list[dict] = []

    async def fake_upsert_member(**kwargs: Any) -> None:
        upserted_members.append(kwargs)

    with (
        patch.object(
            m365_service, "acquire_access_token", AsyncMock(return_value="tok")
        ),
        patch.object(
            m365_service, "_fetch_mailbox_usage_report", AsyncMock(return_value=report)
        ),
        patch.object(m365_service, "get_all_users", AsyncMock(return_value=users)),
        patch.object(
            m365_service, "_count_forwarding_rules", AsyncMock(return_value=0)
        ),
        patch.object(
            m365_service,
            "_get_user_mail_enabled_groups",
            AsyncMock(return_value=mail_groups),
        ),
        patch.object(m365_service.m365_repo, "upsert_mailbox", AsyncMock()),
        patch.object(m365_service.m365_repo, "delete_stale_mailboxes", AsyncMock()),
        patch.object(
            m365_service.m365_repo,
            "upsert_mailbox_member",
            side_effect=fake_upsert_member,
        ),
        patch.object(
            m365_service.m365_repo, "delete_stale_mailbox_members", AsyncMock()
        ),
    ):
        await m365_service.sync_mailboxes(1)

    assert len(upserted_members) == 1
    assert upserted_members[0]["mailbox_email"] == "shared@example.com"
    assert upserted_members[0]["member_upn"] == "user@example.com"
    assert upserted_members[0]["member_display_name"] == "Alice"


@pytest.mark.anyio("asyncio")
async def test_sync_mailboxes_upserts_one_row_per_group():
    """A user in multiple groups produces one member row per group."""
    report = [_make_report_entry("user@example.com", "Alice", storage_bytes=100)]
    users = [
        {"id": "u1", "userPrincipalName": "user@example.com", "displayName": "Alice"}
    ]
    mail_groups = [
        {
            "id": "g1",
            "displayName": "Sales",
            "mail": "sales@example.com",
            "mailEnabled": True,
        },
        {
            "id": "g2",
            "displayName": "Support",
            "mail": "support@example.com",
            "mailEnabled": True,
        },
    ]

    upserted_members: list[dict] = []

    async def fake_upsert_member(**kwargs: Any) -> None:
        upserted_members.append(kwargs)

    with (
        patch.object(
            m365_service, "acquire_access_token", AsyncMock(return_value="tok")
        ),
        patch.object(
            m365_service, "_fetch_mailbox_usage_report", AsyncMock(return_value=report)
        ),
        patch.object(m365_service, "get_all_users", AsyncMock(return_value=users)),
        patch.object(
            m365_service, "_count_forwarding_rules", AsyncMock(return_value=0)
        ),
        patch.object(
            m365_service,
            "_get_user_mail_enabled_groups",
            AsyncMock(return_value=mail_groups),
        ),
        patch.object(m365_service.m365_repo, "upsert_mailbox", AsyncMock()),
        patch.object(m365_service.m365_repo, "delete_stale_mailboxes", AsyncMock()),
        patch.object(
            m365_service.m365_repo,
            "upsert_mailbox_member",
            side_effect=fake_upsert_member,
        ),
        patch.object(
            m365_service.m365_repo, "delete_stale_mailbox_members", AsyncMock()
        ),
    ):
        await m365_service.sync_mailboxes(1)

    assert len(upserted_members) == 2
    emails = {m["mailbox_email"] for m in upserted_members}
    assert "sales@example.com" in emails
    assert "support@example.com" in emails


@pytest.mark.anyio("asyncio")
async def test_sync_mailboxes_calls_delete_stale_members_with_sync_timestamp():
    """delete_stale_mailbox_members is called with a datetime representing the sync start."""
    from datetime import datetime

    report = [_make_report_entry("user@example.com", "Alice", storage_bytes=100)]
    users = [
        {"id": "u1", "userPrincipalName": "user@example.com", "displayName": "Alice"}
    ]
    mail_groups = [
        {
            "id": "g1",
            "displayName": "Shared",
            "mail": "shared@example.com",
            "mailEnabled": True,
        },
    ]

    delete_calls: list = []

    async def fake_delete(company_id: int, synced_before: datetime) -> None:
        delete_calls.append((company_id, synced_before))

    with (
        patch.object(
            m365_service, "acquire_access_token", AsyncMock(return_value="tok")
        ),
        patch.object(
            m365_service, "_fetch_mailbox_usage_report", AsyncMock(return_value=report)
        ),
        patch.object(m365_service, "get_all_users", AsyncMock(return_value=users)),
        patch.object(
            m365_service, "_count_forwarding_rules", AsyncMock(return_value=0)
        ),
        patch.object(
            m365_service,
            "_get_user_mail_enabled_groups",
            AsyncMock(return_value=mail_groups),
        ),
        patch.object(m365_service.m365_repo, "upsert_mailbox", AsyncMock()),
        patch.object(m365_service.m365_repo, "delete_stale_mailboxes", AsyncMock()),
        patch.object(m365_service.m365_repo, "upsert_mailbox_member", AsyncMock()),
        patch.object(
            m365_service.m365_repo,
            "delete_stale_mailbox_members",
            side_effect=fake_delete,
        ),
    ):
        before = datetime.utcnow()
        await m365_service.sync_mailboxes(1)

    assert len(delete_calls) == 1
    company_id, synced_before = delete_calls[0]
    assert company_id == 1
    # synced_before should be a datetime at or after the time we recorded
    # (truncated to the second, so compare with truncated before)
    assert isinstance(synced_before, datetime)
    assert synced_before >= before.replace(microsecond=0)
    # Microseconds must be zero so the value stored in MySQL's DATETIME
    # column (second precision) exactly matches the comparison parameter
    # used by delete_stale_mailbox_members.
    assert synced_before.microsecond == 0


@pytest.mark.anyio("asyncio")
async def test_sync_mailboxes_member_synced_at_has_no_microseconds():
    """Upserted member rows use a synced_at with zero microseconds.

    MySQL DATETIME columns have second precision.  If the Python datetime
    has non-zero microseconds, MySQL rounds the INSERT value to the
    nearest second but compares with the full-precision parameter in the
    subsequent DELETE.  This mismatch causes freshly inserted rows to be
    deleted.  Verifying microsecond=0 prevents this regression.
    """
    from datetime import datetime as dt_cls

    report = [_make_report_entry("user@example.com", "Alice", storage_bytes=100)]
    users = [
        {"id": "u1", "userPrincipalName": "user@example.com", "displayName": "Alice"}
    ]
    mail_groups = [
        {
            "id": "g1",
            "displayName": "Shared Box",
            "mail": "shared@example.com",
            "mailEnabled": True,
        },
    ]

    upserted_members: list[dict] = []

    async def fake_upsert_member(**kwargs: Any) -> None:
        upserted_members.append(kwargs)

    with (
        patch.object(
            m365_service, "acquire_access_token", AsyncMock(return_value="tok")
        ),
        patch.object(
            m365_service, "_fetch_mailbox_usage_report", AsyncMock(return_value=report)
        ),
        patch.object(m365_service, "get_all_users", AsyncMock(return_value=users)),
        patch.object(
            m365_service, "_count_forwarding_rules", AsyncMock(return_value=0)
        ),
        patch.object(
            m365_service,
            "_get_user_mail_enabled_groups",
            AsyncMock(return_value=mail_groups),
        ),
        patch.object(m365_service.m365_repo, "upsert_mailbox", AsyncMock()),
        patch.object(m365_service.m365_repo, "delete_stale_mailboxes", AsyncMock()),
        patch.object(
            m365_service.m365_repo,
            "upsert_mailbox_member",
            side_effect=fake_upsert_member,
        ),
        patch.object(
            m365_service.m365_repo, "delete_stale_mailbox_members", AsyncMock()
        ),
    ):
        await m365_service.sync_mailboxes(1)

    assert len(upserted_members) == 1
    synced_at = upserted_members[0]["synced_at"]
    assert isinstance(synced_at, dt_cls)
    assert synced_at.microsecond == 0, (
        "synced_at must have zero microseconds to avoid MySQL DATETIME "
        "precision mismatch that causes delete_stale_mailbox_members to "
        "delete freshly inserted rows"
    )


@pytest.mark.anyio("asyncio")
async def test_sync_mailboxes_no_member_rows_when_user_has_no_groups():
    """Users with no mail-enabled group memberships produce no member rows."""
    report = [_make_report_entry("user@example.com", "Alice", storage_bytes=100)]
    users = [
        {"id": "u1", "userPrincipalName": "user@example.com", "displayName": "Alice"}
    ]

    upserted_members: list[dict] = []

    async def fake_upsert_member(**kwargs: Any) -> None:
        upserted_members.append(kwargs)

    with (
        patch.object(
            m365_service, "acquire_access_token", AsyncMock(return_value="tok")
        ),
        patch.object(
            m365_service, "_fetch_mailbox_usage_report", AsyncMock(return_value=report)
        ),
        patch.object(m365_service, "get_all_users", AsyncMock(return_value=users)),
        patch.object(
            m365_service, "_count_forwarding_rules", AsyncMock(return_value=0)
        ),
        patch.object(
            m365_service, "_get_user_mail_enabled_groups", AsyncMock(return_value=[])
        ),
        patch.object(m365_service.m365_repo, "upsert_mailbox", AsyncMock()),
        patch.object(m365_service.m365_repo, "delete_stale_mailboxes", AsyncMock()),
        patch.object(
            m365_service.m365_repo,
            "upsert_mailbox_member",
            side_effect=fake_upsert_member,
        ),
        patch.object(
            m365_service.m365_repo, "delete_stale_mailbox_members", AsyncMock()
        ),
    ):
        await m365_service.sync_mailboxes(1)

    assert upserted_members == []


@pytest.mark.anyio("asyncio")
async def test_sync_mailboxes_upserts_direct_user_mailbox_permissions():
    """Direct user mailbox permissions are synced alongside group-based entries."""
    report = [_make_report_entry("shared@example.com", "Shared Box", storage_bytes=100)]

    upserted_members: list[dict] = []

    async def fake_upsert_member(**kwargs: Any) -> None:
        upserted_members.append(kwargs)

    direct_permissions = {
        "shared@example.com": [
            {
                "member_upn": "delegate@example.com",
                "member_display_name": "Delegate User",
            },
        ]
    }

    with (
        patch.object(
            m365_service, "acquire_access_token", AsyncMock(return_value="tok")
        ),
        patch.object(
            m365_service, "_fetch_mailbox_usage_report", AsyncMock(return_value=report)
        ),
        patch.object(m365_service, "get_all_users", AsyncMock(return_value=[])),
        patch.object(
            m365_service,
            "_fetch_exo_mailbox_permissions",
            AsyncMock(return_value=direct_permissions),
        ),
        patch.object(m365_service.m365_repo, "upsert_mailbox", AsyncMock()),
        patch.object(m365_service.m365_repo, "delete_stale_mailboxes", AsyncMock()),
        patch.object(
            m365_service.m365_repo,
            "upsert_mailbox_member",
            side_effect=fake_upsert_member,
        ),
        patch.object(
            m365_service.m365_repo, "delete_stale_mailbox_members", AsyncMock()
        ),
    ):
        await m365_service.sync_mailboxes(1)

    assert upserted_members == [
        {
            "company_id": 1,
            "mailbox_email": "shared@example.com",
            "member_upn": "delegate@example.com",
            "member_display_name": "Delegate User",
            "synced_at": upserted_members[0]["synced_at"],
        }
    ]


def test_parse_exo_mailbox_permission_records_filters_and_normalises():
    """EXO mailbox permission records keep FullAccess users and discard denied/self entries."""
    records = [
        {
            "User": "Delegate User <delegate@example.com>",
            "AccessRights": ["FullAccess"],
            "Deny": False,
        },
        {
            "User": r"NT AUTHORITY\SELF",
            "AccessRights": ["FullAccess"],
            "Deny": False,
        },
        {
            "User": "Denied User <denied@example.com>",
            "AccessRights": ["FullAccess"],
            "Deny": True,
        },
        {
            "User": "Reader <reader@example.com>",
            "AccessRights": ["ReadPermission"],
            "Deny": False,
        },
    ]

    result = _parse_exo_mailbox_permission_records("shared@example.com", records)

    assert result == [
        {
            "member_display_name": "Delegate User",
            "member_upn": "delegate@example.com",
        }
    ]


# ---------------------------------------------------------------------------
# sync_mailboxes – 403 from messageRules skips remaining users
# ---------------------------------------------------------------------------


@pytest.mark.anyio("asyncio")
async def test_sync_mailboxes_skips_forwarding_rules_on_403():
    """A 403 from _count_forwarding_rules skips remaining users instead of repeating failing calls."""
    report = [
        _make_report_entry("user1@example.com", "Alice", storage_bytes=100),
        _make_report_entry("user2@example.com", "Bob", storage_bytes=200),
    ]
    users = [
        {"id": "u1", "userPrincipalName": "user1@example.com", "displayName": "Alice"},
        {"id": "u2", "userPrincipalName": "user2@example.com", "displayName": "Bob"},
    ]

    upserted: list[dict[str, Any]] = []

    async def fake_upsert(**kwargs: Any) -> None:
        upserted.append(kwargs)

    # _count_forwarding_rules raises 403 on the first call
    fw_mock = AsyncMock(side_effect=M365Error("access denied", http_status=403))

    with (
        patch.object(
            m365_service, "acquire_access_token", AsyncMock(return_value="tok")
        ),
        patch.object(
            m365_service, "_fetch_mailbox_usage_report", AsyncMock(return_value=report)
        ),
        patch.object(m365_service, "get_all_users", AsyncMock(return_value=users)),
        patch.object(m365_service, "_count_forwarding_rules", fw_mock),
        patch.object(m365_service.m365_repo, "upsert_mailbox", side_effect=fake_upsert),
        patch.object(m365_service.m365_repo, "delete_stale_mailboxes", AsyncMock()),
        patch.object(
            m365_service, "_get_user_mail_enabled_groups", AsyncMock(return_value=[])
        ),
        patch.object(m365_service.m365_repo, "upsert_mailbox_member", AsyncMock()),
        patch.object(
            m365_service.m365_repo, "delete_stale_mailbox_members", AsyncMock()
        ),
    ):
        total = await m365_service.sync_mailboxes(1)

    # Sync should complete (not abort) and both mailboxes should be upserted
    assert total == 2
    # _count_forwarding_rules should only be called ONCE (for the first user),
    # because the 403 should cause remaining users to be skipped.
    assert fw_mock.call_count == 1
    # Both user mailboxes should have forwarding_rule_count = 0
    for row in upserted:
        if row["mailbox_type"] == "UserMailbox":
            assert row["forwarding_rule_count"] == 0


# ---------------------------------------------------------------------------
# Archive mailbox size sync via Exchange Online Get-MailboxStatistics -Archive
# ---------------------------------------------------------------------------


def test_parse_exo_total_item_size_handles_byte_quantified_string():
    """Display strings with parenthesised byte counts are parsed correctly."""
    from app.services.m365 import _parse_exo_total_item_size

    assert (
        _parse_exo_total_item_size("1.5 GB (1,610,612,736 bytes)") == 1_610_612_736
    )
    assert _parse_exo_total_item_size("0 B (0 bytes)") == 0
    assert (
        _parse_exo_total_item_size("123.45 MB (129,456,789 bytes)") == 129_456_789
    )


def test_parse_exo_total_item_size_handles_dict_and_numeric_inputs():
    """Nested dicts and plain numeric inputs are normalised to ints."""
    from app.services.m365 import _parse_exo_total_item_size

    assert _parse_exo_total_item_size({"Value": "2,048"}) == 2048
    assert (
        _parse_exo_total_item_size({"value": "5 GB (5,368,709,120 bytes)"})
        == 5_368_709_120
    )
    assert _parse_exo_total_item_size({"ToBytes": 4096}) == 4096
    assert _parse_exo_total_item_size(1024) == 1024
    assert _parse_exo_total_item_size("9999") == 9999


def test_parse_exo_total_item_size_handles_invalid_inputs():
    """Unparseable values yield ``None`` rather than raising."""
    from app.services.m365 import _parse_exo_total_item_size

    assert _parse_exo_total_item_size(None) is None
    assert _parse_exo_total_item_size("") is None
    assert _parse_exo_total_item_size("Unlimited") is None
    assert _parse_exo_total_item_size({}) is None
    assert _parse_exo_total_item_size(True) is None


@pytest.mark.anyio("asyncio")
async def test_exo_get_archive_mailbox_size_returns_bytes():
    """A successful Get-MailboxStatistics response yields the archive size in bytes."""
    from app.services.m365 import _exo_get_archive_mailbox_size

    captured: dict[str, Any] = {}

    class _FakeResponse:
        status_code = 200
        text = ""

        def json(self) -> dict[str, Any]:
            return {
                "value": [
                    {
                        "DisplayName": "Alice",
                        "TotalItemSize": "1.5 GB (1,610,612,736 bytes)",
                    }
                ]
            }

    class _FakeAsyncClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(
            self,
            url: str,
            headers: dict[str, str] | None = None,
            json: dict[str, Any] | None = None,
        ):
            captured["url"] = url
            captured["headers"] = headers
            captured["json"] = json
            return _FakeResponse()

    with patch("app.services.m365.httpx.AsyncClient", _FakeAsyncClient):
        size = await _exo_get_archive_mailbox_size(
            "exo-tok", "tenant-1", "user@example.com"
        )

    assert size == 1_610_612_736
    assert "InvokeCommand" in captured["url"]
    assert captured["json"]["CmdletInput"]["CmdletName"] == "Get-MailboxStatistics"
    assert captured["json"]["CmdletInput"]["Parameters"] == {
        "Identity": "user@example.com",
        "Archive": True,
    }


@pytest.mark.anyio("asyncio")
async def test_exo_get_archive_mailbox_size_no_archive_returns_none():
    """Mailboxes without an archive return ``None`` instead of raising."""
    from app.services.m365 import _exo_get_archive_mailbox_size

    class _FakeResponse:
        status_code = 400
        text = (
            "The mailbox 'user@example.com' isn't enabled for archive."
        )

        def json(self) -> dict[str, Any]:
            return {}

    class _FakeAsyncClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, *args: Any, **kwargs: Any):
            return _FakeResponse()

    with patch("app.services.m365.httpx.AsyncClient", _FakeAsyncClient):
        size = await _exo_get_archive_mailbox_size(
            "exo-tok", "tenant-1", "user@example.com"
        )

    assert size is None


@pytest.mark.anyio("asyncio")
async def test_exo_get_archive_mailbox_size_403_raises():
    """A 403 from Exchange Online surfaces as M365Error so callers can short-circuit."""
    from app.services.m365 import _exo_get_archive_mailbox_size

    class _FakeResponse:
        status_code = 403
        text = "Forbidden"

        def json(self) -> dict[str, Any]:
            return {}

    class _FakeAsyncClient:
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, *args: Any, **kwargs: Any):
            return _FakeResponse()

    with patch("app.services.m365.httpx.AsyncClient", _FakeAsyncClient):
        with pytest.raises(M365Error) as exc_info:
            await _exo_get_archive_mailbox_size(
                "exo-tok", "tenant-1", "user@example.com"
            )
    assert exc_info.value.http_status == 403


@pytest.mark.anyio("asyncio")
async def test_fetch_exo_archive_mailbox_sizes_returns_empty_when_token_unavailable():
    """When EXO token cannot be acquired the helper degrades silently to {}."""
    from app.services.m365 import _fetch_exo_archive_mailbox_sizes

    with patch.object(
        m365_service,
        "_acquire_exo_access_token",
        AsyncMock(side_effect=M365Error("no exo")),
    ):
        result = await _fetch_exo_archive_mailbox_sizes(1, {"user@example.com"})

    assert result == {}


@pytest.mark.anyio("asyncio")
async def test_fetch_exo_archive_mailbox_sizes_collects_sizes():
    """Per-mailbox sizes are collected; mailboxes without archives are omitted."""
    from app.services.m365 import _fetch_exo_archive_mailbox_sizes

    async def fake_get_size(
        exo_token: str, tenant_id: str, mailbox_email: str
    ) -> int | None:
        return {
            "alice@example.com": 1_000_000,
            "shared@example.com": 5_000_000,
        }.get(mailbox_email)

    with (
        patch.object(
            m365_service,
            "_acquire_exo_access_token",
            AsyncMock(return_value=("tok", "tenant")),
        ),
        patch.object(
            m365_service,
            "_exo_get_archive_mailbox_size",
            AsyncMock(side_effect=fake_get_size),
        ),
    ):
        result = await _fetch_exo_archive_mailbox_sizes(
            1,
            {"alice@example.com", "shared@example.com", "noarchive@example.com"},
        )

    assert result == {
        "alice@example.com": 1_000_000,
        "shared@example.com": 5_000_000,
    }


@pytest.mark.anyio("asyncio")
async def test_fetch_exo_archive_mailbox_sizes_short_circuits_on_403():
    """A 403 from any mailbox stops further lookups (same permission affects all)."""
    from app.services.m365 import _fetch_exo_archive_mailbox_sizes

    call_count = 0

    async def fake_get_size(
        exo_token: str, tenant_id: str, mailbox_email: str
    ) -> int | None:
        nonlocal call_count
        call_count += 1
        raise M365Error("forbidden", http_status=403)

    with (
        patch.object(
            m365_service,
            "_acquire_exo_access_token",
            AsyncMock(return_value=("tok", "tenant")),
        ),
        patch.object(
            m365_service,
            "_exo_get_archive_mailbox_size",
            AsyncMock(side_effect=fake_get_size),
        ),
    ):
        result = await _fetch_exo_archive_mailbox_sizes(
            1, {"a@example.com", "b@example.com", "c@example.com"}
        )

    assert result == {}
    assert call_count == 1


@pytest.mark.anyio("asyncio")
async def test_sync_mailboxes_overrides_archive_size_from_exchange_online():
    """Archive sizes from Get-MailboxStatistics override the (zero) report values.

    Microsoft Graph's getMailboxUsageDetail report does not expose archive
    storage, so the archive_storage_used_bytes value from the report is
    effectively always 0.  When Exchange Online's Get-MailboxStatistics
    -Archive cmdlet returns a real size, that value must be persisted and
    has_archive must be set to True.
    """
    report = [
        # Report says the user has an archive but reports 0 bytes (real-world
        # Microsoft Graph behaviour).
        _make_report_entry(
            "user@example.com",
            "Alice",
            storage_bytes=500,
            archive_bytes=0,
            has_archive=True,
        ),
        # Shared mailbox where the report does not even claim an archive,
        # but Exchange Online confirms one exists.
        _make_report_entry(
            "shared@example.com",
            "Shared Box",
            storage_bytes=200,
            archive_bytes=0,
            has_archive=False,
        ),
    ]
    users = [
        {"id": "u1", "userPrincipalName": "user@example.com", "displayName": "Alice"}
    ]

    upserted: list[dict[str, Any]] = []

    async def fake_upsert(**kwargs: Any) -> None:
        upserted.append(kwargs)

    archive_sizes = {
        "user@example.com": 1_610_612_736,
        "shared@example.com": 524_288_000,
    }

    with (
        patch.object(
            m365_service, "acquire_access_token", AsyncMock(return_value="tok")
        ),
        patch.object(
            m365_service, "_fetch_mailbox_usage_report", AsyncMock(return_value=report)
        ),
        patch.object(m365_service, "get_all_users", AsyncMock(return_value=users)),
        patch.object(
            m365_service, "_count_forwarding_rules", AsyncMock(return_value=0)
        ),
        patch.object(m365_service.m365_repo, "upsert_mailbox", side_effect=fake_upsert),
        patch.object(m365_service.m365_repo, "delete_stale_mailboxes", AsyncMock()),
        patch.object(
            m365_service, "_get_user_mail_enabled_groups", AsyncMock(return_value=[])
        ),
        patch.object(m365_service.m365_repo, "upsert_mailbox_member", AsyncMock()),
        patch.object(
            m365_service.m365_repo, "delete_stale_mailbox_members", AsyncMock()
        ),
        patch.object(
            m365_service,
            "_fetch_exo_archive_mailbox_sizes",
            AsyncMock(return_value=archive_sizes),
        ),
    ):
        await m365_service.sync_mailboxes(1)

    by_upn = {r["user_principal_name"]: r for r in upserted}
    assert by_upn["user@example.com"]["has_archive"] is True
    assert by_upn["user@example.com"]["archive_storage_used_bytes"] == 1_610_612_736
    assert by_upn["shared@example.com"]["has_archive"] is True
    assert by_upn["shared@example.com"]["archive_storage_used_bytes"] == 524_288_000


@pytest.mark.anyio("asyncio")
async def test_sync_mailboxes_keeps_report_values_when_exchange_online_unavailable():
    """When EXO archive lookup returns no data the report-derived values are kept."""
    report = [
        _make_report_entry(
            "user@example.com",
            "Alice",
            storage_bytes=500,
            archive_bytes=200,
            has_archive=True,
        ),
    ]
    users = [
        {"id": "u1", "userPrincipalName": "user@example.com", "displayName": "Alice"}
    ]

    upserted: list[dict[str, Any]] = []

    async def fake_upsert(**kwargs: Any) -> None:
        upserted.append(kwargs)

    with (
        patch.object(
            m365_service, "acquire_access_token", AsyncMock(return_value="tok")
        ),
        patch.object(
            m365_service, "_fetch_mailbox_usage_report", AsyncMock(return_value=report)
        ),
        patch.object(m365_service, "get_all_users", AsyncMock(return_value=users)),
        patch.object(
            m365_service, "_count_forwarding_rules", AsyncMock(return_value=0)
        ),
        patch.object(m365_service.m365_repo, "upsert_mailbox", side_effect=fake_upsert),
        patch.object(m365_service.m365_repo, "delete_stale_mailboxes", AsyncMock()),
        patch.object(
            m365_service, "_get_user_mail_enabled_groups", AsyncMock(return_value=[])
        ),
        patch.object(m365_service.m365_repo, "upsert_mailbox_member", AsyncMock()),
        patch.object(
            m365_service.m365_repo, "delete_stale_mailbox_members", AsyncMock()
        ),
        patch.object(
            m365_service,
            "_fetch_exo_archive_mailbox_sizes",
            AsyncMock(return_value={}),
        ),
    ):
        await m365_service.sync_mailboxes(1)

    assert upserted[0]["has_archive"] is True
    assert upserted[0]["archive_storage_used_bytes"] == 200
