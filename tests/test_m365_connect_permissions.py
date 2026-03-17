"""Tests for try_grant_missing_permissions – the best-effort permission grant
that runs during the 'Authorize portal access' (connect) callback to ensure
newly-required permissions (e.g. Reports.Read.All, MailboxSettings.Read for
mailbox sync) are added to existing app registrations automatically.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from app.services import m365 as m365_service
from app.services.m365 import _PROVISION_APP_ROLES, M365Error


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_MAILBOX_REPORTS_ROLE = "230c1aed-a721-4c5d-9cb4-a90514e508ef"  # Reports.Read.All
_MAILBOX_SETTINGS_ROLE = "3b55498e-47ec-484f-8136-9013221c06a9"  # MailboxSettings.Read


def _fake_creds(client_id: str = "app-client-id") -> dict[str, Any]:
    return {"client_id": client_id, "tenant_id": "tenant-123"}


def _sp_response(sp_id: str = "sp-object-id") -> dict[str, Any]:
    return {"value": [{"id": sp_id}]}


def _assignments_response(role_ids: list[str]) -> dict[str, Any]:
    return {"value": [{"appRoleId": rid} for rid in role_ids]}


# ---------------------------------------------------------------------------
# Tests for try_grant_missing_permissions
# ---------------------------------------------------------------------------


@pytest.mark.anyio("asyncio")
async def test_try_grant_missing_permissions_grants_missing_roles():
    """Grants roles that are in _PROVISION_APP_ROLES but not yet assigned."""
    all_roles = list(_PROVISION_APP_ROLES)
    # Simulate an app that is missing the two mailbox permissions
    present_roles = [r for r in all_roles if r not in {_MAILBOX_REPORTS_ROLE, _MAILBOX_SETTINGS_ROLE}]

    granted: list[dict] = []

    async def mock_graph_get(token: str, url: str) -> dict:
        if "appRoleAssignments" in url:
            return _assignments_response(present_roles)
        if _GRAPH_APP_ID in url:
            return {"value": [{"id": "graph-sp-id"}]}
        # service principal lookup for the company app
        return _sp_response("sp-123")

    async def mock_graph_post(token: str, url: str, payload: dict) -> dict:
        granted.append(payload)
        return {"id": "new-assignment"}

    with (
        patch.object(m365_service, "get_credentials", AsyncMock(return_value=_fake_creds())),
        patch.object(m365_service, "_graph_get", side_effect=mock_graph_get),
        patch.object(m365_service, "_graph_post", side_effect=mock_graph_post),
    ):
        result = await m365_service.try_grant_missing_permissions(
            company_id=1,
            access_token="admin-token",
        )

    assert result is True, "Should return True when permissions were granted"
    granted_role_ids = {g["appRoleId"] for g in granted}
    assert _MAILBOX_REPORTS_ROLE in granted_role_ids, (
        "Reports.Read.All must be granted when missing"
    )
    assert _MAILBOX_SETTINGS_ROLE in granted_role_ids, (
        "MailboxSettings.Read must be granted when missing"
    )


@pytest.mark.anyio("asyncio")
async def test_try_grant_missing_permissions_noop_when_all_present():
    """No Graph POST calls when all required permissions are already assigned."""
    all_roles = list(_PROVISION_APP_ROLES)

    post_calls: list[str] = []

    async def mock_graph_get(token: str, url: str) -> dict:
        if "appRoleAssignments" in url:
            return _assignments_response(all_roles)
        return _sp_response()

    async def mock_graph_post(token: str, url: str, payload: dict) -> dict:
        post_calls.append(url)
        return {}

    with (
        patch.object(m365_service, "get_credentials", AsyncMock(return_value=_fake_creds())),
        patch.object(m365_service, "_graph_get", side_effect=mock_graph_get),
        patch.object(m365_service, "_graph_post", side_effect=mock_graph_post),
    ):
        result = await m365_service.try_grant_missing_permissions(
            company_id=1,
            access_token="admin-token",
        )

    assert result is False, "Should return False when no permissions were missing"
    assert post_calls == [], "No appRoleAssignment POSTs expected when all roles are present"


@pytest.mark.anyio("asyncio")
async def test_try_grant_missing_permissions_noop_when_no_credentials():
    """Returns False without error when no credentials are configured for the company."""
    with patch.object(m365_service, "get_credentials", AsyncMock(return_value=None)):
        result = await m365_service.try_grant_missing_permissions(
            company_id=99,
            access_token="token",
        )
    assert result is False


@pytest.mark.anyio("asyncio")
async def test_try_grant_missing_permissions_noop_when_sp_not_found():
    """Returns False without error when the service principal is not found."""

    async def mock_graph_get(token: str, url: str) -> dict:
        return {"value": []}  # empty – SP not found

    with (
        patch.object(m365_service, "get_credentials", AsyncMock(return_value=_fake_creds())),
        patch.object(m365_service, "_graph_get", side_effect=mock_graph_get),
    ):
        result = await m365_service.try_grant_missing_permissions(
            company_id=1,
            access_token="token",
        )
    assert result is False


@pytest.mark.anyio("asyncio")
async def test_try_grant_missing_permissions_continues_on_partial_failure():
    """If one role grant fails, the others are still attempted."""
    all_roles = list(_PROVISION_APP_ROLES)
    present_roles = [r for r in all_roles if r not in {_MAILBOX_REPORTS_ROLE, _MAILBOX_SETTINGS_ROLE}]

    granted: list[str] = []

    async def mock_graph_get(token: str, url: str) -> dict:
        if "appRoleAssignments" in url:
            return _assignments_response(present_roles)
        if _GRAPH_APP_ID in url:
            return {"value": [{"id": "graph-sp-id"}]}
        return _sp_response()

    call_count = 0

    async def mock_graph_post(token: str, url: str, payload: dict) -> dict:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise M365Error("403 Forbidden")
        granted.append(payload["appRoleId"])
        return {"id": "assignment"}

    with (
        patch.object(m365_service, "get_credentials", AsyncMock(return_value=_fake_creds())),
        patch.object(m365_service, "_graph_get", side_effect=mock_graph_get),
        patch.object(m365_service, "_graph_post", side_effect=mock_graph_post),
    ):
        # Should not raise even when one role grant fails
        result = await m365_service.try_grant_missing_permissions(
            company_id=1,
            access_token="admin-token",
        )

    # At least the second role should have been attempted
    assert call_count == 2, "Both missing roles should be attempted even when first fails"
    # The second role succeeded, so result should be True
    assert result is True, "Should return True when at least one permission was granted"


@pytest.mark.anyio("asyncio")
async def test_try_grant_missing_permissions_swallows_unexpected_errors():
    """Any unexpected exception is caught and logged rather than raised."""

    async def mock_graph_get(*args: Any, **kwargs: Any) -> dict:
        raise RuntimeError("network failure")

    with (
        patch.object(m365_service, "get_credentials", AsyncMock(return_value=_fake_creds())),
        patch.object(m365_service, "_graph_get", side_effect=mock_graph_get),
    ):
        # Must not raise
        result = await m365_service.try_grant_missing_permissions(
            company_id=1,
            access_token="token",
        )
    assert result is False, "Should return False when an unexpected error occurs"


# ---------------------------------------------------------------------------
# Tests for provision_app_registration – 409 Conflict handling
# ---------------------------------------------------------------------------


@pytest.mark.anyio("asyncio")
async def test_provision_app_registration_skips_409_role_assignments():
    """provision_app_registration must not fail when an appRoleAssignment returns 409."""
    from app.services.m365 import provision_app_registration, M365Error

    post_calls: list[dict] = []
    call_count = 0

    async def mock_graph_post(token: str, url: str, payload: dict) -> dict:
        nonlocal call_count
        call_count += 1
        post_calls.append({"url": url, "payload": payload})
        # Simulate 409 Conflict for the first appRoleAssignment
        if "appRoleAssignments" in url and call_count == 1:
            raise M365Error("Microsoft Graph POST failed (409)")
        # All other calls succeed
        if url.endswith("/addPassword"):
            return {"secretText": "test-secret", "keyId": "key-id-1"}
        return {"id": "created-id", "appId": "new-client-id"}

    async def mock_graph_get(token: str, url: str, **kwargs: Any) -> dict:
        if "filter=displayName" in url:
            return {"value": []}
        if "servicePrincipals" in url and _GRAPH_APP_ID in url:
            return {"value": [{"id": "graph-sp-id"}]}
        return {"value": [{"id": "new-sp-id"}]}

    with (
        patch.object(m365_service, "_graph_post", side_effect=mock_graph_post),
        patch.object(m365_service, "_graph_get", side_effect=mock_graph_get),
    ):
        result = await provision_app_registration(
            access_token="admin-token",
            display_name="Test App",
        )

    assert result["client_id"] == "new-client-id"
    assert result["client_secret"] == "test-secret"
    # All roles should have been attempted (not stopped at the first 409)
    role_assignment_calls = [c for c in post_calls if "appRoleAssignments" in c["url"]]
    assert len(role_assignment_calls) == len(_PROVISION_APP_ROLES), (
        "All role assignments must be attempted even when the first returns 409"
    )


# ---------------------------------------------------------------------------
# Tests for connect callback – access token cleared when permissions granted
# ---------------------------------------------------------------------------


@pytest.mark.anyio("asyncio")
async def test_try_grant_missing_permissions_returns_false_when_all_grants_fail():
    """Returns False when all permission grants fail (no new permissions added)."""
    all_roles = list(_PROVISION_APP_ROLES)
    present_roles = [r for r in all_roles if r not in {_MAILBOX_REPORTS_ROLE, _MAILBOX_SETTINGS_ROLE}]

    async def mock_graph_get(token: str, url: str) -> dict:
        if "appRoleAssignments" in url:
            return _assignments_response(present_roles)
        if _GRAPH_APP_ID in url:
            return {"value": [{"id": "graph-sp-id"}]}
        return _sp_response()

    async def mock_graph_post(token: str, url: str, payload: dict) -> dict:
        raise M365Error("403 Forbidden – insufficient privileges")

    with (
        patch.object(m365_service, "get_credentials", AsyncMock(return_value=_fake_creds())),
        patch.object(m365_service, "_graph_get", side_effect=mock_graph_get),
        patch.object(m365_service, "_graph_post", side_effect=mock_graph_post),
    ):
        result = await m365_service.try_grant_missing_permissions(
            company_id=1,
            access_token="admin-token",
        )

    assert result is False, "Should return False when all grants fail"


# ---------------------------------------------------------------------------
# Constant checks – ensure mailbox permissions are present in _PROVISION_APP_ROLES
# ---------------------------------------------------------------------------


def test_provision_app_roles_includes_reports_read_all():
    """_PROVISION_APP_ROLES must include Reports.Read.All for mailbox sync."""
    assert _MAILBOX_REPORTS_ROLE in _PROVISION_APP_ROLES, (
        "Reports.Read.All (230c1aed-a721-4c5d-9cb4-a90514e508ef) must be in _PROVISION_APP_ROLES"
    )


def test_provision_app_roles_includes_mailbox_settings_read():
    """_PROVISION_APP_ROLES must include MailboxSettings.Read for mailbox sync."""
    assert _MAILBOX_SETTINGS_ROLE in _PROVISION_APP_ROLES, (
        "MailboxSettings.Read (3b55498e-47ec-484f-8136-9013221c06a9) must be in _PROVISION_APP_ROLES"
    )


# ---------------------------------------------------------------------------
# Import the _GRAPH_APP_ID constant used in mock
# ---------------------------------------------------------------------------

from app.services.m365 import _GRAPH_APP_ID  # noqa: E402
