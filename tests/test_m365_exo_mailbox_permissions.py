"""Tests for Exchange Online Get-MailboxPermission integration.

Covers:
- _parse_exo_mailbox_permission_records filters to FullAccess, non-deny, non-self
- _fetch_exo_mailbox_permissions acquires EXO token and calls per-mailbox API
- _fetch_exo_mailbox_permissions returns empty dict on token acquisition failure
- _fetch_exo_mailbox_permissions skips mailboxes whose API call fails
- _exo_get_mailbox_permission returns empty list on non-200 responses
- _exo_get_mailbox_permission returns empty list on invalid JSON
- _exo_get_mailbox_permission returns empty list on decompression errors
- sync_mailboxes calls _fetch_exo_mailbox_permissions instead of UTCM snapshots
- _exchange_token uses custom scope when provided
"""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.services import m365 as m365_service
from app.services.m365 import (
    M365Error,
    _parse_exo_mailbox_permission_records,
)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


# ---------------------------------------------------------------------------
# _parse_exo_mailbox_permission_records
# ---------------------------------------------------------------------------


def test_parse_exo_records_returns_fullaccess_members():
    """Records with FullAccess are returned as member dicts."""
    records = [
        {
            "User": "admin@contoso.com",
            "AccessRights": ["FullAccess"],
            "Deny": False,
        },
        {
            "User": "reader@contoso.com",
            "AccessRights": ["ReadPermission"],
            "Deny": False,
        },
    ]
    result = _parse_exo_mailbox_permission_records("shared@contoso.com", records)
    assert len(result) == 1
    assert result[0]["member_upn"] == "admin@contoso.com"


def test_parse_exo_records_handles_string_access_rights():
    """AccessRights as a single string (not list) is handled."""
    records = [
        {
            "User": "admin@contoso.com",
            "AccessRights": "FullAccess",
            "Deny": False,
        },
    ]
    result = _parse_exo_mailbox_permission_records("shared@contoso.com", records)
    assert len(result) == 1
    assert result[0]["member_upn"] == "admin@contoso.com"


def test_parse_exo_records_handles_nested_object_access_rights():
    """AccessRights as a nested object with 'value' array is handled."""
    records = [
        {
            "User": "admin@contoso.com",
            "AccessRights": {"value": ["FullAccess"]},
            "Deny": False,
        },
    ]
    result = _parse_exo_mailbox_permission_records("shared@contoso.com", records)
    assert len(result) == 1
    assert result[0]["member_upn"] == "admin@contoso.com"


def test_parse_exo_records_handles_nested_object_with_odata_type():
    """AccessRights as a nested object with @odata.type and 'value' is handled."""
    records = [
        {
            "User": "admin@contoso.com",
            "AccessRights": {
                "@odata.type": "#Collection(String)",
                "value": ["FullAccess"],
            },
            "Deny": False,
        },
    ]
    result = _parse_exo_mailbox_permission_records("shared@contoso.com", records)
    assert len(result) == 1
    assert result[0]["member_upn"] == "admin@contoso.com"


def test_parse_exo_records_handles_dict_without_value_key():
    """AccessRights as a dict without 'value' key is treated as empty."""
    records = [
        {
            "User": "admin@contoso.com",
            "AccessRights": {"@odata.type": "#Collection(String)"},  # no value key
            "Deny": False,
        },
    ]
    result = _parse_exo_mailbox_permission_records("shared@contoso.com", records)
    assert result == []


def test_parse_exo_records_skips_deny_entries():
    """Deny=True entries are excluded."""
    records = [
        {
            "User": "blocked@contoso.com",
            "AccessRights": ["FullAccess"],
            "Deny": True,
        },
    ]
    result = _parse_exo_mailbox_permission_records("shared@contoso.com", records)
    assert result == []


def test_parse_exo_records_skips_nt_authority_self():
    """NT AUTHORITY\\SELF entries are excluded."""
    records = [
        {
            "User": "NT AUTHORITY\\SELF",
            "AccessRights": ["FullAccess"],
            "Deny": False,
        },
    ]
    result = _parse_exo_mailbox_permission_records("shared@contoso.com", records)
    assert result == []


def test_parse_exo_records_deduplicates_by_upn():
    """Duplicate user entries are de-duplicated by UPN."""
    records = [
        {
            "User": "admin@contoso.com",
            "AccessRights": ["FullAccess"],
            "Deny": False,
        },
        {
            "User": "admin@contoso.com",
            "AccessRights": ["FullAccess"],
            "Deny": False,
        },
    ]
    result = _parse_exo_mailbox_permission_records("shared@contoso.com", records)
    assert len(result) == 1


