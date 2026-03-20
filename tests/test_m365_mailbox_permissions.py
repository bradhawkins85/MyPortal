"""Tests for the get_mailbox_permissions M365 service function.

Covers:
- Returns correct can_access list from live mail-enabled group memberships
- Ignores groups that are not mail-enabled
- Returns correct accessible_by list from synced DB member data (m365_mailbox_members)
- Returns empty can_access but still serves accessible_by data when user lookup fails
- Returns empty can_access when memberOf call fails; accessible_by still served from DB
- Returns empty accessible_by when DB has no synced data
- Lists are sorted alphabetically by display_name
- accessible_by DB lookup uses user object mail property (not raw UPN)
- accessible_by falls back to UPN for DB lookup when mail property is absent
- accessible_by uses member_upn as display_name fallback when member_display_name is empty
- accessible_by uses proxyAddresses to find members stored under alternate email aliases
- accessible_by includes results from live Exchange Online Get-MailboxPermission
- accessible_by de-duplicates live EXO results with DB members
- accessible_by still returns DB data when EXO token acquisition fails
"""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from app.repositories import m365 as m365_repo
from app.services import m365 as m365_service
from app.services.m365 import M365Error, get_mailbox_permissions


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _make_group(
    group_id: str,
    display_name: str,
    mail: str,
    *,
    mail_enabled: bool = True,
) -> dict[str, Any]:
    return {
        "@odata.type": "#microsoft.graph.group",
        "id": group_id,
        "displayName": display_name,
        "mail": mail,
        "mailEnabled": mail_enabled,
    }


def _db_member(upn: str, display_name: str = "") -> dict[str, Any]:
    return {"member_upn": upn, "member_display_name": display_name}


# ---------------------------------------------------------------------------
# can_access (live Graph membership)
# ---------------------------------------------------------------------------


@pytest.mark.anyio("asyncio")
async def test_get_mailbox_permissions_can_access_returns_mail_enabled_groups():
    """Mail-enabled group memberships appear in can_access."""
    user_obj = {"id": "uid-1", "displayName": "Alice"}
    groups = [
        _make_group("g1", "Sales Team", "sales@contoso.com"),
        _make_group("g2", "Finance", "finance@contoso.com"),
    ]

    with (
        patch.object(m365_service, "acquire_access_token", AsyncMock(return_value="tok")),
        patch.object(m365_service, "_graph_get", AsyncMock(return_value=user_obj)),
        patch.object(m365_service, "_graph_get_all", AsyncMock(return_value=groups)),
        patch.object(m365_service, "_get_mailbox_group_members", AsyncMock(return_value=[])),
        patch.object(m365_repo, "get_mailbox_members", AsyncMock(return_value=[])),
    ):
        result = await get_mailbox_permissions(1, "alice@contoso.com")

    assert len(result["can_access"]) == 2
    emails = {item["email"] for item in result["can_access"]}
    assert "sales@contoso.com" in emails
    assert "finance@contoso.com" in emails


@pytest.mark.anyio("asyncio")
async def test_get_mailbox_permissions_ignores_non_mail_enabled_groups():
    """Groups with mailEnabled=False are excluded from can_access."""
    user_obj = {"id": "uid-1", "displayName": "Alice"}
    groups = [
        _make_group("g1", "Sales Team", "sales@contoso.com", mail_enabled=True),
        _make_group("g2", "Security Group", None, mail_enabled=False),
    ]

    with (
        patch.object(m365_service, "acquire_access_token", AsyncMock(return_value="tok")),
        patch.object(m365_service, "_graph_get", AsyncMock(return_value=user_obj)),
        patch.object(m365_service, "_graph_get_all", AsyncMock(return_value=groups)),
        patch.object(m365_service, "_get_mailbox_group_members", AsyncMock(return_value=[])),
        patch.object(m365_repo, "get_mailbox_members", AsyncMock(return_value=[])),
    ):
        result = await get_mailbox_permissions(1, "alice@contoso.com")

    assert len(result["can_access"]) == 1
    assert result["can_access"][0]["email"] == "sales@contoso.com"


