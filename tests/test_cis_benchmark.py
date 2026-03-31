"""Tests for the CIS Benchmark service."""
from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, patch
from typing import Any

from app.services import cis_benchmark as cis_service
from app.services.cis_benchmark import (
    STATUS_PASS,
    STATUS_FAIL,
    STATUS_UNKNOWN,
    STATUS_NOT_APPLICABLE,
    BENCHMARK_CATEGORIES,
    CATEGORY_M365,
    CATEGORY_INTUNE_WINDOWS,
    CATEGORY_INTUNE_IOS,
    CATEGORY_INTUNE_MACOS,
)


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_ca_policy(
    name: str = "Test Policy",
    state: str = "enabled",
    include_users: list[str] | None = None,
    include_roles: list[str] | None = None,
    client_app_types: list[str] | None = None,
    built_in_controls: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "id": "policy-1",
        "displayName": name,
        "state": state,
        "conditions": {
            "users": {
                "includeUsers": include_users or [],
                "includeRoles": include_roles or [],
            },
            "clientAppTypes": client_app_types or [],
        },
        "grantControls": {
            "operator": "OR",
            "builtInControls": built_in_controls or [],
        },
    }


# ---------------------------------------------------------------------------
# Constants tests
# ---------------------------------------------------------------------------

def test_benchmark_categories_have_required_fields():
    """All benchmark categories have id, name, and description."""
    for cat in BENCHMARK_CATEGORIES:
        assert "id" in cat
        assert "name" in cat
        assert "description" in cat


def test_all_category_ids_are_known():
    """Category IDs match expected constants."""
    ids = {c["id"] for c in BENCHMARK_CATEGORIES}
    assert CATEGORY_M365 in ids
    assert CATEGORY_INTUNE_WINDOWS in ids
    assert CATEGORY_INTUNE_IOS in ids
    assert CATEGORY_INTUNE_MACOS in ids


def test_get_remediation_returns_string_for_known_check():
    """get_remediation returns a non-empty string for known check IDs."""
    assert len(cis_service.get_remediation("m365_security_defaults")) > 10


def test_get_remediation_returns_fallback_for_unknown_check():
    """get_remediation returns a fallback string for unknown check IDs."""
    result = cis_service.get_remediation("unknown_check_id_xyz")
    assert "CIS" in result or "remediation" in result.lower()


# ---------------------------------------------------------------------------
# M365 check: Security Defaults
# ---------------------------------------------------------------------------

