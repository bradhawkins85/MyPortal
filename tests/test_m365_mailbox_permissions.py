"""Tests for the get_mailbox_permissions M365 service function.

Covers:
- Returns correct can_access list from mail-enabled group memberships
- Ignores groups that are not mail-enabled
- Returns correct accessible_by list from M365 group members
- Returns empty lists when user lookup fails (M365Error)
- Returns empty can_access when memberOf call fails
- Returns empty accessible_by when group filter returns no results
- Returns empty accessible_by when group member fetch fails
- Lists are sorted alphabetically by display_name
- Group filter uses user object mail property (not raw UPN) to handle UPN/email mismatch
- Uses transitiveMembers endpoint so nested group members are included
"""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

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


def _make_user(
    user_id: str,
    display_name: str,
    upn: str,
) -> dict[str, Any]:
    return {
        "@odata.type": "#microsoft.graph.user",
        "id": user_id,
        "displayName": display_name,
        "userPrincipalName": upn,
    }


@pytest.mark.anyio("asyncio")
async def test_get_mailbox_permissions_can_access_returns_mail_enabled_groups():
    """Mail-enabled group memberships appear in can_access."""
    user_obj = {"id": "uid-1", "displayName": "Alice"}
    groups = [
        _make_group("g1", "Sales Team", "sales@contoso.com"),
        _make_group("g2", "Finance", "finance@contoso.com"),
    ]
    group_filter_resp = {"value": []}  # no backing group for "accessible_by"

    with (
        patch.object(m365_service, "acquire_access_token", AsyncMock(return_value="tok")),
        patch.object(m365_service, "_graph_get", AsyncMock(side_effect=[user_obj, group_filter_resp])),
        patch.object(m365_service, "_graph_get_all", AsyncMock(return_value=groups)),
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
    group_filter_resp = {"value": []}

    with (
        patch.object(m365_service, "acquire_access_token", AsyncMock(return_value="tok")),
        patch.object(m365_service, "_graph_get", AsyncMock(side_effect=[user_obj, group_filter_resp])),
        patch.object(m365_service, "_graph_get_all", AsyncMock(return_value=groups)),
    ):
        result = await get_mailbox_permissions(1, "alice@contoso.com")

    assert len(result["can_access"]) == 1
    assert result["can_access"][0]["email"] == "sales@contoso.com"


@pytest.mark.anyio("asyncio")
async def test_get_mailbox_permissions_accessible_by_returns_group_members():
    """Members of the backing M365 group appear in accessible_by."""
    user_obj = {"id": "uid-shared", "displayName": "Shared Box"}
    groups_empty: list[dict[str, Any]] = []  # memberOf returns nothing
    backing_group = [{"id": "group-shared", "displayName": "Shared Mailbox", "mail": "shared@contoso.com"}]
    group_filter_resp = {"value": backing_group}
    members = [
        _make_user("u1", "Bob Smith", "bob@contoso.com"),
        _make_user("u2", "Carol Jones", "carol@contoso.com"),
    ]

    def _graph_get_all_side_effect(token: str, url: str) -> Any:
        if "memberOf" in url:
            return groups_empty
        return members  # group members

    with (
        patch.object(m365_service, "acquire_access_token", AsyncMock(return_value="tok")),
        patch.object(m365_service, "_graph_get", AsyncMock(side_effect=[user_obj, group_filter_resp])),
        patch.object(m365_service, "_graph_get_all", AsyncMock(side_effect=_graph_get_all_side_effect)),
    ):
        result = await get_mailbox_permissions(1, "shared@contoso.com")

    assert len(result["accessible_by"]) == 2
    upns = {item["upn"] for item in result["accessible_by"]}
    assert "bob@contoso.com" in upns
    assert "carol@contoso.com" in upns


@pytest.mark.anyio("asyncio")
async def test_get_mailbox_permissions_returns_empty_on_user_lookup_failure():
    """If the user lookup raises M365Error, both lists are empty."""
    with (
        patch.object(m365_service, "acquire_access_token", AsyncMock(return_value="tok")),
        patch.object(m365_service, "_graph_get", AsyncMock(side_effect=M365Error("404"))),
    ):
        result = await get_mailbox_permissions(1, "missing@contoso.com")

    assert result["can_access"] == []
    assert result["accessible_by"] == []


@pytest.mark.anyio("asyncio")
async def test_get_mailbox_permissions_empty_can_access_on_member_of_failure():
    """If memberOf raises M365Error, can_access is empty but accessible_by still attempted."""
    user_obj = {"id": "uid-1", "displayName": "Alice"}
    group_filter_resp = {"value": []}

    def _graph_get_all_side_effect(token: str, url: str) -> Any:
        raise M365Error("memberOf failed")

    with (
        patch.object(m365_service, "acquire_access_token", AsyncMock(return_value="tok")),
        patch.object(m365_service, "_graph_get", AsyncMock(side_effect=[user_obj, group_filter_resp])),
        patch.object(m365_service, "_graph_get_all", AsyncMock(side_effect=_graph_get_all_side_effect)),
    ):
        result = await get_mailbox_permissions(1, "alice@contoso.com")

    assert result["can_access"] == []
    assert result["accessible_by"] == []


@pytest.mark.anyio("asyncio")
async def test_get_mailbox_permissions_empty_accessible_by_when_no_backing_group():
    """When no M365 group matches the mailbox email, accessible_by is empty."""
    user_obj = {"id": "uid-1", "displayName": "Alice"}
    groups = [_make_group("g1", "Sales", "sales@contoso.com")]
    group_filter_resp = {"value": []}  # no backing group

    with (
        patch.object(m365_service, "acquire_access_token", AsyncMock(return_value="tok")),
        patch.object(m365_service, "_graph_get", AsyncMock(side_effect=[user_obj, group_filter_resp])),
        patch.object(m365_service, "_graph_get_all", AsyncMock(return_value=groups)),
    ):
        result = await get_mailbox_permissions(1, "alice@contoso.com")

    assert result["accessible_by"] == []


@pytest.mark.anyio("asyncio")
async def test_get_mailbox_permissions_accessible_by_empty_on_members_failure():
    """If group member fetch raises M365Error, accessible_by is empty."""
    user_obj = {"id": "uid-shared", "displayName": "Shared"}
    backing_group = [{"id": "group-id", "displayName": "Shared", "mail": "shared@contoso.com"}]
    group_filter_resp = {"value": backing_group}
    groups_empty: list[dict[str, Any]] = []

    call_count = 0

    def _graph_get_all_side_effect(token: str, url: str) -> Any:
        nonlocal call_count
        call_count += 1
        if "memberOf" in url:
            return groups_empty
        raise M365Error("members failed")

    with (
        patch.object(m365_service, "acquire_access_token", AsyncMock(return_value="tok")),
        patch.object(m365_service, "_graph_get", AsyncMock(side_effect=[user_obj, group_filter_resp])),
        patch.object(m365_service, "_graph_get_all", AsyncMock(side_effect=_graph_get_all_side_effect)),
    ):
        result = await get_mailbox_permissions(1, "shared@contoso.com")

    assert result["accessible_by"] == []


@pytest.mark.anyio("asyncio")
async def test_get_mailbox_permissions_results_are_sorted():
    """can_access and accessible_by lists are sorted alphabetically by display_name."""
    user_obj = {"id": "uid-1", "displayName": "Alice"}
    groups = [
        _make_group("g2", "Zebra Mailbox", "zebra@contoso.com"),
        _make_group("g1", "Alpha Mailbox", "alpha@contoso.com"),
    ]
    backing_group = [{"id": "group-id", "displayName": "My Mailbox", "mail": "alice@contoso.com"}]
    group_filter_resp = {"value": backing_group}
    members = [
        _make_user("u2", "Zoe", "zoe@contoso.com"),
        _make_user("u1", "Aaron", "aaron@contoso.com"),
    ]

    def _graph_get_all_side_effect(token: str, url: str) -> Any:
        if "memberOf" in url:
            return groups
        return members

    with (
        patch.object(m365_service, "acquire_access_token", AsyncMock(return_value="tok")),
        patch.object(m365_service, "_graph_get", AsyncMock(side_effect=[user_obj, group_filter_resp])),
        patch.object(m365_service, "_graph_get_all", AsyncMock(side_effect=_graph_get_all_side_effect)),
    ):
        result = await get_mailbox_permissions(1, "alice@contoso.com")

    assert result["can_access"][0]["display_name"] == "Alpha Mailbox"
    assert result["can_access"][1]["display_name"] == "Zebra Mailbox"
    assert result["accessible_by"][0]["display_name"] == "Aaron"
    assert result["accessible_by"][1]["display_name"] == "Zoe"


@pytest.mark.anyio("asyncio")
async def test_get_mailbox_permissions_excludes_members_without_upn():
    """Group members without a userPrincipalName (e.g. nested groups) are excluded."""
    user_obj = {"id": "uid-shared", "displayName": "Shared"}
    backing_group = [{"id": "group-id", "displayName": "Shared", "mail": "shared@contoso.com"}]
    group_filter_resp = {"value": backing_group}
    groups_empty: list[dict[str, Any]] = []
    members = [
        {"id": "u1", "displayName": "Bob", "userPrincipalName": "bob@contoso.com"},
        {"id": "g-nested", "displayName": "Nested Group", "userPrincipalName": None},
    ]

    def _graph_get_all_side_effect(token: str, url: str) -> Any:
        if "memberOf" in url:
            return groups_empty
        return members

    with (
        patch.object(m365_service, "acquire_access_token", AsyncMock(return_value="tok")),
        patch.object(m365_service, "_graph_get", AsyncMock(side_effect=[user_obj, group_filter_resp])),
        patch.object(m365_service, "_graph_get_all", AsyncMock(side_effect=_graph_get_all_side_effect)),
    ):
        result = await get_mailbox_permissions(1, "shared@contoso.com")

    assert len(result["accessible_by"]) == 1
    assert result["accessible_by"][0]["upn"] == "bob@contoso.com"


@pytest.mark.anyio("asyncio")
async def test_get_mailbox_permissions_uses_mail_property_for_group_filter():
    """Group filter uses the user object's mail property (primary SMTP), not the raw UPN.

    Tenants where Exchange usage reports store an onmicrosoft.com UPN but the
    M365 group's primary email is a custom domain would get no results with the
    old implementation.  The fix reads the mail property from the user lookup
    and uses that for the group filter.
    """
    # User object has a different mail address than the UPN passed in.
    user_obj = {"id": "uid-shared", "displayName": "Help Desk", "mail": "helpdesk@company.com"}
    backing_group = [{"id": "group-id", "displayName": "Help Desk", "mail": "helpdesk@company.com"}]
    group_filter_resp = {"value": backing_group}
    groups_empty: list[dict[str, Any]] = []
    members = [_make_user("u1", "Alice", "alice@company.com")]

    captured_urls: list[str] = []

    def _graph_get_side_effect(token: str, url: str, *, extra_headers: Any = None) -> Any:
        captured_urls.append(url)
        if "groups?" in url:
            return group_filter_resp
        return user_obj

    def _graph_get_all_side_effect(token: str, url: str) -> Any:
        if "memberOf" in url:
            return groups_empty
        return members

    with (
        patch.object(m365_service, "acquire_access_token", AsyncMock(return_value="tok")),
        patch.object(m365_service, "_graph_get", AsyncMock(side_effect=_graph_get_side_effect)),
        patch.object(m365_service, "_graph_get_all", AsyncMock(side_effect=_graph_get_all_side_effect)),
    ):
        # UPN passed in uses onmicrosoft.com domain; group filter must use mail property.
        result = await get_mailbox_permissions(1, "helpdesk@company.onmicrosoft.com")

    assert len(result["accessible_by"]) == 1
    assert result["accessible_by"][0]["upn"] == "alice@company.com"

    # The group filter URL must use the mail property (helpdesk@company.com),
    # not the raw UPN (helpdesk@company.onmicrosoft.com).
    group_filter_url = next(u for u in captured_urls if "groups?" in u)
    assert "helpdesk@company.com" in group_filter_url
    assert "onmicrosoft.com" not in group_filter_url


@pytest.mark.anyio("asyncio")
async def test_get_mailbox_permissions_falls_back_to_upn_when_mail_absent():
    """When the user object has no mail property the raw UPN is used for the group filter."""
    user_obj = {"id": "uid-shared", "displayName": "Shared"}  # no mail field
    group_filter_resp = {"value": []}
    groups_empty: list[dict[str, Any]] = []

    captured_urls: list[str] = []

    def _graph_get_side_effect(token: str, url: str, *, extra_headers: Any = None) -> Any:
        captured_urls.append(url)
        if "groups?" in url:
            return group_filter_resp
        return user_obj

    with (
        patch.object(m365_service, "acquire_access_token", AsyncMock(return_value="tok")),
        patch.object(m365_service, "_graph_get", AsyncMock(side_effect=_graph_get_side_effect)),
        patch.object(m365_service, "_graph_get_all", AsyncMock(return_value=groups_empty)),
    ):
        result = await get_mailbox_permissions(1, "shared@contoso.com")

    assert result["accessible_by"] == []
    group_filter_url = next(u for u in captured_urls if "groups?" in u)
    assert "shared@contoso.com" in group_filter_url


@pytest.mark.anyio("asyncio")
async def test_get_mailbox_permissions_uses_transitive_members_endpoint():
    """The accessible_by query uses the transitiveMembers endpoint.

    This ensures users nested inside sub-groups are included, not just direct
    members of the top-level backing M365 group.
    """
    user_obj = {"id": "uid-shared", "displayName": "Shared Box", "mail": "shared@contoso.com"}
    backing_group = [{"id": "group-shared", "displayName": "Shared Mailbox", "mail": "shared@contoso.com"}]
    group_filter_resp = {"value": backing_group}
    groups_empty: list[dict[str, Any]] = []
    members = [_make_user("u1", "Bob", "bob@contoso.com")]

    captured_get_all_urls: list[str] = []

    def _graph_get_all_side_effect(token: str, url: str) -> Any:
        captured_get_all_urls.append(url)
        if "memberOf" in url:
            return groups_empty
        return members

    with (
        patch.object(m365_service, "acquire_access_token", AsyncMock(return_value="tok")),
        patch.object(m365_service, "_graph_get", AsyncMock(side_effect=[user_obj, group_filter_resp])),
        patch.object(m365_service, "_graph_get_all", AsyncMock(side_effect=_graph_get_all_side_effect)),
    ):
        result = await get_mailbox_permissions(1, "shared@contoso.com")

    assert len(result["accessible_by"]) == 1

    # Verify that transitiveMembers (not members) was called.
    members_url = next(u for u in captured_get_all_urls if "group" in u and "memberOf" not in u)
    assert "transitiveMembers" in members_url
    assert "/members" not in members_url.split("transitiveMembers")[0].split("/")[-1]