@pytest.mark.anyio("asyncio")
async def test_get_mailbox_permissions_empty_can_access_on_member_of_failure():
    """If memberOf raises M365Error, can_access is empty but accessible_by still served from DB."""
    user_obj = {"id": "uid-1", "displayName": "Alice", "mail": "alice@contoso.com"}
    db_members = [_db_member("bob@contoso.com", "Bob")]

    with (
        patch.object(m365_service, "acquire_access_token", AsyncMock(return_value="tok")),
        patch.object(m365_service, "_graph_get", AsyncMock(return_value=user_obj)),
        patch.object(m365_service, "_graph_get_all", AsyncMock(side_effect=M365Error("memberOf failed"))),
        patch.object(m365_service, "_get_mailbox_group_members", AsyncMock(return_value=[])),
        patch.object(m365_repo, "get_mailbox_members", AsyncMock(return_value=db_members)),
    ):
        result = await get_mailbox_permissions(1, "alice@contoso.com")

    assert result["can_access"] == []
    assert len(result["accessible_by"]) == 1
    assert result["accessible_by"][0]["upn"] == "bob@contoso.com"


# ---------------------------------------------------------------------------
# accessible_by (DB-backed)
# ---------------------------------------------------------------------------


@pytest.mark.anyio("asyncio")
async def test_get_mailbox_permissions_accessible_by_returns_db_members():
    """accessible_by is populated from synced DB member data."""
    user_obj = {"id": "uid-shared", "displayName": "Shared Box", "mail": "shared@contoso.com"}
    db_members = [
        _db_member("bob@contoso.com", "Bob Smith"),
        _db_member("carol@contoso.com", "Carol Jones"),
    ]

    with (
        patch.object(m365_service, "acquire_access_token", AsyncMock(return_value="tok")),
        patch.object(m365_service, "_graph_get", AsyncMock(return_value=user_obj)),
        patch.object(m365_service, "_graph_get_all", AsyncMock(return_value=[])),
        patch.object(m365_service, "_get_mailbox_group_members", AsyncMock(return_value=[])),
        patch.object(m365_repo, "get_mailbox_members", AsyncMock(return_value=db_members)),
    ):
        result = await get_mailbox_permissions(1, "shared@contoso.com")

    assert len(result["accessible_by"]) == 2
    upns = {item["upn"] for item in result["accessible_by"]}
    assert "bob@contoso.com" in upns
    assert "carol@contoso.com" in upns


@pytest.mark.anyio("asyncio")
async def test_get_mailbox_permissions_accessible_by_includes_live_group_members():
    """accessible_by supplements cached rows with live backing-group membership."""
    user_obj = {"id": "uid-shared", "displayName": "Shared Box", "mail": "shared@contoso.com"}
    live_members = [
        {"displayName": "Alice Delegate", "userPrincipalName": "alice@contoso.com"},
        {"displayName": "Bob Delegate", "userPrincipalName": "bob@contoso.com"},
    ]

    with (
        patch.object(m365_service, "acquire_access_token", AsyncMock(return_value="tok")),
        patch.object(m365_service, "_graph_get", AsyncMock(return_value=user_obj)),
        patch.object(m365_service, "_graph_get_all", AsyncMock(return_value=[])),
        patch.object(m365_service, "_get_mailbox_group_members", AsyncMock(return_value=live_members)),
        patch.object(m365_repo, "get_mailbox_members", AsyncMock(return_value=[])),
    ):
        result = await get_mailbox_permissions(1, "shared@contoso.com")

    assert result["accessible_by"] == [
        {"display_name": "Alice Delegate", "upn": "alice@contoso.com"},
        {"display_name": "Bob Delegate", "upn": "bob@contoso.com"},
    ]


@pytest.mark.anyio("asyncio")
async def test_get_mailbox_permissions_empty_accessible_by_when_no_db_data():
    """When the DB has no synced members for the mailbox, accessible_by is empty."""
    user_obj = {"id": "uid-1", "displayName": "Alice"}

    with (
        patch.object(m365_service, "acquire_access_token", AsyncMock(return_value="tok")),
        patch.object(m365_service, "_graph_get", AsyncMock(return_value=user_obj)),
        patch.object(m365_service, "_graph_get_all", AsyncMock(return_value=[])),
        patch.object(m365_service, "_get_mailbox_group_members", AsyncMock(return_value=[])),
        patch.object(m365_repo, "get_mailbox_members", AsyncMock(return_value=[])),
    ):
        result = await get_mailbox_permissions(1, "alice@contoso.com")

    assert result["accessible_by"] == []