def test_parse_exo_records_sorted_alphabetically():
    """Results are sorted alphabetically by display name."""
    records = [
        {
            "User": "zoe@contoso.com",
            "AccessRights": ["FullAccess"],
            "Deny": False,
        },
        {
            "User": "alice@contoso.com",
            "AccessRights": ["FullAccess"],
            "Deny": False,
        },
    ]
    result = _parse_exo_mailbox_permission_records("shared@contoso.com", records)
    assert result[0]["member_upn"] == "alice@contoso.com"
    assert result[1]["member_upn"] == "zoe@contoso.com"


def test_parse_exo_records_handles_case_insensitive_keys():
    """Records with lowercase keys (accessRights, deny) are handled."""
    records = [
        {
            "User": "admin@contoso.com",
            "accessRights": ["FullAccess"],
            "deny": False,
        },
    ]
    result = _parse_exo_mailbox_permission_records("shared@contoso.com", records)
    assert len(result) == 1
    assert result[0]["member_upn"] == "admin@contoso.com"


def test_parse_exo_records_empty_user_skipped():
    """Records with empty User field are skipped."""
    records = [
        {
            "User": "",
            "AccessRights": ["FullAccess"],
            "Deny": False,
        },
    ]
    result = _parse_exo_mailbox_permission_records("shared@contoso.com", records)
    assert result == []


# ---------------------------------------------------------------------------
# _exo_get_mailbox_permission
# ---------------------------------------------------------------------------


@pytest.mark.anyio("asyncio")
async def test_exo_get_mailbox_permission_returns_value_on_success():
    """Successful 200 response returns value list."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "value": [
            {"User": "admin@contoso.com", "AccessRights": ["FullAccess"], "Deny": False}
        ]
    }

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(return_value=mock_response)

    with patch("app.services.m365.httpx.AsyncClient", return_value=mock_client):
        result = await m365_service._exo_get_mailbox_permission(
            "token", "tenant-id", "shared@contoso.com"
        )

    assert len(result) == 1
    assert result[0]["User"] == "admin@contoso.com"


@pytest.mark.anyio("asyncio")
async def test_exo_get_mailbox_permission_returns_empty_on_non_200():
    """Non-200 responses return an empty list."""
    mock_response = MagicMock()
    mock_response.status_code = 403

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(return_value=mock_response)

    with patch("app.services.m365.httpx.AsyncClient", return_value=mock_client):
        result = await m365_service._exo_get_mailbox_permission(
            "token", "tenant-id", "shared@contoso.com"
        )

    assert result == []


@pytest.mark.anyio("asyncio")
async def test_exo_get_mailbox_permission_non_200_decompression_error():
    """Non-200 responses with corrupt gzip body return an empty list."""
    mock_response = MagicMock()
    mock_response.status_code = 500
    # Simulate .text raising DecodingError when accessed
    type(mock_response).text = property(
        lambda self: (_ for _ in ()).throw(
            httpx.DecodingError("Error -3 while decompressing data: incorrect header check")
        )
    )

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(return_value=mock_response)

    with patch("app.services.m365.httpx.AsyncClient", return_value=mock_client):
        result = await m365_service._exo_get_mailbox_permission(
            "token", "tenant-id", "shared@contoso.com"
        )

    assert result == []


@pytest.mark.anyio("asyncio")
async def test_exo_get_mailbox_permission_returns_empty_on_invalid_json():
    """Invalid JSON responses return an empty list."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.side_effect = ValueError("bad json")

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(return_value=mock_response)

    with patch("app.services.m365.httpx.AsyncClient", return_value=mock_client):
        result = await m365_service._exo_get_mailbox_permission(
            "token", "tenant-id", "shared@contoso.com"
        )

    assert result == []


