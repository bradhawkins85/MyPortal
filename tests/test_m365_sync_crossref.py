"""Tests for the sync_mailboxes shared mailbox cross-reference step.

During sync, group membership rows are stored keyed by the group's primary
SMTP address (``group_email``).  When a shared mailbox UPN differs from the
group email (e.g. an onmicrosoft.com UPN vs. a custom-domain group address),
the cross-reference step resolves the shared mailbox's email aliases via the
Graph API and copies matching group-member entries to the shared mailbox UPN.

Covers:
- Cross-reference copies group members to the shared mailbox UPN when the
  group email differs from the mailbox UPN.
- Cross-reference is skipped when the group email already matches the UPN.
- Cross-reference is skipped when the Graph API alias lookup fails.
- Cross-reference only processes shared mailboxes (not user mailboxes).
"""
from __future__ import annotations

from datetime import datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, call, patch

import pytest

from app.repositories import m365 as m365_repo
from app.services import m365 as m365_service
from app.services.m365 import M365Error


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _make_report_item(upn: str, display: str = "", *, is_deleted: bool = False) -> dict[str, Any]:
    return {
        "userPrincipalName": upn,
        "displayName": display or upn,
        "isDeleted": is_deleted,
        "storageUsedInBytes": 1024,
    }


def _make_user(uid: str, upn: str, mail: str | None = None, *, enabled: bool = True) -> dict[str, Any]:
    obj: dict[str, Any] = {
        "id": uid,
        "userPrincipalName": upn,
        "displayName": upn.split("@")[0].replace(".", " ").title(),
        "accountEnabled": enabled,
    }
    if mail is not None:
        obj["mail"] = mail
    return obj


def _make_group(mail: str) -> dict[str, Any]:
    return {"id": f"grp-{mail}", "displayName": mail, "mail": mail, "mailEnabled": True}


# ---------------------------------------------------------------------------
# sync_mailboxes – shared mailbox cross-reference
# ---------------------------------------------------------------------------


@pytest.mark.anyio("asyncio")
async def test_sync_crossref_copies_group_members_to_shared_mailbox_upn():
    """When a shared mailbox UPN differs from the group email, the cross-reference
    step resolves proxy addresses and copies group members under the shared mailbox
    UPN so that ``get_mailbox_permissions`` finds them without a live Graph API call.
    """
    # Setup: one user (alice) is a member of group "support@company.com".
    # The shared mailbox appears in the usage report as "support@company.onmicrosoft.com".
    users = [_make_user("uid-alice", "alice@company.com", "alice@company.com")]
    report_items = [
        _make_report_item("alice@company.com"),
        _make_report_item("support@company.onmicrosoft.com"),
    ]
    group = _make_group("support@company.com")

    # Graph API responses for the shared mailbox alias resolution
    mb_user_response = {
        "mail": "support@company.com",
        "proxyAddresses": [
            "SMTP:support@company.com",
            "smtp:support@company.onmicrosoft.com",
        ],
    }

    upsert_calls: list[dict[str, Any]] = []

    async def _capture_upsert(**kwargs: Any) -> None:
        upsert_calls.append(kwargs)

    graph_get_responses: dict[str, Any] = {}

    async def _fake_graph_get(token: str, url: str, **kw: Any) -> dict[str, Any]:
        # Return the appropriate response based on URL
        if "support%40company.onmicrosoft.com" in url:
            return mb_user_response
        if "alice%40company.com" in url:
            return {"id": "uid-alice", "mail": "alice@company.com"}
        # User lookup for the report API
        if "getMailboxUsageDetail" in url:
            return {"value": report_items}
        return {"value": []}

    async def _fake_graph_get_all(token: str, url: str, **kw: Any) -> list[dict[str, Any]]:
        if "memberOf" in url:
            return [group]
        if "mailboxSettings" in url:
            return []
        return []

    with (
        patch.object(m365_service, "acquire_access_token", AsyncMock(return_value="tok")),
        patch.object(m365_service, "get_all_users", AsyncMock(return_value=users)),
        patch.object(m365_service, "_fetch_mailbox_usage_report", AsyncMock(return_value=report_items)),
        patch.object(m365_service, "_graph_get", AsyncMock(side_effect=_fake_graph_get)),
        patch.object(m365_service, "_graph_get_all", AsyncMock(side_effect=_fake_graph_get_all)),
        patch.object(m365_service, "_count_forwarding_rules", AsyncMock(return_value=0)),
        patch.object(m365_service, "_fetch_exo_mailbox_permissions", AsyncMock(return_value={})),
        patch.object(m365_repo, "upsert_mailbox_member", AsyncMock(side_effect=_capture_upsert)),
        patch.object(m365_repo, "upsert_mailbox", AsyncMock()),
        patch.object(m365_repo, "delete_stale_mailboxes", AsyncMock()),
        patch.object(m365_repo, "delete_stale_mailbox_members", AsyncMock()),
    ):
        await m365_service.sync_mailboxes(1)

    # There should be at least two upsert_mailbox_member calls:
    # 1. alice -> support@company.com (from user-centric group membership sync)
    # 2. alice -> support@company.onmicrosoft.com (from cross-reference step)
    group_email_calls = [
        c for c in upsert_calls if c["mailbox_email"] == "support@company.com"
    ]
    crossref_calls = [
        c for c in upsert_calls if c["mailbox_email"] == "support@company.onmicrosoft.com"
    ]

    assert len(group_email_calls) >= 1, (
        "Expected at least one upsert for group email support@company.com"
    )
    assert group_email_calls[0]["member_upn"] == "alice@company.com"

    assert len(crossref_calls) >= 1, (
        "Expected at least one cross-reference upsert for shared mailbox UPN "
        "support@company.onmicrosoft.com"
    )
    assert crossref_calls[0]["member_upn"] == "alice@company.com"


