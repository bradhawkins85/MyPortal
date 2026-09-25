"""Regression coverage for policy alternatives and verified batch remediation."""
import asyncio

import pytest

from app.services import m365_best_practices as service


def test_teams_lobby_controls_are_explicit_alternatives_with_one_default():
    catalog = {item["id"]: item for item in service._BEST_PRACTICES}
    org = catalog["bp_only_org_can_bypass_lobby"]
    invited = catalog["bp_invited_users_auto_admitted"]

    assert org["alternative_group"] == invited["alternative_group"]
    assert org["policy_profile"] != invited["policy_profile"]
    assert org["default_enabled"] is False
    assert invited["default_enabled"] is True
    assert org["desired_settings"][0]["value"] == "EveryoneInCompany"
    assert invited["desired_settings"][0]["value"] == "InvitedUsers"


def test_conflicting_policy_controls_are_rejected_before_writes():
    with pytest.raises(ValueError, match="Conflicting policy controls"):
        service._validate_policy_selection({
            "bp_only_org_can_bypass_lobby",
            "bp_invited_users_auto_admitted",
        })


def test_batch_requires_read_back_pass_and_preserves_partial_outcome(monkeypatch):
    candidates = [
        {"check_id": "a", "check_name": "A", "batch_scope": "m365", "status": "fail", "has_remediation": True},
        {"check_id": "b", "check_name": "B", "batch_scope": "m365", "status": "fail", "has_remediation": True},
    ]

    async def last_results(_company_id):
        return candidates

    async def remediate(*, company_id, check_id):
        return {"success": check_id == "a", "message": "write failed"}

    reads = 0
    async def verify(**_kwargs):
        nonlocal reads
        reads += 1
        return {"status": "pass" if reads == 2 else "fail"}

    async def no_sleep(_delay):
        return None

    monkeypatch.setattr(service, "get_last_results", last_results)
    monkeypatch.setattr(service, "remediate_check", remediate)
    monkeypatch.setattr(service, "run_single_check", verify)
    monkeypatch.setattr(service.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(service, "_validate_policy_selection", lambda _ids: None)

    result = asyncio.run(service.remediate_failed_checks_batch(1, scope="m365"))
    assert result["success"] is False
    assert result["succeeded"] == 1
    assert result["failed"] == 1
    assert reads == 2  # bounded propagation retry before accepting success


def test_batch_does_not_count_unreadable_verification_as_success(monkeypatch):
    async def last_results(_company_id):
        return [{"check_id": "a", "check_name": "A", "batch_scope": "m365", "status": "fail", "has_remediation": True}]

    async def remediate(**_kwargs):
        return {"success": True, "message": "written"}

    async def unreadable(**_kwargs):
        raise service.M365Error("read denied", http_status=403)

    async def no_sleep(_delay):
        return None

    monkeypatch.setattr(service, "get_last_results", last_results)
    monkeypatch.setattr(service, "remediate_check", remediate)
    monkeypatch.setattr(service, "run_single_check", unreadable)
    monkeypatch.setattr(service.asyncio, "sleep", no_sleep)
    monkeypatch.setattr(service, "_validate_policy_selection", lambda _ids: None)

    result = asyncio.run(service.remediate_failed_checks_batch(1, scope="m365"))
    assert result["succeeded"] == 0
    assert result["failed"] == 1
    assert "verification read failed" in result["failures"][0]


def test_capability_outcomes_are_distinct_from_noncompliance():
    assert len({
        service.STATUS_NOT_LICENSED,
        service.STATUS_UNSUPPORTED,
        service.STATUS_PERMISSION_MISSING,
        service.STATUS_ASSESSMENT_FAILED,
        service.STATUS_FAIL,
    }) == 5
