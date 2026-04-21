"""Tests for the Microsoft 365 monitoring best-practice checks."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from app.services import cis_benchmark as cis_service
from app.services import m365_best_practices as bp_service
from app.services.cis_benchmark import (
    STATUS_FAIL,
    STATUS_PASS,
    STATUS_UNKNOWN,
)
from app.services.m365 import M365Error, _PROVISION_APP_ROLES


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


# ---------------------------------------------------------------------------
# Catalog wiring
# ---------------------------------------------------------------------------


_MONITOR_IDS = {
    "bp_monitor_sign_in_logs",
    "bp_monitor_risky_users",
    "bp_monitor_sign_in_risk_policy",
    "bp_monitor_user_risk_policy",
    "bp_monitor_named_locations",
    "bp_monitor_ca_report_only_policies",
    "bp_monitor_app_credential_expiry",
    "bp_monitor_cloud_admin_accounts",
    "bp_monitor_secure_score",
    "bp_monitor_mfa_registration_policy",
}


def test_all_monitor_check_ids_in_catalog():
    """All bp_monitor_* checks are exposed via the public catalog."""
    catalog_ids = {bp["id"] for bp in bp_service.list_best_practices()}
    missing = _MONITOR_IDS - catalog_ids
    assert not missing, f"missing monitor checks in catalog: {missing}"


def test_monitor_checks_have_remediation():
    for check_id in _MONITOR_IDS:
        assert len(cis_service.get_remediation(check_id)) > 10


def test_required_monitor_permissions_provisioned():
    """Application permissions for monitoring checks must be granted by provisioning."""
    required = {
        "dc5007c0-2d7d-4c42-879c-2dab87571379",  # IdentityRiskyUser.Read.All
        "9a5d68dd-52b0-4cc2-bd40-abcf44ac3a30",  # Application.Read.All
        "bf394140-e372-4bf9-a898-299cfc7564e5",  # SecurityEvents.Read.All
    }
    missing = required - set(_PROVISION_APP_ROLES)
    assert not missing, f"missing app roles: {missing}"


# ---------------------------------------------------------------------------
# bp_monitor_sign_in_logs
# ---------------------------------------------------------------------------


@pytest.mark.anyio("asyncio")
async def test_check_monitor_sign_in_logs_pass():
    with patch(
        "app.services.cis_benchmark._graph_get",
        new_callable=AsyncMock,
    ) as mock_get:
        mock_get.return_value = {"value": []}
        result = await cis_service._check_monitor_sign_in_logs("tok")
    assert result["status"] == STATUS_PASS


@pytest.mark.anyio("asyncio")
async def test_check_monitor_sign_in_logs_fail_on_403():
    with patch(
        "app.services.cis_benchmark._graph_get",
        side_effect=M365Error("403 Forbidden"),
    ):
        result = await cis_service._check_monitor_sign_in_logs("tok")
    assert result["status"] == STATUS_FAIL
    assert result["remediation"]


@pytest.mark.anyio("asyncio")
async def test_check_monitor_sign_in_logs_unknown_on_other_error():
    with patch(
        "app.services.cis_benchmark._graph_get",
        side_effect=M365Error("500 boom"),
    ):
        result = await cis_service._check_monitor_sign_in_logs("tok")
    assert result["status"] == STATUS_UNKNOWN


# ---------------------------------------------------------------------------
# bp_monitor_risky_users
# ---------------------------------------------------------------------------


@pytest.mark.anyio("asyncio")
async def test_check_monitor_risky_users_pass_when_none():
    with patch(
        "app.services.cis_benchmark._graph_get",
        new_callable=AsyncMock,
    ) as mock_get:
        mock_get.return_value = {"value": []}
        result = await cis_service._check_monitor_risky_users("tok")
    assert result["status"] == STATUS_PASS


@pytest.mark.anyio("asyncio")
async def test_check_monitor_risky_users_fail_when_present():
    with patch(
        "app.services.cis_benchmark._graph_get",
        new_callable=AsyncMock,
    ) as mock_get:
        mock_get.return_value = {
            "value": [
                {"id": "u1", "userPrincipalName": "alice@example.com", "riskLevel": "high"},
                {"id": "u2", "userPrincipalName": "bob@example.com", "riskLevel": "medium"},
            ]
        }
        result = await cis_service._check_monitor_risky_users("tok")
    assert result["status"] == STATUS_FAIL
    assert "alice@example.com" in result["details"]


@pytest.mark.anyio("asyncio")
async def test_check_monitor_risky_users_unknown_on_error():
    with patch(
        "app.services.cis_benchmark._graph_get",
        side_effect=M365Error("nope"),
    ):
        result = await cis_service._check_monitor_risky_users("tok")
    assert result["status"] == STATUS_UNKNOWN


# ---------------------------------------------------------------------------
# bp_monitor_sign_in_risk_policy
# ---------------------------------------------------------------------------


@pytest.mark.anyio("asyncio")
async def test_check_monitor_sign_in_risk_policy_pass():
    with patch(
        "app.services.cis_benchmark._graph_get",
        new_callable=AsyncMock,
    ) as mock_get:
        mock_get.return_value = {"isEnabled": True, "riskLevel": "medium"}
        result = await cis_service._check_monitor_sign_in_risk_policy("tok")
    assert result["status"] == STATUS_PASS


@pytest.mark.anyio("asyncio")
async def test_check_monitor_sign_in_risk_policy_fail_when_disabled():
    with patch(
        "app.services.cis_benchmark._graph_get",
        new_callable=AsyncMock,
    ) as mock_get:
        mock_get.return_value = {"isEnabled": False, "riskLevel": "medium"}
        result = await cis_service._check_monitor_sign_in_risk_policy("tok")
    assert result["status"] == STATUS_FAIL


@pytest.mark.anyio("asyncio")
async def test_check_monitor_sign_in_risk_policy_fail_when_risk_none():
    with patch(
        "app.services.cis_benchmark._graph_get",
        new_callable=AsyncMock,
    ) as mock_get:
        mock_get.return_value = {"isEnabled": True, "riskLevel": "none"}
        result = await cis_service._check_monitor_sign_in_risk_policy("tok")
    assert result["status"] == STATUS_FAIL


# ---------------------------------------------------------------------------
# bp_monitor_user_risk_policy
# ---------------------------------------------------------------------------


@pytest.mark.anyio("asyncio")
async def test_check_monitor_user_risk_policy_pass():
    with patch(
        "app.services.cis_benchmark._graph_get",
        new_callable=AsyncMock,
    ) as mock_get:
        mock_get.return_value = {"isEnabled": True}
        result = await cis_service._check_monitor_user_risk_policy("tok")
    assert result["status"] == STATUS_PASS


@pytest.mark.anyio("asyncio")
async def test_check_monitor_user_risk_policy_fail():
    with patch(
        "app.services.cis_benchmark._graph_get",
        new_callable=AsyncMock,
    ) as mock_get:
        mock_get.return_value = {"isEnabled": False}
        result = await cis_service._check_monitor_user_risk_policy("tok")
    assert result["status"] == STATUS_FAIL


# ---------------------------------------------------------------------------
# bp_monitor_named_locations
# ---------------------------------------------------------------------------


@pytest.mark.anyio("asyncio")
async def test_check_monitor_named_locations_pass():
    with patch(
        "app.services.cis_benchmark._graph_get",
        new_callable=AsyncMock,
    ) as mock_get:
        mock_get.return_value = {"value": [{"id": "loc-1", "displayName": "HQ"}]}
        result = await cis_service._check_monitor_named_locations("tok")
    assert result["status"] == STATUS_PASS


@pytest.mark.anyio("asyncio")
async def test_check_monitor_named_locations_fail():
    with patch(
        "app.services.cis_benchmark._graph_get",
        new_callable=AsyncMock,
    ) as mock_get:
        mock_get.return_value = {"value": []}
        result = await cis_service._check_monitor_named_locations("tok")
    assert result["status"] == STATUS_FAIL


# ---------------------------------------------------------------------------
# bp_monitor_ca_report_only_policies
# ---------------------------------------------------------------------------


@pytest.mark.anyio("asyncio")
async def test_check_monitor_ca_report_only_policies_pass_when_no_security_policies_in_report_only():
    with patch(
        "app.services.cis_benchmark._graph_get",
        new_callable=AsyncMock,
    ) as mock_get:
        mock_get.return_value = {
            "value": [
                {
                    "displayName": "Test info policy",
                    "state": "enabledForReportingButNotEnforced",
                    "conditions": {"clientAppTypes": ["browser"]},
                    "grantControls": {"builtInControls": ["passwordChange"]},
                },
                {
                    "displayName": "MFA policy enforced",
                    "state": "enabled",
                    "conditions": {"clientAppTypes": ["browser"]},
                    "grantControls": {"builtInControls": ["mfa"]},
                },
            ]
        }
        result = await cis_service._check_monitor_ca_report_only_policies("tok")
    assert result["status"] == STATUS_PASS


@pytest.mark.anyio("asyncio")
async def test_check_monitor_ca_report_only_policies_fail_when_mfa_in_report_only():
    with patch(
        "app.services.cis_benchmark._graph_get",
        new_callable=AsyncMock,
    ) as mock_get:
        mock_get.return_value = {
            "value": [
                {
                    "displayName": "MFA report-only",
                    "state": "enabledForReportingButNotEnforced",
                    "conditions": {"clientAppTypes": ["browser"]},
                    "grantControls": {"builtInControls": ["mfa"]},
                },
            ]
        }
        result = await cis_service._check_monitor_ca_report_only_policies("tok")
    assert result["status"] == STATUS_FAIL
    assert "MFA report-only" in result["details"]


# ---------------------------------------------------------------------------
# bp_monitor_app_credential_expiry
# ---------------------------------------------------------------------------


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


@pytest.mark.anyio("asyncio")
async def test_check_monitor_app_credential_expiry_pass():
    far_future = datetime.now(timezone.utc) + timedelta(days=365)
    with patch(
        "app.services.cis_benchmark._graph_get",
        new_callable=AsyncMock,
    ) as mock_get:
        mock_get.return_value = {
            "value": [
                {
                    "id": "app-1",
                    "displayName": "App One",
                    "passwordCredentials": [{"endDateTime": _iso(far_future)}],
                    "keyCredentials": [],
                },
            ]
        }
        result = await cis_service._check_monitor_app_credential_expiry("tok")
    assert result["status"] == STATUS_PASS


@pytest.mark.anyio("asyncio")
async def test_check_monitor_app_credential_expiry_fail_when_expiring_soon():
    soon = datetime.now(timezone.utc) + timedelta(days=10)
    with patch(
        "app.services.cis_benchmark._graph_get",
        new_callable=AsyncMock,
    ) as mock_get:
        mock_get.return_value = {
            "value": [
                {
                    "id": "app-1",
                    "displayName": "ExpiringApp",
                    "passwordCredentials": [{"endDateTime": _iso(soon)}],
                    "keyCredentials": [],
                },
            ]
        }
        result = await cis_service._check_monitor_app_credential_expiry("tok")
    assert result["status"] == STATUS_FAIL
    assert "ExpiringApp" in result["details"]


@pytest.mark.anyio("asyncio")
async def test_check_monitor_app_credential_expiry_ignores_already_expired():
    past = datetime.now(timezone.utc) - timedelta(days=10)
    with patch(
        "app.services.cis_benchmark._graph_get",
        new_callable=AsyncMock,
    ) as mock_get:
        mock_get.return_value = {
            "value": [
                {
                    "id": "app-1",
                    "displayName": "Already expired",
                    "passwordCredentials": [{"endDateTime": _iso(past)}],
                    "keyCredentials": [],
                },
            ]
        }
        result = await cis_service._check_monitor_app_credential_expiry("tok")
    # Already-expired credentials are outside the [now, now+30d] window
    assert result["status"] == STATUS_PASS


# ---------------------------------------------------------------------------
# bp_monitor_cloud_admin_accounts
# ---------------------------------------------------------------------------


@pytest.mark.anyio("asyncio")
async def test_check_monitor_cloud_admin_accounts_pass():
    async def mock_graph_get(token: str, url: str) -> dict:
        if "directoryRoles?$filter=displayName" in url:
            return {"value": [{"id": "role-1", "displayName": "Global Administrator"}]}
        if "directoryRoles/role-1/members" in url:
            return {
                "value": [
                    {"id": "u1", "userPrincipalName": "admin1@example.com", "onPremisesSyncEnabled": None},
                    {"id": "u2", "userPrincipalName": "admin2@example.com", "onPremisesSyncEnabled": False},
                ]
            }
        return {}

    with patch("app.services.cis_benchmark._graph_get", side_effect=mock_graph_get):
        result = await cis_service._check_monitor_cloud_admin_accounts("tok")
    assert result["status"] == STATUS_PASS


@pytest.mark.anyio("asyncio")
async def test_check_monitor_cloud_admin_accounts_fail_when_synced():
    async def mock_graph_get(token: str, url: str) -> dict:
        if "directoryRoles?$filter=displayName" in url:
            return {"value": [{"id": "role-1", "displayName": "Global Administrator"}]}
        if "directoryRoles/role-1/members" in url:
            return {
                "value": [
                    {"id": "u1", "userPrincipalName": "synced-admin@example.com", "onPremisesSyncEnabled": True},
                ]
            }
        return {}

    with patch("app.services.cis_benchmark._graph_get", side_effect=mock_graph_get):
        result = await cis_service._check_monitor_cloud_admin_accounts("tok")
    assert result["status"] == STATUS_FAIL
    assert "synced-admin@example.com" in result["details"]


# ---------------------------------------------------------------------------
# bp_monitor_secure_score
# ---------------------------------------------------------------------------


@pytest.mark.anyio("asyncio")
async def test_check_monitor_secure_score_pass():
    with patch(
        "app.services.cis_benchmark._graph_get",
        new_callable=AsyncMock,
    ) as mock_get:
        mock_get.return_value = {"value": [{"currentScore": 80, "maxScore": 100}]}
        result = await cis_service._check_monitor_secure_score("tok")
    assert result["status"] == STATUS_PASS


@pytest.mark.anyio("asyncio")
async def test_check_monitor_secure_score_fail_when_low():
    with patch(
        "app.services.cis_benchmark._graph_get",
        new_callable=AsyncMock,
    ) as mock_get:
        mock_get.return_value = {"value": [{"currentScore": 20, "maxScore": 100}]}
        result = await cis_service._check_monitor_secure_score("tok")
    assert result["status"] == STATUS_FAIL


@pytest.mark.anyio("asyncio")
async def test_check_monitor_secure_score_unknown_when_no_data():
    with patch(
        "app.services.cis_benchmark._graph_get",
        new_callable=AsyncMock,
    ) as mock_get:
        mock_get.return_value = {"value": []}
        result = await cis_service._check_monitor_secure_score("tok")
    assert result["status"] == STATUS_UNKNOWN


# ---------------------------------------------------------------------------
# bp_monitor_mfa_registration_policy
# ---------------------------------------------------------------------------


@pytest.mark.anyio("asyncio")
async def test_check_monitor_mfa_registration_policy_pass():
    with patch(
        "app.services.cis_benchmark._graph_get",
        new_callable=AsyncMock,
    ) as mock_get:
        mock_get.return_value = {
            "authenticationMethodConfigurations": [
                {"id": "MicrosoftAuthenticator", "state": "enabled"},
                {"id": "Sms", "state": "disabled"},
            ]
        }
        result = await cis_service._check_monitor_mfa_registration_policy("tok")
    assert result["status"] == STATUS_PASS
    assert "MicrosoftAuthenticator" in result["details"]


@pytest.mark.anyio("asyncio")
async def test_check_monitor_mfa_registration_policy_fail_when_none_enabled():
    with patch(
        "app.services.cis_benchmark._graph_get",
        new_callable=AsyncMock,
    ) as mock_get:
        mock_get.return_value = {
            "authenticationMethodConfigurations": [
                {"id": "Sms", "state": "disabled"},
                {"id": "Fido2", "state": "disabled"},
            ]
        }
        result = await cis_service._check_monitor_mfa_registration_policy("tok")
    assert result["status"] == STATUS_FAIL