@pytest.mark.anyio("asyncio")
async def test_get_mailbox_permissions_accessible_by_uses_mail_property_for_db_lookup():
    """DB lookup uses the user object's mail property (primary SMTP), not the raw UPN.

    Tenants where Exchange usage reports store an onmicrosoft.com UPN but the
    mailbox's primary email is a custom domain would get no results if the DB
    lookup used the raw UPN.
    """
    user_obj = {"id": "uid-shared", "displayName": "Help Desk", "mail": "helpdesk@company.com"}
    db_members = [_db_member("alice@company.com", "Alice")]

    captured_db_email: list[str] = []

    async def _fake_get_members(company_id: int, mailbox_email: str) -> list[dict]:
        captured_db_email.append(mailbox_email)
        return db_members

    with (
        patch.object(m365_service, "acquire_access_token", AsyncMock(return_value="tok")),
        patch.object(m365_service, "_graph_get", AsyncMock(return_value=user_obj)),
        patch.object(m365_service, "_graph_get_all", AsyncMock(return_value=[])),
        patch.object(m365_service, "_get_mailbox_group_members", AsyncMock(return_value=[])),
        patch.object(m365_repo, "get_mailbox_members", AsyncMock(side_effect=_fake_get_members)),
    ):
        # UPN passed in uses onmicrosoft.com domain; DB lookup must use mail property.
        result = await get_mailbox_permissions(1, "helpdesk@company.onmicrosoft.com")

    assert len(result["accessible_by"]) == 1
    assert result["accessible_by"][0]["upn"] == "alice@company.com"
    # The raw mailbox UPN is checked first, then the canonical mail property.
    assert captured_db_email == [
        "helpdesk@company.onmicrosoft.com",
        "helpdesk@company.com",
    ]


@pytest.mark.anyio("asyncio")
async def test_get_mailbox_permissions_accessible_by_falls_back_to_upn_when_mail_absent():
    """When the user object has no mail property, the raw UPN is used for the DB lookup."""
    user_obj = {"id": "uid-shared", "displayName": "Shared"}  # no mail field

    captured_db_email: list[str] = []

    async def _fake_get_members(company_id: int, mailbox_email: str) -> list[dict]:
        captured_db_email.append(mailbox_email)
        return []

    with (
        patch.object(m365_service, "acquire_access_token", AsyncMock(return_value="tok")),
        patch.object(m365_service, "_graph_get", AsyncMock(return_value=user_obj)),
        patch.object(m365_service, "_graph_get_all", AsyncMock(return_value=[])),
        patch.object(m365_service, "_get_mailbox_group_members", AsyncMock(return_value=[])),
        patch.object(m365_repo, "get_mailbox_members", AsyncMock(side_effect=_fake_get_members)),
    ):
        result = await get_mailbox_permissions(1, "shared@contoso.com")

    assert result["accessible_by"] == []
    assert captured_db_email == ["shared@contoso.com"]


@pytest.mark.anyio("asyncio")
async def test_get_mailbox_permissions_accessible_by_display_name_fallback():
    """When member_display_name is empty, member_upn is used as the display_name."""
    user_obj = {"id": "uid-shared", "displayName": "Shared", "mail": "shared@contoso.com"}
    db_members = [_db_member("bob@contoso.com", "")]  # empty display_name

    with (
        patch.object(m365_service, "acquire_access_token", AsyncMock(return_value="tok")),
        patch.object(m365_service, "_graph_get", AsyncMock(return_value=user_obj)),
        patch.object(m365_service, "_graph_get_all", AsyncMock(return_value=[])),
        patch.object(m365_service, "_get_mailbox_group_members", AsyncMock(return_value=[])),
        patch.object(m365_repo, "get_mailbox_members", AsyncMock(return_value=db_members)),
    ):
        result = await get_mailbox_permissions(1, "shared@contoso.com")

    assert len(result["accessible_by"]) == 1
    assert result["accessible_by"][0]["display_name"] == "bob@contoso.com"
    assert result["accessible_by"][0]["upn"] == "bob@contoso.com"


# ---------------------------------------------------------------------------
# Common / error paths
# ---------------------------------------------------------------------------


