"""Tests for the M365 best-practices 403/permission self-heal logic.

Covers the fixes for the cluster of "Microsoft Graph request failed (403)"
errors reported on best-practice and CIS Intune Benchmark checks
(Conditional Access policies, named locations, authorization policy,
authentication methods policy, Secure Score, sign-in logs, device
compliance policies, /admin/reportSettings, etc.).

The root cause is that the tenant's enterprise-app service principal is
missing app role assignments for permissions that were added to the
required set after the app was originally provisioned.
``run_best_practices`` now mirrors the self-heal pattern already used by
``sync_company_licenses`` / ``sync_mailboxes``: when a stored delegated
token is available, ``try_grant_missing_permissions`` is called once at
the start of the run and the app-only token is force-refreshed if any new
roles were granted.

Also covers:
- ``ReportSettings.ReadWrite.All`` is in ``_PROVISION_APP_ROLES`` so the
  ``/admin/reportSettings`` check & PATCH remediation succeed.
- ``_check_audit_log_enabled`` now probes ``/auditLogs/directoryAudits``
  (which actually supports GET) instead of ``/security/auditLog/queries``
  (POST-only collection that returned HTTP 400).
"""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from app.services import cis_benchmark as cis_service
from app.services import m365 as m365_service
from app.services import m365_best_practices as bp_service
from app.services.cis_benchmark import STATUS_PASS, STATUS_UNKNOWN
from app.services.m365 import M365Error


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


# ---------------------------------------------------------------------------
# ReportSettings.ReadWrite.All in the required app roles
# ---------------------------------------------------------------------------


def test_provision_app_roles_includes_report_settings_readwrite_all() -> None:
    """The provisioning role list must include ReportSettings.ReadWrite.All so
    the "Display concealed names" best-practice check & its PATCH remediation
    against ``/admin/reportSettings`` are not rejected with S2SUnauthorized.
    """
    # Microsoft-published, well-known application permission ID for
    # ReportSettings.ReadWrite.All (the read-write variant covers both the
    # GET probe in ``_check_concealed_names`` and the PATCH used by the
    # remediation flow).
    report_settings_readwrite = "ee353f83-55ef-4b78-82da-555bfa2b4b95"
    assert report_settings_readwrite in m365_service._PROVISION_APP_ROLES


# ---------------------------------------------------------------------------
# _check_audit_log_enabled now uses the GET-able directoryAudits endpoint
# ---------------------------------------------------------------------------


@pytest.mark.anyio("asyncio")
async def test_check_audit_log_enabled_uses_directory_audits_endpoint() -> None:
    """The unified-audit-log probe must hit ``/auditLogs/directoryAudits``.

    The previous implementation called ``/security/auditLog/queries`` which is
    a POST-only collection and returned HTTP 400 for GET, producing the
    "Microsoft Graph request failed (400)" error reported by users.
    """
    captured_urls: list[str] = []

    async def mock_graph_get(token: str, url: str, **_: Any) -> dict[str, Any]:
        captured_urls.append(url)
        return {"value": []}

    with patch("app.services.cis_benchmark._graph_get", side_effect=mock_graph_get):
        result = await cis_service._check_audit_log_enabled("fake-token")

    assert len(captured_urls) == 1
    assert "/auditLogs/directoryAudits" in captured_urls[0]
    assert "/security/auditLog/queries" not in captured_urls[0]
    assert result["status"] == STATUS_PASS


@pytest.mark.anyio("asyncio")
async def test_check_audit_log_enabled_unknown_on_403() -> None:
    """A 403 from the new endpoint still maps to STATUS_UNKNOWN with a clear
    message about the missing AuditLog.Read.All permission.
    """
    with patch(
        "app.services.cis_benchmark._graph_get",
        side_effect=M365Error("Microsoft Graph request failed (403)", http_status=403),
    ):
        result = await cis_service._check_audit_log_enabled("fake-token")

    assert result["status"] == STATUS_UNKNOWN
    assert "AuditLog.Read.All" in result["details"]


# ---------------------------------------------------------------------------
# run_best_practices self-heals via try_grant_missing_permissions
# ---------------------------------------------------------------------------