@pytest.mark.anyio("asyncio")
async def test_exo_get_mailbox_permission_returns_empty_on_decompression_error():
    """Decompression errors (e.g. corrupt gzip) return an empty list."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.side_effect = httpx.DecodingError(
        "Error -3 while decompressing data: incorrect header check"
    )

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.post = AsyncMock(return_value=mock_response)

    with patch("app.services.m365.httpx.AsyncClient", return_value=mock_client):
        result = await m365_service._exo_get_mailbox_permission(
            "token", "tenant-id", "shared@contoso.com"
        )

    assert result == []


# ---------------------------------------------------------------------------
# _fetch_exo_mailbox_permissions
# ---------------------------------------------------------------------------


@pytest.mark.anyio("asyncio")
async def test_fetch_exo_mailbox_permissions_returns_members():
    """Fetches EXO token and returns parsed FullAccess members per mailbox."""
    exo_records = [
        {"User": "admin@contoso.com", "AccessRights": ["FullAccess"], "Deny": False},
        {"User": "NT AUTHORITY\\SELF", "AccessRights": ["FullAccess"], "Deny": False},
    ]

    with (
        patch.object(
            m365_service,
            "_acquire_exo_access_token",
            AsyncMock(return_value=("exo-tok", "tid")),
        ),
        patch.object(
            m365_service,
            "_exo_get_mailbox_permission",
            AsyncMock(return_value=exo_records),
        ),
    ):
        result = await m365_service._fetch_exo_mailbox_permissions(
            1, {"shared@contoso.com"}
        )

    assert "shared@contoso.com" in result
    members = result["shared@contoso.com"]
    assert len(members) == 1
    assert members[0]["member_upn"] == "admin@contoso.com"


@pytest.mark.anyio("asyncio")
async def test_fetch_exo_mailbox_permissions_returns_empty_on_token_failure():
    """Returns empty dict when EXO token acquisition fails."""
    with patch.object(
        m365_service,
        "_acquire_exo_access_token",
        AsyncMock(side_effect=M365Error("token failed")),
    ):
        result = await m365_service._fetch_exo_mailbox_permissions(
            1, {"shared@contoso.com"}
        )

    assert result == {}


@pytest.mark.anyio("asyncio")
async def test_fetch_exo_mailbox_permissions_empty_set():
    """Empty mailbox set returns empty dict without any API calls."""
    result = await m365_service._fetch_exo_mailbox_permissions(1, set())
    assert result == {}


@pytest.mark.anyio("asyncio")
async def test_fetch_exo_mailbox_permissions_skips_failed_mailboxes():
    """Mailboxes whose EXO API call returns no records are excluded from results."""
    with (
        patch.object(
            m365_service,
            "_acquire_exo_access_token",
            AsyncMock(return_value=("exo-tok", "tid")),
        ),
        patch.object(
            m365_service,
            "_exo_get_mailbox_permission",
            AsyncMock(return_value=[]),
        ),
    ):
        result = await m365_service._fetch_exo_mailbox_permissions(
            1, {"shared@contoso.com"}
        )

    assert result == {}


# ---------------------------------------------------------------------------
# _exchange_token scope parameter
# ---------------------------------------------------------------------------


@pytest.mark.anyio("asyncio")
async def test_exchange_token_uses_custom_scope():
    """When scope is provided, _exchange_token uses it instead of the default Graph scope."""
    captured_data: list[dict[str, Any]] = []

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "access_token": "test-token",
        "expires_in": 3600,
    }

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    async def fake_post(url, **kwargs):
        captured_data.append(kwargs.get("data", {}))
        return mock_response

    mock_client.post = fake_post

    with patch("app.services.m365.httpx.AsyncClient", return_value=mock_client):
        token, _, _ = await m365_service._exchange_token(
            tenant_id="test-tenant",
            client_id="test-client",
            client_secret="test-secret",
            refresh_token=None,
            scope="https://outlook.office365.com/.default",
        )

    assert token == "test-token"
    assert len(captured_data) == 1
    assert captured_data[0]["scope"] == "https://outlook.office365.com/.default"


@pytest.mark.anyio("asyncio")
async def test_exchange_token_defaults_to_graph_scope():
    """When scope is not provided, _exchange_token defaults to Graph scope."""
    captured_data: list[dict[str, Any]] = []

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "access_token": "test-token",
        "expires_in": 3600,
    }

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    async def fake_post(url, **kwargs):
        captured_data.append(kwargs.get("data", {}))
        return mock_response

    mock_client.post = fake_post

    with patch("app.services.m365.httpx.AsyncClient", return_value=mock_client):
        token, _, _ = await m365_service._exchange_token(
            tenant_id="test-tenant",
            client_id="test-client",
            client_secret="test-secret",
            refresh_token=None,
        )

    assert token == "test-token"
    assert len(captured_data) == 1
    assert captured_data[0]["scope"] == "https://graph.microsoft.com/.default"