@pytest.mark.anyio("asyncio")
async def test_get_mailbox_permissions_returns_empty_on_user_lookup_failure():
    """If the user lookup raises M365Error, can_access is empty and DB-backed data still loads."""
    db_members = [_db_member("delegate@contoso.com", "Delegate User")]

    with (
        patch.object(m365_service, "acquire_access_token", AsyncMock(return_value="tok")),
        patch.object(m365_service, "_graph_get", AsyncMock(side_effect=M365Error("404"))),
        patch.object(m365_service, "_get_mailbox_group_members", AsyncMock(return_value=[])),
        patch.object(m365_repo, "get_mailbox_members", AsyncMock(return_value=db_members)),
    ):
        result = await get_mailbox_permissions(1, "shared@contoso.com")

    assert result["can_access"] == []
    assert result["accessible_by"] == [
        {"display_name": "Delegate User", "upn": "delegate@contoso.com"}
    ]


@pytest.mark.anyio("asyncio")
async def test_get_mailbox_permissions_accessible_by_combines_raw_upn_and_mail_lookups():
    """accessible_by includes members found via both the requested mailbox UPN and the Graph mail value."""
    user_obj = {"id": "uid-shared", "displayName": "Help Desk", "mail": "helpdesk@company.com"}

    async def _fake_get_members(company_id: int, mailbox_email: str) -> list[dict]:
        if mailbox_email == "helpdesk@company.onmicrosoft.com":
            return [_db_member("raw@company.com", "Raw Lookup User")]
        if mailbox_email == "helpdesk@company.com":
            return [_db_member("mail@company.com", "Mail Lookup User")]
        return []

    with (
        patch.object(m365_service, "acquire_access_token", AsyncMock(return_value="tok")),
        patch.object(m365_service, "_graph_get", AsyncMock(return_value=user_obj)),
        patch.object(m365_service, "_graph_get_all", AsyncMock(return_value=[])),
        patch.object(m365_service, "_get_mailbox_group_members", AsyncMock(return_value=[])),
        patch.object(m365_repo, "get_mailbox_members", AsyncMock(side_effect=_fake_get_members)),
    ):
        result = await get_mailbox_permissions(1, "helpdesk@company.onmicrosoft.com")

    assert result["accessible_by"] == [
        {"display_name": "Mail Lookup User", "upn": "mail@company.com"},
        {"display_name": "Raw Lookup User", "upn": "raw@company.com"},
    ]


@pytest.mark.anyio("asyncio")
async def test_get_mailbox_permissions_lists_sorted_alphabetically():
    """can_access and accessible_by lists are sorted alphabetically by display_name."""
    user_obj = {"id": "uid-1", "displayName": "Alice", "mail": "alice@contoso.com"}
    groups = [
        _make_group("g2", "Zebra Mailbox", "zebra@contoso.com"),
        _make_group("g1", "Alpha Mailbox", "alpha@contoso.com"),
    ]
    db_members = [
        _db_member("zoe@contoso.com", "Zoe"),
        _db_member("aaron@contoso.com", "Aaron"),
    ]

    with (
        patch.object(m365_service, "acquire_access_token", AsyncMock(return_value="tok")),
        patch.object(m365_service, "_graph_get", AsyncMock(return_value=user_obj)),
        patch.object(m365_service, "_graph_get_all", AsyncMock(return_value=groups)),
        patch.object(m365_service, "_get_mailbox_group_members", AsyncMock(return_value=[])),
        patch.object(m365_repo, "get_mailbox_members", AsyncMock(return_value=db_members)),
    ):
        result = await get_mailbox_permissions(1, "alice@contoso.com")

    assert result["can_access"][0]["display_name"] == "Alpha Mailbox"
    assert result["can_access"][1]["display_name"] == "Zebra Mailbox"
    assert result["accessible_by"][0]["display_name"] == "Aaron"
    assert result["accessible_by"][1]["display_name"] == "Zoe"


# ---------------------------------------------------------------------------
# accessible_by – proxyAddresses alias matching
# ---------------------------------------------------------------------------


@pytest.mark.anyio("asyncio")
async def test_get_mailbox_permissions_accessible_by_uses_proxy_addresses():
    """accessible_by finds members stored under a proxyAddresses alias.

    When the sync stores members keyed by an M365 group's onmicrosoft.com
    address but the UI passes the custom-domain UPN, proxyAddresses bridges
    the gap by providing the alternate email that matches the DB key.
    """
    user_obj = {
        "id": "uid-shared",
        "displayName": "Payroll",
        "mail": "payroll@company.com.au",
        "proxyAddresses": [
            "SMTP:payroll@company.com.au",
            "smtp:payroll@company.onmicrosoft.com",
        ],
    }

    async def _fake_get_members(company_id: int, mailbox_email: str) -> list[dict]:
        if mailbox_email == "payroll@company.onmicrosoft.com":
            return [_db_member("employee1@company.com.au", "Employee One")]
        return []

    with (
        patch.object(m365_service, "acquire_access_token", AsyncMock(return_value="tok")),
        patch.object(m365_service, "_graph_get", AsyncMock(return_value=user_obj)),
        patch.object(m365_service, "_graph_get_all", AsyncMock(return_value=[])),
        patch.object(m365_service, "_get_mailbox_group_members", AsyncMock(return_value=[])),
        patch.object(m365_repo, "get_mailbox_members", AsyncMock(side_effect=_fake_get_members)),
    ):
        result = await get_mailbox_permissions(1, "payroll@company.com.au")

    assert len(result["accessible_by"]) == 1
    assert result["accessible_by"][0]["upn"] == "employee1@company.com.au"