@pytest.mark.anyio("asyncio")
async def test_run_best_practices_self_heals_missing_permissions() -> None:
    """When a stored delegated token is available and
    ``try_grant_missing_permissions`` reports that new roles were granted,
    ``run_best_practices`` must force-refresh the app-only token before
    running the checks.  This recovers tenants whose enterprise-app SP was
    missing roles that were added to the required set post-provisioning.
    """
    acquire_calls: list[dict[str, Any]] = []

    async def fake_acquire(company_id: int, *, force_client_credentials: bool = False) -> str:
        acquire_calls.append({"force": force_client_credentials})
        return "refreshed-token" if force_client_credentials else "stale-token"

    with (
        patch.object(bp_service, "acquire_access_token", side_effect=fake_acquire),
        patch.object(
            bp_service,
            "acquire_delegated_token",
            new=AsyncMock(return_value="delegated-token"),
        ),
        patch.object(
            bp_service,
            "try_grant_missing_permissions",
            new=AsyncMock(return_value=True),
        ),
        patch.object(
            bp_service, "get_enabled_check_ids", new=AsyncMock(return_value=set())
        ),
        patch.object(
            bp_service, "get_auto_remediate_check_ids", new=AsyncMock(return_value=set())
        ),
        patch.object(
            bp_service,
            "detect_tenant_capabilities",
            new=AsyncMock(return_value=set()),
        ),
        patch.object(bp_service.bp_repo, "upsert_result", new=AsyncMock()),
    ):
        results = await bp_service.run_best_practices(company_id=1)

    # Initial token acquisition + one forced refresh after granting new roles.
    assert len(acquire_calls) == 2
    assert acquire_calls[0]["force"] is False
    assert acquire_calls[1]["force"] is True
    # No checks enabled, so results is empty – we are only asserting the
    # self-heal control flow here.
    assert results == []


@pytest.mark.anyio("asyncio")
async def test_run_best_practices_no_refresh_when_nothing_granted() -> None:
    """When ``try_grant_missing_permissions`` reports that nothing was newly
    granted, the app-only token must NOT be force-refreshed (avoids an
    unnecessary OAuth round-trip).
    """
    acquire_calls: list[dict[str, Any]] = []

    async def fake_acquire(company_id: int, *, force_client_credentials: bool = False) -> str:
        acquire_calls.append({"force": force_client_credentials})
        return "token"

    with (
        patch.object(bp_service, "acquire_access_token", side_effect=fake_acquire),
        patch.object(
            bp_service,
            "acquire_delegated_token",
            new=AsyncMock(return_value="delegated-token"),
        ),
        patch.object(
            bp_service,
            "try_grant_missing_permissions",
            new=AsyncMock(return_value=False),
        ),
        patch.object(
            bp_service, "get_enabled_check_ids", new=AsyncMock(return_value=set())
        ),
        patch.object(
            bp_service, "get_auto_remediate_check_ids", new=AsyncMock(return_value=set())
        ),
        patch.object(
            bp_service,
            "detect_tenant_capabilities",
            new=AsyncMock(return_value=set()),
        ),
        patch.object(bp_service.bp_repo, "upsert_result", new=AsyncMock()),
    ):
        await bp_service.run_best_practices(company_id=1)

    assert len(acquire_calls) == 1
    assert acquire_calls[0]["force"] is False


@pytest.mark.anyio("asyncio")
async def test_run_best_practices_skips_self_heal_without_delegated_token() -> None:
    """When no delegated token is stored (admin never completed
    "Authorise portal access"), the self-heal path is skipped without
    raising and ``try_grant_missing_permissions`` is never invoked.
    """
    grant_mock = AsyncMock(return_value=True)

    with (
        patch.object(
            bp_service, "acquire_access_token", new=AsyncMock(return_value="token")
        ),
        patch.object(
            bp_service,
            "acquire_delegated_token",
            new=AsyncMock(return_value=None),
        ),
        patch.object(bp_service, "try_grant_missing_permissions", new=grant_mock),
        patch.object(
            bp_service, "get_enabled_check_ids", new=AsyncMock(return_value=set())
        ),
        patch.object(
            bp_service, "get_auto_remediate_check_ids", new=AsyncMock(return_value=set())
        ),
        patch.object(
            bp_service,
            "detect_tenant_capabilities",
            new=AsyncMock(return_value=set()),
        ),
        patch.object(bp_service.bp_repo, "upsert_result", new=AsyncMock()),
    ):
        await bp_service.run_best_practices(company_id=1)

    grant_mock.assert_not_called()


@pytest.mark.anyio("asyncio")
async def test_run_best_practices_self_heal_swallows_errors() -> None:
    """Errors raised by the self-heal helpers must never propagate – the
    benchmark run should still proceed using the original (possibly stale)
    app-only token.
    """
    with (
        patch.object(
            bp_service, "acquire_access_token", new=AsyncMock(return_value="token")
        ),
        patch.object(
            bp_service,
            "acquire_delegated_token",
            new=AsyncMock(side_effect=RuntimeError("boom")),
        ),
        patch.object(
            bp_service,
            "try_grant_missing_permissions",
            new=AsyncMock(return_value=False),
        ),
        patch.object(
            bp_service, "get_enabled_check_ids", new=AsyncMock(return_value=set())
        ),
        patch.object(
            bp_service, "get_auto_remediate_check_ids", new=AsyncMock(return_value=set())
        ),
        patch.object(
            bp_service,
            "detect_tenant_capabilities",
            new=AsyncMock(return_value=set()),
        ),
        patch.object(bp_service.bp_repo, "upsert_result", new=AsyncMock()),
    ):
        # Must not raise.
        results = await bp_service.run_best_practices(company_id=1)

    assert results == []
