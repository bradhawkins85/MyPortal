"""Tests for improved 403 error handling in mailbox sync and the verify-permissions route."""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services import m365 as m365_service
from app.services.m365 import M365Error


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


# ---------------------------------------------------------------------------
# sync_mailboxes – 403 from reports endpoint raises actionable message
# ---------------------------------------------------------------------------


@pytest.mark.anyio("asyncio")
async def test_sync_mailboxes_403_raises_actionable_error():
    """A 403 from the getMailboxUsageDetail endpoint raises a clear re-provision message."""
    with (
        patch.object(m365_service, "acquire_access_token", AsyncMock(return_value="tok")),
        patch.object(
            m365_service,
            "_graph_get_all",
            AsyncMock(side_effect=M365Error("Microsoft Graph request failed (403)", http_status=403)),
        ),
    ):
        with pytest.raises(M365Error) as exc_info:
            await m365_service.sync_mailboxes(1)

    error_message = str(exc_info.value)
    assert "Reports.Read.All" in error_message
    assert "Re-provision" in error_message
    assert "403 Forbidden" in error_message
    # The message should also mention waiting for permissions to propagate
    assert "wait" in error_message.lower(), (
        "Error message should mention waiting for permissions to propagate after re-provisioning"
    )


@pytest.mark.anyio("asyncio")
async def test_sync_mailboxes_non_403_error_propagates_unchanged():
    """Non-403 errors from the reports endpoint propagate without modification."""
    original_message = "Microsoft Graph request failed (500)"
    with (
        patch.object(m365_service, "acquire_access_token", AsyncMock(return_value="tok")),
        patch.object(
            m365_service,
            "_graph_get_all",
            AsyncMock(side_effect=M365Error(original_message)),
        ),
    ):
        with pytest.raises(M365Error) as exc_info:
            await m365_service.sync_mailboxes(1)

    assert str(exc_info.value) == original_message


# ---------------------------------------------------------------------------
# verify_tenant_permissions – basic happy/sad paths
# ---------------------------------------------------------------------------


@pytest.mark.anyio("asyncio")
async def test_verify_tenant_permissions_returns_all_ok_when_no_missing():
    """verify_tenant_permissions returns all_ok=True when no permissions are missing."""
    from app.services.m365 import _PROVISION_APP_ROLES

    fake_assignments = [{"appRoleId": role_id} for role_id in _PROVISION_APP_ROLES]

    with (
        patch.object(
            m365_service,
            "get_credentials",
            AsyncMock(return_value={"tenant_id": "t1", "client_id": "c1", "client_secret": "s1"}),
        ),
        patch.object(
            m365_service,
            "_exchange_token",
            AsyncMock(return_value=("tok", None, None)),
        ),
        patch.object(
            m365_service,
            "_graph_get",
            AsyncMock(
                side_effect=[
                    {"value": [{"id": "sp-id"}]},  # service principal lookup
                    {"value": fake_assignments},     # app role assignments
                ]
            ),
        ),
    ):
        result = await m365_service.verify_tenant_permissions(1)

    assert result["all_ok"] is True
    assert result["missing"] == []
    assert result["updated"] is False


@pytest.mark.anyio("asyncio")
async def test_verify_tenant_permissions_reports_missing_roles():
    """verify_tenant_permissions returns missing roles when the app lacks some permissions."""
    from app.services.m365 import _PROVISION_APP_ROLES

    # Only grant half the roles
    half_roles = _PROVISION_APP_ROLES[: len(_PROVISION_APP_ROLES) // 2]
    fake_assignments = [{"appRoleId": role_id} for role_id in half_roles]

    with (
        patch.object(
            m365_service,
            "get_credentials",
            AsyncMock(return_value={"tenant_id": "t1", "client_id": "c1", "client_secret": "s1"}),
        ),
        patch.object(
            m365_service,
            "_exchange_token",
            AsyncMock(return_value=("tok", None, None)),
        ),
        patch.object(
            m365_service,
            "_graph_get",
            AsyncMock(
                side_effect=[
                    {"value": [{"id": "sp-id"}]},
                    {"value": fake_assignments},
                ]
            ),
        ),
    ):
        result = await m365_service.verify_tenant_permissions(1)

    assert result["all_ok"] is False
    assert len(result["missing"]) > 0
    assert result["updated"] is False