@pytest.mark.anyio("asyncio")
async def test_get_mailbox_permissions_proxy_addresses_deduplicates_members():
    """Members found via multiple alias lookups are de-duplicated in accessible_by."""
    user_obj = {
        "id": "uid-shared",
        "displayName": "Payroll",
        "mail": "payroll@company.com.au",
        "proxyAddresses": [
            "SMTP:payroll@company.com.au",
            "smtp:payroll@company.onmicrosoft.com",
        ],
    }

    async def _fake_get_members(company_id: int, mailbox_email: str) -> list[dict]:
        # Same member appears under both email keys
        if mailbox_email in ("payroll@company.com.au", "payroll@company.onmicrosoft.com"):
            return [_db_member("employee1@company.com.au", "Employee One")]
        return []

    with (
        patch.object(m365_service, "acquire_access_token", AsyncMock(return_value="tok")),
        patch.object(m365_service, "_graph_get", AsyncMock(return_value=user_obj)),
        patch.object(m365_service, "_graph_get_all", AsyncMock(return_value=[])),
        patch.object(m365_service, "_get_mailbox_group_members", AsyncMock(return_value=[])),
        patch.object(m365_repo, "get_mailbox_members", AsyncMock(side_effect=_fake_get_members)),
    ):
        result = await get_mailbox_permissions(1, "payroll@company.com.au")

    # Employee1 should appear exactly once despite being found via multiple lookups.
    assert len(result["accessible_by"]) == 1
    assert result["accessible_by"][0]["upn"] == "employee1@company.com.au"


@pytest.mark.anyio("asyncio")
async def test_get_mailbox_permissions_proxy_addresses_live_group_lookup():
    """Live group member lookup also uses proxyAddresses aliases.

    When the backing M365 group email differs from the mailbox UPN, the live
    _get_mailbox_group_members call should try the alias from proxyAddresses
    to find the group.
    """
    user_obj = {
        "id": "uid-shared",
        "displayName": "Payroll",
        "mail": "payroll@company.com.au",
        "proxyAddresses": [
            "SMTP:payroll@company.com.au",
            "smtp:payroll@company.onmicrosoft.com",
        ],
    }

    captured_group_lookups: list[str] = []

    async def _fake_group_members(access_token: str, email: str) -> list[dict]:
        captured_group_lookups.append(email)
        if email == "payroll@company.onmicrosoft.com":
            return [{"displayName": "Employee One", "userPrincipalName": "employee1@company.com.au"}]
        return []

    with (
        patch.object(m365_service, "acquire_access_token", AsyncMock(return_value="tok")),
        patch.object(m365_service, "_graph_get", AsyncMock(return_value=user_obj)),
        patch.object(m365_service, "_graph_get_all", AsyncMock(return_value=[])),
        patch.object(m365_service, "_get_mailbox_group_members", AsyncMock(side_effect=_fake_group_members)),
        patch.object(m365_repo, "get_mailbox_members", AsyncMock(return_value=[])),
    ):
        result = await get_mailbox_permissions(1, "payroll@company.com.au")

    assert len(result["accessible_by"]) == 1
    assert result["accessible_by"][0]["upn"] == "employee1@company.com.au"
    # Both the primary email and the onmicrosoft alias should have been tried.
    assert "payroll@company.com.au" in captured_group_lookups
    assert "payroll@company.onmicrosoft.com" in captured_group_lookups


# ---------------------------------------------------------------------------
# accessible_by – live Exchange Online Get-MailboxPermission
# ---------------------------------------------------------------------------


