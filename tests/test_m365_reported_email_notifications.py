from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services import m365_best_practices as service
from app.services.m365 import M365Error


CHECK_ID = "bp_reported_email_support_notifications"
SUPPORT = "support@msp.example"


@pytest.fixture
def alert_tenant(monkeypatch):
    state = SimpleNamespace(policies=[
        {"Name": "Email reported by user as junk", "Identity": "junk-policy",
         "NotifyUser": ["TenantAdmins", "soc@customer.example"], "Disabled": True,
         "NotificationEnabled": False, "Severity": "Low", "Threshold": 12,
         "NotifyUserSuppressionExpiryDate": "2030-01-01T00:00:00Z"},
        {"Name": "Email reported by user as malware or phish", "Identity": "phish-policy",
         "NotifyUser": "security@customer.example", "Disabled": False,
         "NotificationEnabled": True, "Severity": "High"},
        {"Name": "Email reported by user as not junk", "Identity": "not-junk-policy",
         "NotifyUser": ["other@customer.example"], "Disabled": False,
         "NotificationEnabled": True},
    ], propagate=True)
    monkeypatch.setattr(service, "get_settings", lambda: SimpleNamespace(
        m365_it_support_external_email_address=SUPPORT))

    async def invoke(_token, _tenant, cmdlet, params=None):
        if cmdlet == "Get-ProtectionAlert":
            return {"value": deepcopy(state.policies)}
        assert cmdlet == "Set-ProtectionAlert"
        if state.propagate:
            policy = next(row for row in state.policies if row["Identity"] == params["Identity"])
            policy.update({key: deepcopy(value) for key, value in params.items() if key != "Identity"})
        return {"value": []}

    state.command = AsyncMock(side_effect=invoke)
    monkeypatch.setattr(service, "_scc_invoke_command", state.command)
    return state


@pytest.mark.anyio
async def test_adds_support_and_preserves_recipients_and_other_settings(alert_tenant):
    unrelated = deepcopy(alert_tenant.policies[2])
    assert (await service._check_reported_email_support_notifications("scc", "tenant"))["status"] == "fail"
    success, message = await service._remediate_reported_email_support_notifications("scc", "tenant")
    assert success, message
    calls = [call.args[3] for call in alert_tenant.command.await_args_list if call.args[2] == "Set-ProtectionAlert"]
    assert calls == [
        {"Identity": "junk-policy", "Disabled": False, "NotificationEnabled": True,
         "NotifyUser": ["TenantAdmins", "soc@customer.example", SUPPORT]},
        {"Identity": "phish-policy", "NotifyUser": ["security@customer.example", SUPPORT]},
    ]
    assert alert_tenant.policies[0]["Severity"] == "Low"
    assert alert_tenant.policies[0]["Threshold"] == 12
    assert alert_tenant.policies[0]["NotifyUserSuppressionExpiryDate"] == "2030-01-01T00:00:00Z"
    assert alert_tenant.policies[2] == unrelated
    assert (await service._check_reported_email_support_notifications("scc", "tenant"))["status"] == "pass"


@pytest.mark.anyio
async def test_repeat_remediation_does_not_write_or_duplicate_recipients(alert_tenant):
    await service._remediate_reported_email_support_notifications("scc", "tenant")
    alert_tenant.command.reset_mock()
    assert (await service._remediate_reported_email_support_notifications("scc", "tenant"))[0]
    assert all(call.args[2] == "Get-ProtectionAlert" for call in alert_tenant.command.await_args_list)
    assert alert_tenant.policies[0]["NotifyUser"].count(SUPPORT) == 1


@pytest.mark.anyio
async def test_existing_support_recipient_is_matched_case_insensitively(alert_tenant):
    for policy in alert_tenant.policies[:2]:
        policy.update(NotifyUser=["SMTP:SUPPORT@MSP.EXAMPLE"], Disabled="False", NotificationEnabled="True")
    assert (await service._remediate_reported_email_support_notifications("scc", "tenant"))[0]
    assert all(call.args[2] == "Get-ProtectionAlert" for call in alert_tenant.command.await_args_list)


@pytest.mark.anyio
@pytest.mark.parametrize("name", ["Email reported by user as phish", "Email reported by user as phishing"])
async def test_phishing_policy_name_variants_are_supported(alert_tenant, name):
    alert_tenant.policies[1]["Name"] = name
    assert (await service._remediate_reported_email_support_notifications("scc", "tenant"))[0]
    assert SUPPORT in alert_tenant.policies[1]["NotifyUser"]


@pytest.mark.anyio
async def test_missing_builtin_policy_does_not_create_a_custom_alert(alert_tenant):
    alert_tenant.policies = alert_tenant.policies[1:]
    check = await service._check_reported_email_support_notifications("scc", "tenant")
    assert check["status"] == "unsupported" and "junk" in check["details"]
    success, message = await service._remediate_reported_email_support_notifications("scc", "tenant")
    assert not success and "no policies were changed" in message
    assert all(call.args[2] == "Get-ProtectionAlert" for call in alert_tenant.command.await_args_list)