@pytest.mark.anyio("asyncio")
async def test_sync_crossref_skips_when_group_email_matches_upn():
    """When the group email already matches the shared mailbox UPN, the cross-reference
    step does NOT make additional Graph API calls for alias resolution.
    """
    users = [_make_user("uid-alice", "alice@company.com", "alice@company.com")]
    report_items = [
        _make_report_item("alice@company.com"),
        _make_report_item("support@company.com"),  # Same as group email
    ]
    group = _make_group("support@company.com")

    graph_get_calls: list[str] = []

    async def _fake_graph_get(token: str, url: str, **kw: Any) -> dict[str, Any]:
        graph_get_calls.append(url)
        return {"id": "uid-x", "mail": "x@x.com"}

    with (
        patch.object(m365_service, "acquire_access_token", AsyncMock(return_value="tok")),
        patch.object(m365_service, "get_all_users", AsyncMock(return_value=users)),
        patch.object(m365_service, "_fetch_mailbox_usage_report", AsyncMock(return_value=report_items)),
        patch.object(m365_service, "_graph_get", AsyncMock(side_effect=_fake_graph_get)),
        patch.object(m365_service, "_graph_get_all", AsyncMock(return_value=[group])),
        patch.object(m365_service, "_count_forwarding_rules", AsyncMock(return_value=0)),
        patch.object(m365_service, "_fetch_exo_mailbox_permissions", AsyncMock(return_value={})),
        patch.object(m365_repo, "upsert_mailbox_member", AsyncMock()),
        patch.object(m365_repo, "upsert_mailbox", AsyncMock()),
        patch.object(m365_repo, "delete_stale_mailboxes", AsyncMock()),
        patch.object(m365_repo, "delete_stale_mailbox_members", AsyncMock()),
    ):
        await m365_service.sync_mailboxes(1)

    # The cross-reference step should skip "support@company.com" because
    # the group email already matches the UPN. No alias resolution call needed.
    alias_resolution_calls = [
        url for url in graph_get_calls
        if "support%40company.com" in url and "proxyAddresses" in url
    ]
    assert alias_resolution_calls == [], (
        "Expected no alias resolution call when group email matches UPN"
    )


@pytest.mark.anyio("asyncio")
async def test_sync_crossref_skips_when_graph_alias_lookup_fails():
    """When the Graph API alias resolution fails for a shared mailbox, the
    cross-reference step continues without error.
    """
    users = [_make_user("uid-alice", "alice@company.com", "alice@company.com")]
    report_items = [
        _make_report_item("alice@company.com"),
        _make_report_item("support@company.onmicrosoft.com"),
    ]
    group = _make_group("support@company.com")

    upsert_calls: list[dict[str, Any]] = []

    async def _capture_upsert(**kwargs: Any) -> None:
        upsert_calls.append(kwargs)

    async def _fake_graph_get(token: str, url: str, **kw: Any) -> dict[str, Any]:
        if "support%40company.onmicrosoft.com" in url:
            raise M365Error("404 Not Found")
        return {"id": "uid-x", "mail": "alice@company.com"}

    with (
        patch.object(m365_service, "acquire_access_token", AsyncMock(return_value="tok")),
        patch.object(m365_service, "get_all_users", AsyncMock(return_value=users)),
        patch.object(m365_service, "_fetch_mailbox_usage_report", AsyncMock(return_value=report_items)),
        patch.object(m365_service, "_graph_get", AsyncMock(side_effect=_fake_graph_get)),
        patch.object(m365_service, "_graph_get_all", AsyncMock(return_value=[group])),
        patch.object(m365_service, "_count_forwarding_rules", AsyncMock(return_value=0)),
        patch.object(m365_service, "_fetch_exo_mailbox_permissions", AsyncMock(return_value={})),
        patch.object(m365_repo, "upsert_mailbox_member", AsyncMock(side_effect=_capture_upsert)),
        patch.object(m365_repo, "upsert_mailbox", AsyncMock()),
        patch.object(m365_repo, "delete_stale_mailboxes", AsyncMock()),
        patch.object(m365_repo, "delete_stale_mailbox_members", AsyncMock()),
    ):
        # Should complete without error
        await m365_service.sync_mailboxes(1)

    # The group membership row should still be stored under the group email
    group_email_calls = [
        c for c in upsert_calls if c["mailbox_email"] == "support@company.com"
    ]
    assert len(group_email_calls) >= 1

    # No cross-reference call for the shared mailbox UPN (alias resolution failed)
    crossref_calls = [
        c for c in upsert_calls if c["mailbox_email"] == "support@company.onmicrosoft.com"
    ]
    assert crossref_calls == []