@pytest.mark.anyio("asyncio")
async def test_get_mailbox_permissions_accessible_by_includes_live_exo_permissions():
    """accessible_by includes results from a live EXO Get-MailboxPermission call."""
    user_obj = {"id": "uid-1", "displayName": "Alice", "mail": "alice@contoso.com"}
    exo_records = [
        {
            "User": "Bob Delegate <bob@contoso.com>",
            "AccessRights": ["FullAccess"],
            "Deny": False,
        },
    ]

    with (
        patch.object(m365_service, "acquire_access_token", AsyncMock(return_value="tok")),
        patch.object(m365_service, "_graph_get", AsyncMock(return_value=user_obj)),
        patch.object(m365_service, "_graph_get_all", AsyncMock(return_value=[])),
        patch.object(m365_service, "_get_mailbox_group_members", AsyncMock(return_value=[])),
        patch.object(m365_repo, "get_mailbox_members", AsyncMock(return_value=[])),
        patch.object(
            m365_service,
            "_acquire_exo_access_token",
            AsyncMock(return_value=("exo-tok", "tenant-id")),
        ),
        patch.object(
            m365_service,
            "_exo_get_mailbox_permission",
            AsyncMock(return_value=exo_records),
        ),
    ):
        result = await get_mailbox_permissions(1, "alice@contoso.com")

    assert len(result["accessible_by"]) == 1
    assert result["accessible_by"][0]["upn"] == "bob@contoso.com"
    assert result["accessible_by"][0]["display_name"] == "Bob Delegate"


@pytest.mark.anyio("asyncio")
async def test_get_mailbox_permissions_accessible_by_exo_supplements_db_data():
    """Live EXO results merge with DB members; duplicates are de-duplicated."""
    user_obj = {"id": "uid-1", "displayName": "Alice", "mail": "alice@contoso.com"}
    db_members = [_db_member("bob@contoso.com", "Bob Smith")]
    exo_records = [
        {
            "User": "Bob Smith <bob@contoso.com>",
            "AccessRights": ["FullAccess"],
            "Deny": False,
        },
        {
            "User": "Carol Jones <carol@contoso.com>",
            "AccessRights": ["FullAccess"],
            "Deny": False,
        },
    ]

    with (
        patch.object(m365_service, "acquire_access_token", AsyncMock(return_value="tok")),
        patch.object(m365_service, "_graph_get", AsyncMock(return_value=user_obj)),
        patch.object(m365_service, "_graph_get_all", AsyncMock(return_value=[])),
        patch.object(m365_service, "_get_mailbox_group_members", AsyncMock(return_value=[])),
        patch.object(m365_repo, "get_mailbox_members", AsyncMock(return_value=db_members)),
        patch.object(
            m365_service,
            "_acquire_exo_access_token",
            AsyncMock(return_value=("exo-tok", "tenant-id")),
        ),
        patch.object(
            m365_service,
            "_exo_get_mailbox_permission",
            AsyncMock(return_value=exo_records),
        ),
    ):
        result = await get_mailbox_permissions(1, "alice@contoso.com")

    # Bob appears once (de-duplicated), Carol added from live EXO
    upns = {item["upn"] for item in result["accessible_by"]}
    assert upns == {"bob@contoso.com", "carol@contoso.com"}


@pytest.mark.anyio("asyncio")
async def test_get_mailbox_permissions_accessible_by_exo_failure_silent():
    """When EXO token acquisition fails, accessible_by still returns DB data."""
    user_obj = {"id": "uid-1", "displayName": "Alice", "mail": "alice@contoso.com"}
    db_members = [_db_member("bob@contoso.com", "Bob Smith")]

    with (
        patch.object(m365_service, "acquire_access_token", AsyncMock(return_value="tok")),
        patch.object(m365_service, "_graph_get", AsyncMock(return_value=user_obj)),
        patch.object(m365_service, "_graph_get_all", AsyncMock(return_value=[])),
        patch.object(m365_service, "_get_mailbox_group_members", AsyncMock(return_value=[])),
        patch.object(m365_repo, "get_mailbox_members", AsyncMock(return_value=db_members)),
        patch.object(
            m365_service,
            "_acquire_exo_access_token",
            AsyncMock(side_effect=M365Error("EXO unavailable")),
        ),
    ):
        result = await get_mailbox_permissions(1, "alice@contoso.com")

    # DB data still present despite EXO failure
    assert len(result["accessible_by"]) == 1
    assert result["accessible_by"][0]["upn"] == "bob@contoso.com"