@pytest.mark.anyio
@pytest.mark.parametrize("support", ["", "invalid", "a@", "a@b.example\nBcc:other@b.example"])
async def test_invalid_support_config_does_not_call_scc(alert_tenant, monkeypatch, support):
    monkeypatch.setattr(service, "get_settings", lambda: SimpleNamespace(
        m365_it_support_external_email_address=support))
    assert (await service._check_reported_email_support_notifications("scc", "tenant"))["status"] == "fail"
    assert not (await service._remediate_reported_email_support_notifications("scc", "tenant"))[0]
    alert_tenant.command.assert_not_awaited()


@pytest.mark.anyio
async def test_unknown_recipient_shape_is_not_silently_replaced(alert_tenant):
    alert_tenant.policies[1]["NotifyUser"] = [{"unrecognized": "recipient"}]
    with pytest.raises(M365Error, match="read safely"):
        await service._remediate_reported_email_support_notifications("scc", "tenant")
    assert all(call.args[2] == "Get-ProtectionAlert" for call in alert_tenant.command.await_args_list)


@pytest.mark.anyio
async def test_update_is_verified_and_stale_state_reports_propagation(alert_tenant):
    alert_tenant.propagate = False
    success, message = await service._remediate_reported_email_support_notifications("scc", "tenant")
    assert not success and "propagation" in message


@pytest.mark.anyio
async def test_public_remediation_dispatch_persists_success_and_permission_errors(alert_tenant, monkeypatch):
    monkeypatch.setattr(service, "_acquire_scc_access_token", AsyncMock(return_value=("scc", "tenant")))
    persist = AsyncMock()
    monkeypatch.setattr(service.bp_repo, "update_remediation_status", persist)
    result = await service.remediate_check(7, CHECK_ID)
    assert result["success"], result["message"]
    assert persist.await_args.kwargs["remediation_status"] == "success"
    alert_tenant.command.side_effect = M365Error("Forbidden", http_status=403)
    result = await service.remediate_check(7, CHECK_ID)
    assert not result["success"]
    assert "alert policy management access" in result["message"]
    assert persist.await_args.kwargs["remediation_status"] == "failed"


@pytest.mark.anyio
async def test_single_check_uses_scc_runner_and_persists_result(alert_tenant, monkeypatch):
    monkeypatch.setattr(service, "get_enabled_check_ids", AsyncMock(return_value={CHECK_ID}))
    monkeypatch.setattr(service, "acquire_access_token", AsyncMock(return_value="graph"))
    monkeypatch.setattr(service, "acquire_delegated_token", AsyncMock(return_value=None))
    monkeypatch.setattr(service, "detect_tenant_capabilities", AsyncMock(return_value={service.CAP_EXCHANGE_ONLINE}))
    acquire_scc = AsyncMock(return_value=("scc", "tenant"))
    monkeypatch.setattr(service, "_acquire_scc_access_token", acquire_scc)
    persist = AsyncMock()
    monkeypatch.setattr(service.bp_repo, "upsert_result", persist)
    result = await service.run_single_check(7, CHECK_ID, allow_auto_remediation=False, emit_ticket_on_fail=False)
    assert result["status"] == "fail"
    acquire_scc.assert_awaited_once_with(7)
    assert persist.await_args.kwargs["check_id"] == CHECK_ID
    assert persist.await_args.kwargs["status"] == "fail"


def test_check_is_enabled_and_remediable_in_catalog():
    entry = next(row for row in service.list_best_practices() if row["id"] == CHECK_ID)
    assert entry["default_enabled"] is True
    assert entry["has_remediation"] is True
    assert service._catalog_map()[CHECK_ID]["source_type"] == "scc"


@pytest.mark.anyio
async def test_missing_policy_identity_is_detected_before_any_writes(alert_tenant):
    policy = alert_tenant.policies[1]
    policy["DisplayName"] = policy.pop("Name")
    del policy["Identity"]
    success, message = await service._remediate_reported_email_support_notifications("scc", "tenant")
    assert not success and "no usable identity" in message
    assert all(call.args[2] == "Get-ProtectionAlert" for call in alert_tenant.command.await_args_list)


@pytest.mark.anyio
async def test_partial_failure_can_be_retried_without_duplicating_recipients(alert_tenant, monkeypatch):
    invoke = alert_tenant.command.side_effect

    async def fail_second_update(token, tenant, cmdlet, params=None):
        if cmdlet == "Set-ProtectionAlert" and params["Identity"] == "phish-policy":
            raise M365Error("Forbidden", http_status=403)
        return await invoke(token, tenant, cmdlet, params)

    monkeypatch.setattr(service, "_acquire_scc_access_token", AsyncMock(return_value=("scc", "tenant")))
    monkeypatch.setattr(service.bp_repo, "update_remediation_status", AsyncMock())
    alert_tenant.command.side_effect = fail_second_update
    assert not (await service.remediate_check(7, CHECK_ID))["success"]
    assert alert_tenant.policies[0]["NotifyUser"].count(SUPPORT) == 1
    alert_tenant.command.side_effect = invoke
    assert (await service.remediate_check(7, CHECK_ID))["success"]
    assert alert_tenant.policies[0]["NotifyUser"].count(SUPPORT) == 1
    assert alert_tenant.policies[1]["NotifyUser"].count(SUPPORT) == 1