@pytest.mark.anyio("asyncio")
async def test_check_security_defaults_pass():
    with patch("app.services.cis_benchmark._graph_get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {"isEnabled": True}
        results = await cis_service.run_m365_benchmarks("fake-token")
    sd = next(r for r in results if r["check_id"] == "m365_security_defaults")
    assert sd["status"] == STATUS_PASS


@pytest.mark.anyio("asyncio")
async def test_check_security_defaults_fail():
    with patch("app.services.cis_benchmark._graph_get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {"isEnabled": False}
        results = await cis_service.run_m365_benchmarks("fake-token")
    sd = next(r for r in results if r["check_id"] == "m365_security_defaults")
    assert sd["status"] == STATUS_FAIL
    assert sd["remediation"] is not None


@pytest.mark.anyio("asyncio")
async def test_check_security_defaults_unknown_on_error():
    from app.services.m365 import M365Error
    with patch("app.services.cis_benchmark._graph_get", side_effect=M365Error("403 Forbidden")):
        results = await cis_service.run_m365_benchmarks("fake-token")
    sd = next(r for r in results if r["check_id"] == "m365_security_defaults")
    assert sd["status"] == STATUS_UNKNOWN


# ---------------------------------------------------------------------------
# M365 check: Legacy Auth Blocked
# ---------------------------------------------------------------------------

@pytest.mark.anyio("asyncio")
async def test_check_legacy_auth_blocked_pass():
    blocking_policy = _make_ca_policy(
        name="Block Legacy Auth",
        state="enabled",
        client_app_types=["exchangeActiveSync", "other"],
        built_in_controls=["block"],
    )

    async def mock_graph_get(token: str, url: str) -> dict:
        if "identitySecurityDefaults" in url:
            return {"isEnabled": False}
        if "conditionalAccessPolicies" in url:
            return {"value": [blocking_policy]}
        if "authorizationPolicy" in url:
            return {"allowedToUseSspr": True, "guestUserRoleId": "10dae51f-b6af-4016-8d66-8c2a99b929b3"}
        if "directoryRoles" in url:
            return {"value": [{"id": "role-1", "displayName": "Global Administrator"}]}
        if f"directoryRoles/role-1/members" in url:
            return {"value": [{"id": "u1"}, {"id": "u2"}]}
        if "domains" in url:
            return {"value": [{"id": "example.com", "passwordValidityPeriodInDays": 2147483647}]}
        if "auditLog" in url:
            return {"value": []}
        return {}

    with patch("app.services.cis_benchmark._graph_get", side_effect=mock_graph_get):
        results = await cis_service.run_m365_benchmarks("fake-token")

    check = next(r for r in results if r["check_id"] == "m365_legacy_auth_blocked")
    assert check["status"] == STATUS_PASS


@pytest.mark.anyio("asyncio")
async def test_check_legacy_auth_blocked_fail():
    async def mock_graph_get(token: str, url: str) -> dict:
        if "identitySecurityDefaults" in url:
            return {"isEnabled": False}
        if "conditionalAccessPolicies" in url:
            # No blocking policy
            return {"value": [_make_ca_policy(name="MFA Policy", built_in_controls=["mfa"])]}
        if "authorizationPolicy" in url:
            return {"allowedToUseSspr": True}
        if "directoryRoles" in url:
            return {"value": [{"id": "role-1"}]}
        if "directoryRoles/role-1/members" in url:
            return {"value": [{"id": "u1"}, {"id": "u2"}]}
        if "domains" in url:
            return {"value": []}
        if "auditLog" in url:
            return {"value": []}
        return {}

    with patch("app.services.cis_benchmark._graph_get", side_effect=mock_graph_get):
        results = await cis_service.run_m365_benchmarks("fake-token")

    check = next(r for r in results if r["check_id"] == "m365_legacy_auth_blocked")
    assert check["status"] == STATUS_FAIL
    assert check["remediation"] is not None


# ---------------------------------------------------------------------------
# M365 check: Global Admin Count
# ---------------------------------------------------------------------------

@pytest.mark.anyio("asyncio")
async def test_global_admin_count_pass():
    async def mock_graph_get(token: str, url: str) -> dict:
        if "identitySecurityDefaults" in url:
            return {"isEnabled": True}
        if "conditionalAccessPolicies" in url:
            return {"value": []}
        if "directoryRoles" in url and "members" not in url:
            return {"value": [{"id": "role-ga", "displayName": "Global Administrator"}]}
        if "directoryRoles/role-ga/members" in url:
            return {"value": [{"id": "u1"}, {"id": "u2"}]}
        if "authorizationPolicy" in url:
            return {"allowedToUseSspr": True}
        if "domains" in url:
            return {"value": []}
        if "auditLog" in url:
            return {"value": []}
        return {}

    with patch("app.services.cis_benchmark._graph_get", side_effect=mock_graph_get):
        results = await cis_service.run_m365_benchmarks("fake-token")

    check = next(r for r in results if r["check_id"] == "m365_global_admin_count")
    assert check["status"] == STATUS_PASS


@pytest.mark.anyio("asyncio")
async def test_global_admin_count_fail_too_many():
    async def mock_graph_get(token: str, url: str) -> dict:
        if "identitySecurityDefaults" in url:
            return {"isEnabled": True}
        if "conditionalAccessPolicies" in url:
            return {"value": []}
        if "directoryRoles" in url and "members" not in url:
            return {"value": [{"id": "role-ga", "displayName": "Global Administrator"}]}
        if "role-ga/members" in url:
            return {"value": [{"id": f"u{i}"} for i in range(8)]}
        if "authorizationPolicy" in url:
            return {"allowedToUseSspr": True}
        if "domains" in url:
            return {"value": []}
        if "auditLog" in url:
            return {"value": []}
        return {}

    with patch("app.services.cis_benchmark._graph_get", side_effect=mock_graph_get):
        results = await cis_service.run_m365_benchmarks("fake-token")

    check = next(r for r in results if r["check_id"] == "m365_global_admin_count")
    assert check["status"] == STATUS_FAIL


# ---------------------------------------------------------------------------
# Intune Windows checks
# ---------------------------------------------------------------------------

def _make_windows_policy(name: str = "Windows Policy", **overrides) -> dict[str, Any]:
    base = {
        "@odata.type": "#microsoft.graph.windows10CompliancePolicy",
        "id": "win-policy-1",
        "displayName": name,
        "bitLockerEnabled": True,
        "firewallEnabled": True,
        "antivirusRequired": True,
        "secureBootEnabled": True,
        "osMinimumVersion": "10.0.19041",
    }
    base.update(overrides)
    return base


@pytest.mark.anyio("asyncio")
async def test_intune_windows_all_pass():
    with patch("app.services.cis_benchmark._graph_get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {"value": [_make_windows_policy()]}
        results = await cis_service.run_intune_windows_benchmarks("fake-token")

    for r in results:
        assert r["status"] in (STATUS_PASS, STATUS_NOT_APPLICABLE), \
            f"Expected pass for {r['check_id']}, got {r['status']}: {r['details']}"


@pytest.mark.anyio("asyncio")
async def test_intune_windows_encryption_fail():
    policy = _make_windows_policy(bitLockerEnabled=False)
    with patch("app.services.cis_benchmark._graph_get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {"value": [policy]}
        results = await cis_service.run_intune_windows_benchmarks("fake-token")

    check = next(r for r in results if r["check_id"] == "intune_windows_encryption")
    assert check["status"] == STATUS_FAIL
    assert check["remediation"] is not None


@pytest.mark.anyio("asyncio")
async def test_intune_windows_no_policies():
    with patch("app.services.cis_benchmark._graph_get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {"value": []}
        results = await cis_service.run_intune_windows_benchmarks("fake-token")

    exists_check = next(r for r in results if r["check_id"] == "intune_windows_compliance_policy_exists")
    assert exists_check["status"] == STATUS_FAIL
    for r in results:
        if r["check_id"] != "intune_windows_compliance_policy_exists":
            assert r["status"] == STATUS_NOT_APPLICABLE


# ---------------------------------------------------------------------------
# Intune iOS checks
# ---------------------------------------------------------------------------

def _make_ios_policy(name: str = "iOS Policy", **overrides) -> dict[str, Any]:
    base = {
        "@odata.type": "#microsoft.graph.iosCompliancePolicy",
        "id": "ios-policy-1",
        "displayName": name,
        "passcodeRequired": True,
        "jailBroken": "Block",
        "osMinimumVersion": "16.0",
    }
    base.update(overrides)
    return base


@pytest.mark.anyio("asyncio")
async def test_intune_ios_all_pass():
    with patch("app.services.cis_benchmark._graph_get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {"value": [_make_ios_policy()]}
        results = await cis_service.run_intune_ios_benchmarks("fake-token")

    for r in results:
        assert r["status"] in (STATUS_PASS, STATUS_NOT_APPLICABLE), \
            f"Expected pass for {r['check_id']}, got {r['status']}: {r['details']}"


@pytest.mark.anyio("asyncio")
async def test_intune_ios_passcode_fail():
    policy = _make_ios_policy(passcodeRequired=False)
    with patch("app.services.cis_benchmark._graph_get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {"value": [policy]}
        results = await cis_service.run_intune_ios_benchmarks("fake-token")

    check = next(r for r in results if r["check_id"] == "intune_ios_passcode_required")
    assert check["status"] == STATUS_FAIL
    assert check["remediation"] is not None


@pytest.mark.anyio("asyncio")
async def test_intune_ios_no_policies():
    with patch("app.services.cis_benchmark._graph_get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {"value": []}
        results = await cis_service.run_intune_ios_benchmarks("fake-token")

    exists_check = next(r for r in results if r["check_id"] == "intune_ios_compliance_policy_exists")
    assert exists_check["status"] == STATUS_FAIL
    for r in results:
        if r["check_id"] != "intune_ios_compliance_policy_exists":
            assert r["status"] == STATUS_NOT_APPLICABLE


# ---------------------------------------------------------------------------
# Intune macOS checks
# ---------------------------------------------------------------------------

def _make_macos_policy(name: str = "macOS Policy", **overrides) -> dict[str, Any]:
    base = {
        "@odata.type": "#microsoft.graph.macOSCompliancePolicy",
        "id": "macos-policy-1",
        "displayName": name,
        "storageRequireEncryption": True,
        "firewallEnabled": True,
        "osMinimumVersion": "13.0",
        "gatekeeperAllowedAppSource": "macAppStore",
    }
    base.update(overrides)
    return base


@pytest.mark.anyio("asyncio")
async def test_intune_macos_all_pass():
    with patch("app.services.cis_benchmark._graph_get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {"value": [_make_macos_policy()]}
        results = await cis_service.run_intune_macos_benchmarks("fake-token")

    for r in results:
        assert r["status"] in (STATUS_PASS, STATUS_NOT_APPLICABLE), \
            f"Expected pass for {r['check_id']}, got {r['status']}: {r['details']}"


@pytest.mark.anyio("asyncio")
async def test_intune_macos_filevault_fail():
    policy = _make_macos_policy(storageRequireEncryption=False)
    with patch("app.services.cis_benchmark._graph_get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {"value": [policy]}
        results = await cis_service.run_intune_macos_benchmarks("fake-token")

    check = next(r for r in results if r["check_id"] == "intune_macos_filevault")
    assert check["status"] == STATUS_FAIL
    assert check["remediation"] is not None


@pytest.mark.anyio("asyncio")
async def test_intune_macos_no_policies():
    with patch("app.services.cis_benchmark._graph_get", new_callable=AsyncMock) as mock_get:
        mock_get.return_value = {"value": []}
        results = await cis_service.run_intune_macos_benchmarks("fake-token")

    exists_check = next(r for r in results if r["check_id"] == "intune_macos_compliance_policy_exists")
    assert exists_check["status"] == STATUS_FAIL
    for r in results:
        if r["check_id"] != "intune_macos_compliance_policy_exists":
            assert r["status"] == STATUS_NOT_APPLICABLE


# ---------------------------------------------------------------------------
# run_benchmarks orchestration
# ---------------------------------------------------------------------------

@pytest.mark.anyio("asyncio")
async def test_run_benchmarks_stores_results():
    """run_benchmarks calls upsert_result for each check result."""
    from unittest.mock import AsyncMock

    async def mock_acquire_token(company_id: int) -> str:
        return "access-token"

    mock_m365_results = [
        {"check_id": "m365_security_defaults", "check_name": "Security Defaults", "status": "pass", "details": "OK"}
    ]

    upsert_calls: list[dict] = []

    async def mock_upsert(**kwargs):
        upsert_calls.append(kwargs)

    mock_runners = {
        CATEGORY_M365: AsyncMock(return_value=mock_m365_results),
        CATEGORY_INTUNE_WINDOWS: AsyncMock(return_value=[]),
        CATEGORY_INTUNE_IOS: AsyncMock(return_value=[]),
        CATEGORY_INTUNE_MACOS: AsyncMock(return_value=[]),
    }

    with (
        patch("app.services.cis_benchmark.acquire_access_token", side_effect=mock_acquire_token),
        patch("app.services.cis_benchmark._CATEGORY_RUNNERS", mock_runners),
        patch("app.services.cis_benchmark.benchmark_repo.upsert_result", side_effect=mock_upsert),
    ):
        results = await cis_service.run_benchmarks(company_id=1)

    assert CATEGORY_M365 in results
    assert len(results[CATEGORY_M365]) == 1
    assert results[CATEGORY_M365][0]["check_id"] == "m365_security_defaults"
    # One upsert call for the one M365 check
    assert len(upsert_calls) == 1
    assert upsert_calls[0]["check_id"] == "m365_security_defaults"
    assert upsert_calls[0]["company_id"] == 1


@pytest.mark.anyio("asyncio")
async def test_run_benchmarks_handles_m365_error():
    """run_benchmarks captures M365Error and returns error result instead of raising."""
    from app.services.m365 import M365Error

    async def mock_acquire_token(company_id: int) -> str:
        return "access-token"

    async def failing_m365(token: str) -> list:
        raise M365Error("Token expired")

    mock_runners = {
        CATEGORY_M365: failing_m365,
        CATEGORY_INTUNE_WINDOWS: AsyncMock(return_value=[]),
        CATEGORY_INTUNE_IOS: AsyncMock(return_value=[]),
        CATEGORY_INTUNE_MACOS: AsyncMock(return_value=[]),
    }

    with (
        patch("app.services.cis_benchmark.acquire_access_token", side_effect=mock_acquire_token),
        patch("app.services.cis_benchmark._CATEGORY_RUNNERS", mock_runners),
        patch("app.services.cis_benchmark.benchmark_repo.upsert_result", new_callable=AsyncMock),
    ):
        results = await cis_service.run_benchmarks(company_id=1)

    assert CATEGORY_M365 in results
    error_result = results[CATEGORY_M365][0]
    assert error_result["status"] == STATUS_UNKNOWN
    assert "Token expired" in error_result["details"]


@pytest.mark.anyio("asyncio")
async def test_run_benchmarks_with_specific_categories():
    """run_benchmarks only runs the requested categories."""
    called: list[str] = []

    async def mock_acquire_token(company_id: int) -> str:
        return "access-token"

    async def mock_run_m365(token: str) -> list:
        called.append("m365")
        return []

    async def mock_run_windows(token: str) -> list:
        called.append("intune_windows")
        return []

    mock_runners = {
        CATEGORY_M365: mock_run_m365,
        CATEGORY_INTUNE_WINDOWS: mock_run_windows,
        CATEGORY_INTUNE_IOS: AsyncMock(return_value=[]),
        CATEGORY_INTUNE_MACOS: AsyncMock(return_value=[]),
    }

    with (
        patch("app.services.cis_benchmark.acquire_access_token", side_effect=mock_acquire_token),
        patch("app.services.cis_benchmark._CATEGORY_RUNNERS", mock_runners),
        patch("app.services.cis_benchmark.benchmark_repo.upsert_result", new_callable=AsyncMock),
    ):
        results = await cis_service.run_benchmarks(company_id=1, categories=[CATEGORY_M365])

    assert "m365" in called
    assert "intune_windows" not in called
    assert CATEGORY_M365 in results
    assert CATEGORY_INTUNE_WINDOWS not in results


# ---------------------------------------------------------------------------
# get_last_results
# ---------------------------------------------------------------------------

@pytest.mark.anyio("asyncio")
async def test_get_last_results_groups_by_category():
    """get_last_results groups check rows by benchmark_category."""
    from datetime import datetime

    mock_rows = [
        {
            "benchmark_category": "m365",
            "check_id": "m365_security_defaults",
            "check_name": "Security Defaults",
            "status": "fail",
            "details": "Disabled",
            "run_at": datetime(2024, 1, 1, 12, 0, 0),
        },
        {
            "benchmark_category": "intune_windows",
            "check_id": "intune_windows_encryption",
            "check_name": "BitLocker",
            "status": "pass",
            "details": "Enabled",
            "run_at": datetime(2024, 1, 1, 12, 0, 0),
        },
    ]

    with (
        patch("app.services.cis_benchmark.benchmark_repo.list_results", new_callable=AsyncMock) as mock_list,
        patch("app.services.cis_benchmark.benchmark_repo.get_exclusion_map", new_callable=AsyncMock) as mock_excl,
    ):
        mock_list.return_value = mock_rows
        mock_excl.return_value = {}
        results = await cis_service.get_last_results(company_id=1)

    assert "m365" in results
    assert "intune_windows" in results
    assert results["m365"][0]["check_id"] == "m365_security_defaults"
    # Failed check should have remediation
    assert results["m365"][0]["remediation"] is not None
    # Passing check should not have remediation
    assert results["intune_windows"][0]["remediation"] is None


# ---------------------------------------------------------------------------
# Exclusion overlay in get_last_results
# ---------------------------------------------------------------------------

@pytest.mark.anyio("asyncio")
async def test_get_last_results_applies_exclusion_overlay():
    """Excluded checks are shown with STATUS_EXCLUDED and the exclusion reason."""
    from datetime import datetime

    mock_rows = [
        {
            "benchmark_category": "m365",
            "check_id": "m365_security_defaults",
            "check_name": "Security Defaults",
            "status": "fail",
            "details": "Disabled",
            "run_at": datetime(2024, 1, 1, 12, 0, 0),
        },
    ]

    with (
        patch("app.services.cis_benchmark.benchmark_repo.list_results", new_callable=AsyncMock) as mock_list,
        patch("app.services.cis_benchmark.benchmark_repo.get_exclusion_map", new_callable=AsyncMock) as mock_excl,
    ):
        mock_list.return_value = mock_rows
        mock_excl.return_value = {"m365_security_defaults": "Not supported in this tenant configuration"}
        results = await cis_service.get_last_results(company_id=1)

    check = results["m365"][0]
    assert check["status"] == cis_service.STATUS_EXCLUDED
    assert check["raw_status"] == "fail"
    assert "Not supported" in check["details"]
    assert check["exclusion_reason"] == "Not supported in this tenant configuration"
    # Excluded checks should not have remediation guidance
    assert check["remediation"] is None


@pytest.mark.anyio("asyncio")
async def test_get_last_results_exclusion_with_empty_reason():
    """Excluded check with empty reason shows a default message."""
    from datetime import datetime

    mock_rows = [
        {
            "benchmark_category": "m365",
            "check_id": "m365_security_defaults",
            "check_name": "Security Defaults",
            "status": "fail",
            "details": "Disabled",
            "run_at": datetime(2024, 1, 1, 12, 0, 0),
        },
    ]

    with (
        patch("app.services.cis_benchmark.benchmark_repo.list_results", new_callable=AsyncMock) as mock_list,
        patch("app.services.cis_benchmark.benchmark_repo.get_exclusion_map", new_callable=AsyncMock) as mock_excl,
    ):
        mock_list.return_value = mock_rows
        mock_excl.return_value = {"m365_security_defaults": ""}
        results = await cis_service.get_last_results(company_id=1)

    check = results["m365"][0]
    assert check["status"] == cis_service.STATUS_EXCLUDED
    assert "administrator" in check["details"].lower() or "excluded" in check["details"].lower()


@pytest.mark.anyio("asyncio")
async def test_get_last_results_non_excluded_check_unaffected():
    """Checks not in the exclusion map retain their original status."""
    from datetime import datetime

    mock_rows = [
        {
            "benchmark_category": "m365",
            "check_id": "m365_audit_log_enabled",
            "check_name": "Audit log",
            "status": "fail",
            "details": "Disabled",
            "run_at": datetime(2024, 1, 1, 12, 0, 0),
        },
    ]

    with (
        patch("app.services.cis_benchmark.benchmark_repo.list_results", new_callable=AsyncMock) as mock_list,
        patch("app.services.cis_benchmark.benchmark_repo.get_exclusion_map", new_callable=AsyncMock) as mock_excl,
    ):
        mock_list.return_value = mock_rows
        # Exclusion map has a different check_id – this check is NOT excluded
        mock_excl.return_value = {"m365_security_defaults": "some reason"}
        results = await cis_service.get_last_results(company_id=1)

    check = results["m365"][0]
    assert check["status"] == "fail"
    assert check["exclusion_reason"] is None


@pytest.mark.anyio("asyncio")
async def test_add_exclusion_calls_upsert():
    """add_exclusion delegates to benchmark_repo.upsert_exclusion."""
    with patch("app.services.cis_benchmark.benchmark_repo.upsert_exclusion", new_callable=AsyncMock) as mock_upsert:
        await cis_service.add_exclusion(1, "m365_security_defaults", "  built-in domain  ")
    mock_upsert.assert_awaited_once_with(
        company_id=1,
        check_id="m365_security_defaults",
        reason="built-in domain",  # stripped
    )


@pytest.mark.anyio("asyncio")
async def test_remove_exclusion_calls_delete():
    """remove_exclusion delegates to benchmark_repo.delete_exclusion."""
    with patch("app.services.cis_benchmark.benchmark_repo.delete_exclusion", new_callable=AsyncMock) as mock_delete:
        await cis_service.remove_exclusion(1, "m365_security_defaults")
    mock_delete.assert_awaited_once_with(
        company_id=1,
        check_id="m365_security_defaults",
    )


@pytest.mark.anyio("asyncio")
async def test_list_exclusions_returns_repo_data():
    """list_exclusions returns data from the repository."""
    mock_data = [{"check_id": "m365_security_defaults", "reason": "n/a", "created_at": None}]
    with patch("app.services.cis_benchmark.benchmark_repo.list_exclusions", new_callable=AsyncMock) as mock_list:
        mock_list.return_value = mock_data
        result = await cis_service.list_exclusions(1)
    assert result == mock_data


def test_status_excluded_constant_exists():
    """STATUS_EXCLUDED constant is defined."""
    assert cis_service.STATUS_EXCLUDED == "excluded"


# ---------------------------------------------------------------------------
# Provision app roles updated for CIS benchmarks
# ---------------------------------------------------------------------------

def test_provision_app_roles_include_cis_permissions():
    """_PROVISION_APP_ROLES now includes required CIS benchmark permissions."""
    from app.services import m365 as m365_svc
    roles = m365_svc._PROVISION_APP_ROLES
    # Policy.Read.All
    assert "246dd0d5-5bd0-4def-940b-0421030a5b68" in roles
    # Organization.Read.All
    assert "498476ce-e0fe-48b0-b801-37ba7e2685c6" in roles
    # DeviceManagementConfiguration.Read.All
    assert "dc377aa6-52d8-4e23-b271-2a7ae04cedf3" in roles
    # DeviceManagementManagedDevices.Read.All
    assert "2f51be20-0bb4-4fed-bf7b-db946066c75e" in roles
    # AuditLog.Read.All
    assert "b0afded3-3588-46d8-8b3d-9842eff778da" in roles


# ---------------------------------------------------------------------------
# _graph_get error includes HTTP status code
# ---------------------------------------------------------------------------

@pytest.mark.anyio("asyncio")
async def test_graph_get_error_includes_status_code():
    """_graph_get raises M365Error with the HTTP status code in the message."""
    from unittest.mock import MagicMock
    from app.services.m365 import _graph_get, M365Error

    mock_response = MagicMock()
    mock_response.status_code = 403
    mock_response.text = '{"error": {"code": "Forbidden"}}'

    mock_client = AsyncMock()
    mock_client.get = AsyncMock(return_value=mock_response)
    mock_client_ctx = MagicMock()
    mock_client_ctx.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client_ctx.__aexit__ = AsyncMock(return_value=None)

    with patch("app.services.m365.httpx.AsyncClient", return_value=mock_client_ctx):
        with pytest.raises(M365Error) as exc_info:
            await _graph_get("fake-token", "https://graph.microsoft.com/v1.0/test")

    assert "403" in str(exc_info.value)


# ---------------------------------------------------------------------------
# _check_audit_log_enabled distinguishes 403 from other errors
# ---------------------------------------------------------------------------

@pytest.mark.anyio("asyncio")
async def test_check_audit_log_enabled_unknown_on_403():
    """A 403 from the audit-log endpoint should return STATUS_UNKNOWN.

    A 403 means the app lacks AuditLog.Read.All permission – it does NOT tell
    us whether auditing is enabled or disabled.  Returning STATUS_FAIL would be
    misleading, so STATUS_UNKNOWN is the correct outcome.
    """
    from app.services.m365 import M365Error

    with patch(
        "app.services.cis_benchmark._graph_get",
        side_effect=M365Error("Microsoft Graph request failed (403)"),
    ):
        results = await cis_service.run_m365_benchmarks("fake-token")

    check = next(r for r in results if r["check_id"] == "m365_audit_log_enabled")
    assert check["status"] == STATUS_UNKNOWN


@pytest.mark.anyio("asyncio")
async def test_check_audit_log_enabled_unknown_on_non_403_error():
    """A non-403 error from the audit-log endpoint returns STATUS_UNKNOWN."""
    from app.services.m365 import M365Error

    with patch(
        "app.services.cis_benchmark._graph_get",
        side_effect=M365Error("Microsoft Graph request failed (500)"),
    ):
        results = await cis_service.run_m365_benchmarks("fake-token")

    check = next(r for r in results if r["check_id"] == "m365_audit_log_enabled")
    assert check["status"] == STATUS_UNKNOWN


# ---------------------------------------------------------------------------
# _graph_get_all handles @odata.nextLink pagination
# ---------------------------------------------------------------------------

@pytest.mark.anyio("asyncio")
async def test_graph_get_all_follows_pagination():
    """_graph_get_all follows @odata.nextLink and returns all items from every page."""
    page1_url = "https://graph.microsoft.com/v1.0/test"
    page2_url = "https://graph.microsoft.com/v1.0/test?$skip=2"

    call_order: list[str] = []

    async def mock_graph_get(token: str, url: str) -> dict:
        call_order.append(url)
        if url == page1_url:
            # First page has a nextLink; second page has none (loop terminates)
            return {"value": [{"id": "item1"}, {"id": "item2"}], "@odata.nextLink": page2_url}
        return {"value": [{"id": "item3"}]}

    with patch("app.services.cis_benchmark._graph_get", side_effect=mock_graph_get):
        items = await cis_service._graph_get_all("fake-token", page1_url)

    assert len(items) == 3
    assert items[0]["id"] == "item1"
    assert items[2]["id"] == "item3"
    # Both pages were fetched in order
    assert call_order == [page1_url, page2_url]


@pytest.mark.anyio("asyncio")
async def test_conditional_access_checks_follow_pagination():
    """CA policy benchmark checks use _graph_get_all so paginated tenants are evaluated fully.

    Specifically tests that when the blocking legacy-auth policy is on the second
    page, _check_legacy_auth_blocked still returns STATUS_PASS.  Without
    pagination the check would see only the first page and incorrectly return FAIL.
    """
    page2_url = (
        "https://graph.microsoft.com/v1.0/policies/conditionalAccessPolicies"
        "?$select=id,displayName,state,conditions,grantControls&$skip=1"
    )
    blocking_policy = _make_ca_policy(
        name="Block Legacy Auth",
        state="enabled",
        client_app_types=["exchangeActiveSync", "other"],
        built_in_controls=["block"],
    )

    async def mock_graph_get(token: str, url: str) -> dict:
        if "identitySecurityDefaults" in url:
            return {"isEnabled": False}
        if "conditionalAccessPolicies" in url and "$skip" not in url:
            # First page: no blocking policy, but provides a nextLink
            return {
                "value": [_make_ca_policy(name="MFA Policy", built_in_controls=["mfa"])],
                "@odata.nextLink": page2_url,
            }
        if "$skip=1" in url:
            # Second page: blocking policy is here
            return {"value": [blocking_policy]}
        if "authorizationPolicy" in url:
            return {"allowedToUseSspr": True, "guestUserRoleId": "10dae51f-b6af-4016-8d66-8c2a99b929b3"}
        if "directoryRoles" in url and "members" not in url:
            return {"value": [{"id": "role-1", "displayName": "Global Administrator"}]}
        if "directoryRoles/role-1/members" in url:
            return {"value": [{"id": "u1"}, {"id": "u2"}]}
        if "domains" in url:
            return {"value": [{"id": "example.com", "passwordValidityPeriodInDays": 2147483647}]}
        if "auditLog" in url:
            return {"value": []}
        return {}

    with patch("app.services.cis_benchmark._graph_get", side_effect=mock_graph_get):
        results = await cis_service.run_m365_benchmarks("fake-token")

    check = next(r for r in results if r["check_id"] == "m365_legacy_auth_blocked")
    assert check["status"] == STATUS_PASS
