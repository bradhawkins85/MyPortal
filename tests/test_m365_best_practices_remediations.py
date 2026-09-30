"""Remediation coverage for Microsoft 365 best-practice checks."""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from app.services import m365_best_practices as bp_service
from app.services.m365 import M365Error

_SVC = "app.services.m365_best_practices"


def _entry(check_id: str) -> dict:
    return next(bp for bp in bp_service._BEST_PRACTICES if bp["id"] == check_id)


def _patches(**extra):
    base = {
        "acquire_access_token": AsyncMock(return_value="graph-token"),
        "_acquire_exo_access_token": AsyncMock(return_value=("exo-token", "tenant-id")),
    }
    base.update(extra)
    managers = [patch(f"{_SVC}.{name}", mock) for name, mock in base.items()]
    managers.append(patch(f"{_SVC}.bp_repo.update_remediation_status", AsyncMock()))
    return managers


async def _remediate(check_id: str, **mocks) -> dict:
    managers = _patches(**mocks)
    for manager in managers:
        manager.start()
    try:
        return await bp_service.remediate_check(company_id=5, check_id=check_id)
    finally:
        for manager in reversed(managers):
            manager.stop()


# ---------------------------------------------------------------------------
# Catalog contracts
# ---------------------------------------------------------------------------


def test_newly_remediable_checks_are_flagged():
    for check_id in (
        "bp_security_defaults",
        "bp_security_defaults_appropriate",
        "bp_block_legacy_auth",
        "bp_mfa_for_all_users",
        "bp_admin_mfa",
        "bp_signin_freq_admin_browser_no_persist",
        "bp_password_never_expires",
        "bp_password_expiry_never_expire",
        "bp_laps_enabled",
        "bp_sharepoint_sign_out_inactive_users",
        "bp_external_content_sharing_restricted",
        "bp_audit_bypass_disabled_mailboxes",
        "bp_zap_teams_on",
        "bp_safe_links_office_apps",
        "bp_dkim_enabled_all_domains",
        "bp_anon_dialin_cannot_start_meeting",
        "bp_dialin_cannot_bypass_lobby",
        "bp_restrict_dialin_bypass_lobby",
        "bp_teams_external_files_approved_storage",
    ):
        assert _entry(check_id)["has_remediation"] is True, check_id


def test_every_teams_remediation_has_a_command():
    teams_ids = {
        bp["id"] for bp in bp_service._BEST_PRACTICES
        if bp.get("remediation_type") == "teams_policy"
    }
    assert teams_ids == set(bp_service._TEAMS_REMEDIATIONS)


def test_sspr_payload_uses_top_level_graph_property():
    assert _entry("bp_self_service_password_reset")["remediation_payload"] == {
        "allowedToUseSSPR": True
    }


def test_system_preferred_mfa_payload_targets_all_users():
    prefs = _entry("bp_system_preferred_mfa")["remediation_payload"]["systemCredentialPreferences"]
    assert prefs["state"] == "enabled"
    assert prefs["includeTargets"] == [{"id": "all_users", "targetType": "group"}]


def test_email_otp_payload_includes_odata_type():
    payload = _entry("bp_email_otp_disabled")["remediation_payload"]
    assert payload["@odata.type"] == "#microsoft.graph.emailAuthenticationMethodConfiguration"


def test_new_write_permissions_are_provisioned():
    from app.services import m365 as m365_svc

    roles = m365_svc._PROVISION_APP_ROLES
    assert "01c0a623-fc9b-48e9-b794-0756f8e8f067" in roles  # Policy.ReadWrite.ConditionalAccess
    assert "7e05723c-0bb0-42da-be95-ae9f08a6e53c" in roles  # Domain.ReadWrite.All
    assert "230fb2d5-aa21-49c1-bfa7-ae1be179d867" in roles  # Policy.ReadWrite.DeviceConfiguration


# ---------------------------------------------------------------------------
# Exchange Online
# ---------------------------------------------------------------------------


@pytest.mark.anyio("asyncio")
async def test_outlook_addins_remediation_sets_each_owa_policy_with_identity():
    calls: list[tuple[str, dict]] = []

    async def fake_exo(token, tenant, cmdlet, params=None):
        calls.append((cmdlet, params or {}))
        if cmdlet == "Get-OwaMailboxPolicy":
            return {"value": [
                {"Identity": "OwaMailboxPolicy-Default", "WebPartsFrameworkEnabled": True},
                {"Identity": "Restricted", "WebPartsFrameworkEnabled": False},
            ]}
        return {}

    result = await _remediate(
        "bp_outlook_addins_disabled", _exo_invoke_command=AsyncMock(side_effect=fake_exo)
    )
    assert result["success"] is True
    assert ("Set-OwaMailboxPolicy", {
        "Identity": "OwaMailboxPolicy-Default", "WebPartsFrameworkEnabled": False,
    }) in calls
    assert all(params.get("Identity") != "Restricted" for cmd, params in calls if cmd.startswith("Set-"))


@pytest.mark.anyio("asyncio")
async def test_block_users_message_limit_updates_custom_policies_and_skips_presets():
    calls: list[tuple[str, dict]] = []

    async def fake_exo(token, tenant, cmdlet, params=None):
        calls.append((cmdlet, params or {}))
        if cmdlet == "Get-HostedOutboundSpamFilterPolicy":
            return {"value": [
                {"Identity": "Default", "ActionWhenThresholdReached": "BlockUserForToday"},
                {"Identity": "Strict Preset Security Policy", "RecommendedPolicyType": "Strict",
                 "ActionWhenThresholdReached": "Alert"},
            ]}
        return {}

    result = await _remediate(
        "bp_block_users_message_limit", _exo_invoke_command=AsyncMock(side_effect=fake_exo)
    )
    assert result["success"] is True
    sets = [params for cmd, params in calls if cmd == "Set-HostedOutboundSpamFilterPolicy"]
    assert sets == [{"Identity": "Default", "ActionWhenThresholdReached": "BlockUser"}]


@pytest.mark.anyio("asyncio")
async def test_audit_bypass_remediation_disables_bypass_per_mailbox():
    calls: list[tuple[str, dict]] = []

    async def fake_exo(token, tenant, cmdlet, params=None):
        calls.append((cmdlet, params or {}))
        if cmdlet == "Get-MailboxAuditBypassAssociation":
            return {"value": [
                {"Identity": "svc-backup", "AuditBypassEnabled": True},
                {"Identity": "alice", "AuditBypassEnabled": False},
            ]}
        return {}

    result = await _remediate(
        "bp_audit_bypass_disabled_mailboxes", _exo_invoke_command=AsyncMock(side_effect=fake_exo)
    )
    assert result["success"] is True
    assert [p for c, p in calls if c.startswith("Set-")] == [
        {"Identity": "svc-backup", "AuditBypassEnabled": False}
    ]


@pytest.mark.anyio("asyncio")
async def test_foreach_exo_remediation_reports_failures():
    async def fake_exo(token, tenant, cmdlet, params=None):
        if cmdlet == "Get-TeamsProtectionPolicy":
            return {"value": [{"Identity": "Teams Protection Policy", "ZapEnabled": False}]}
        raise M365Error("Exchange Online Set-TeamsProtectionPolicy failed (400): denied")

    result = await _remediate("bp_zap_teams_on", _exo_invoke_command=AsyncMock(side_effect=fake_exo))
    assert result["success"] is False
    assert "Teams Protection Policy" in result["message"]


@pytest.mark.anyio("asyncio")
async def test_audit_log_search_enables_org_customization_when_dehydrated():
    calls: list[str] = []
    attempts = {"set": 0}

    async def fake_exo(token, tenant, cmdlet, params=None):
        calls.append(cmdlet)
        if cmdlet == "Set-AdminAuditLogConfig":
            attempts["set"] += 1
            if attempts["set"] == 1:
                raise M365Error("Run Enable-OrganizationCustomization before this operation")
        return {}

    result = await _remediate(
        "bp_audit_log_search_enabled", _exo_invoke_command=AsyncMock(side_effect=fake_exo)
    )
    assert result["success"] is True
    assert calls == [
        "Set-AdminAuditLogConfig", "Enable-OrganizationCustomization", "Set-AdminAuditLogConfig",
    ]


@pytest.mark.anyio("asyncio")
async def test_safe_links_remediation_creates_policy_and_rule():
    calls: list[tuple[str, dict]] = []

    async def fake_exo(token, tenant, cmdlet, params=None):
        calls.append((cmdlet, params or {}))
        if cmdlet in {"Get-SafeLinksPolicy", "Get-SafeLinksRule"}:
            return {"value": []}
        if cmdlet == "Get-AcceptedDomain":
            return {"value": [{"DomainName": "contoso.com"}, {"DomainName": "contoso.onmicrosoft.com"}]}
        return {}

    result = await _remediate(
        "bp_safe_links_office_apps", _exo_invoke_command=AsyncMock(side_effect=fake_exo)
    )
    assert result["success"] is True
    new_policy = next(p for c, p in calls if c == "New-SafeLinksPolicy")
    assert new_policy["EnableSafeLinksForOffice"] is True
    assert new_policy["TrackClicks"] is True
    assert new_policy["AllowClickThrough"] is False
    new_rule = next(p for c, p in calls if c == "New-SafeLinksRule")
    assert new_rule["RecipientDomainIs"] == ["contoso.com", "contoso.onmicrosoft.com"]


@pytest.mark.anyio("asyncio")
async def test_dkim_remediation_enables_ready_domains_and_reports_missing_cnames():
    calls: list[tuple[str, dict]] = []

    async def fake_exo(token, tenant, cmdlet, params=None):
        params = params or {}
        calls.append((cmdlet, params))
        if cmdlet == "Get-DkimSigningConfig" and "Identity" not in params:
            return {"value": [
                {"Domain": "ready.com", "Enabled": False},
                {"Domain": "nodns.com", "Enabled": False},
            ]}
        if cmdlet == "Set-DkimSigningConfig" and params["Identity"] == "nodns.com":
            raise M365Error("CNAME record does not exist for this config")
        if cmdlet == "Get-DkimSigningConfig":
            return {"value": [{
                "Domain": "nodns.com",
                "Selector1CNAME": "selector1-nodns-com._domainkey.contoso.onmicrosoft.com",
                "Selector2CNAME": "selector2-nodns-com._domainkey.contoso.onmicrosoft.com",
            }]}
        return {}

    result = await _remediate(
        "bp_dkim_enabled_all_domains",
        _exo_invoke_command=AsyncMock(side_effect=fake_exo),
        **{"companies_repo.get_email_domains_for_company": AsyncMock(
            return_value=["ready.com", "nodns.com"]
        )},
    )
    assert result["success"] is False
    assert ("Set-DkimSigningConfig", {"Identity": "ready.com", "Enabled": True}) in calls
    assert "Enabled DKIM for ready.com" in result["message"]
    assert "selector1-nodns-com._domainkey.contoso.onmicrosoft.com" in result["message"]


# ---------------------------------------------------------------------------
# Microsoft Graph
# ---------------------------------------------------------------------------


@pytest.mark.anyio("asyncio")
async def test_domain_password_remediation_patches_managed_domains_only():
    patched: list[tuple[str, dict]] = []

    async def fake_patch(token, url, payload, **kwargs):
        patched.append((url, payload))
        return {}

    result = await _remediate(
        "bp_password_expiry_never_expire",
        _graph_get_all=AsyncMock(return_value=[
            {"id": "contoso.com", "isVerified": True, "authenticationType": "Managed",
             "passwordValidityPeriodInDays": 90},
            {"id": "ok.com", "isVerified": True, "authenticationType": "Managed",
             "passwordValidityPeriodInDays": 2147483647},
        ]),
        _graph_patch=AsyncMock(side_effect=fake_patch),
    )
    assert result["success"] is True
    assert patched == [(
        "https://graph.microsoft.com/v1.0/domains/contoso.com",
        {"passwordValidityPeriodInDays": 2147483647},
    )]


@pytest.mark.anyio("asyncio")
async def test_domain_password_remediation_reports_federated_domains():
    result = await _remediate(
        "bp_password_never_expires",
        _graph_get_all=AsyncMock(return_value=[
            {"id": "fed.com", "isVerified": True, "authenticationType": "Federated",
             "passwordValidityPeriodInDays": 90},
        ]),
        _graph_patch=AsyncMock(),
    )
    assert result["success"] is False
    assert "fed.com" in result["message"]


@pytest.mark.anyio("asyncio")
async def test_security_defaults_enabled_when_no_conditional_access():
    graph_patch = AsyncMock(return_value={})
    result = await _remediate(
        "bp_security_defaults",
        _graph_get=AsyncMock(return_value={"isEnabled": False}),
        _safe_graph_get_all=AsyncMock(return_value=[]),
        _graph_patch=graph_patch,
    )
    assert result["success"] is True
    graph_patch.assert_awaited_once_with(
        "graph-token", bp_service._SECURITY_DEFAULTS_URL, {"isEnabled": True}
    )


@pytest.mark.anyio("asyncio")
async def test_security_defaults_not_enabled_alongside_conditional_access():
    graph_patch = AsyncMock(return_value={})
    result = await _remediate(
        "bp_security_defaults",
        _graph_get=AsyncMock(return_value={"isEnabled": False}),
        _safe_graph_get_all=AsyncMock(return_value=[{"displayName": "CA1", "state": "enabled"}]),
        _graph_patch=graph_patch,
    )
    assert result["success"] is False
    graph_patch.assert_not_awaited()


@pytest.mark.anyio("asyncio")
async def test_security_defaults_appropriate_disables_defaults_when_ca_in_use():
    graph_patch = AsyncMock(return_value={})
    result = await _remediate(
        "bp_security_defaults_appropriate",
        _graph_get=AsyncMock(return_value={"isEnabled": True}),
        _safe_graph_get_all=AsyncMock(return_value=[{"displayName": "CA1", "state": "enabled"}]),
        _graph_patch=graph_patch,
    )
    assert result["success"] is True
    graph_patch.assert_awaited_once_with(
        "graph-token", bp_service._SECURITY_DEFAULTS_URL, {"isEnabled": False}
    )


def _ga_graph_get(url_role_value=True):
    async def fake_get(token, url):
        if "directoryRoles" in url:
            return {"value": [{"id": "ga-role"}]} if url_role_value else {"value": []}
        return {}
    return fake_get


_BREAK_GLASS_MEMBERS = [
    {"id": "bg-1", "userPrincipalName": "myportal-emergency-admin-1-abc@contoso.com",
     "accountEnabled": True, "onPremisesSyncEnabled": None},
    {"id": "admin-1", "userPrincipalName": "admin@contoso.com",
     "accountEnabled": True, "onPremisesSyncEnabled": None},
]


@pytest.mark.anyio("asyncio")
async def test_conditional_access_remediation_creates_policy_excluding_break_glass():
    async def fake_get_all(token, url):
        if "directoryRoles" in url:
            return _BREAK_GLASS_MEMBERS
        return []

    graph_post = AsyncMock(return_value={"id": "new"})
    result = await _remediate(
        "bp_block_legacy_auth",
        _safe_graph_get=AsyncMock(return_value={"isEnabled": False}),
        _graph_get=AsyncMock(side_effect=_ga_graph_get()),
        _graph_get_all=AsyncMock(side_effect=fake_get_all),
        _graph_post=graph_post,
    )
    assert result["success"] is True
    url, payload = graph_post.await_args.args[1:3]
    assert url == bp_service._CA_POLICIES_URL
    assert payload["state"] == "enabled"
    assert payload["conditions"]["clientAppTypes"] == ["exchangeActiveSync", "other"]
    assert payload["conditions"]["users"]["excludeUsers"] == ["bg-1"]
    assert payload["grantControls"]["builtInControls"] == ["block"]
    # The shared template must not be mutated by remediation.
    assert "excludeUsers" not in bp_service._ca_policy_templates()["bp_block_legacy_auth"]["conditions"]["users"]


@pytest.mark.anyio("asyncio")
async def test_conditional_access_remediation_requires_break_glass_accounts():
    graph_post = AsyncMock()
    result = await _remediate(
        "bp_mfa_for_all_users",
        _safe_graph_get=AsyncMock(return_value={"isEnabled": False}),
        _graph_get=AsyncMock(side_effect=_ga_graph_get()),
        _graph_get_all=AsyncMock(return_value=[_BREAK_GLASS_MEMBERS[1]]),
        _graph_post=graph_post,
    )
    assert result["success"] is False
    assert "break-glass" in result["message"]
    graph_post.assert_not_awaited()


@pytest.mark.anyio("asyncio")
async def test_conditional_access_remediation_blocked_by_security_defaults():
    graph_post = AsyncMock()
    result = await _remediate(
        "bp_admin_mfa",
        _safe_graph_get=AsyncMock(return_value={"isEnabled": True}),
        _graph_post=graph_post,
    )
    assert result["success"] is False
    assert "Security Defaults" in result["message"]
    graph_post.assert_not_awaited()


@pytest.mark.anyio("asyncio")
async def test_conditional_access_remediation_reenables_existing_policy():
    existing = {
        "id": "pol-1",
        "displayName": bp_service._ca_policy_templates()["bp_admin_mfa"]["displayName"],
        "state": "disabled",
        "conditions": {"users": {"includeRoles": ["x"], "excludeUsers": ["bg-1"]}},
    }

    async def fake_get_all(token, url):
        if "directoryRoles" in url:
            return _BREAK_GLASS_MEMBERS
        return [existing]

    graph_patch = AsyncMock(return_value={})
    result = await _remediate(
        "bp_admin_mfa",
        _safe_graph_get=AsyncMock(return_value={"isEnabled": False}),
        _graph_get=AsyncMock(side_effect=_ga_graph_get()),
        _graph_get_all=AsyncMock(side_effect=fake_get_all),
        _graph_patch=graph_patch,
        _graph_post=AsyncMock(),
    )
    assert result["success"] is True
    graph_patch.assert_awaited_once_with(
        "graph-token", f"{bp_service._CA_POLICIES_URL}/pol-1", {"state": "enabled"}
    )


@pytest.mark.anyio("asyncio")
async def test_laps_remediation_puts_full_policy():
    current = {
        "id": "deviceRegistrationPolicy",
        "userDeviceQuota": 50,
        "multiFactorAuthConfiguration": "notRequired",
        "azureADRegistration": {"isAdminConfigurable": False},
        "azureADJoin": {"isAdminConfigurable": True},
        "localAdminPassword": {"isEnabled": False},
    }
    graph_put = AsyncMock(return_value={})
    result = await _remediate(
        "bp_laps_enabled",
        _graph_get=AsyncMock(return_value=current),
        _graph_put=graph_put,
    )
    assert result["success"] is True
    url, payload = graph_put.await_args.args[1:3]
    assert url == bp_service._DEVICE_REG_POLICY_URL
    assert payload == {
        "userDeviceQuota": 50,
        "multiFactorAuthConfiguration": "notRequired",
        "azureADRegistration": {"isAdminConfigurable": False},
        "azureADJoin": {"isAdminConfigurable": True},
        "localAdminPassword": {"isEnabled": True},
    }


@pytest.mark.anyio("asyncio")
async def test_sharepoint_idle_sign_out_remediation_payload():
    graph_patch = AsyncMock(return_value={})
    result = await _remediate("bp_sharepoint_sign_out_inactive_users", _graph_patch=graph_patch)
    assert result["success"] is True
    graph_patch.assert_awaited_once_with(
        "graph-token",
        bp_service._SPO_SETTINGS_URL,
        {"idleSessionSignOut": {
            "isEnabled": True, "warnAfterInSeconds": 2700, "signOutAfterInSeconds": 3600,
        }},
    )


@pytest.mark.anyio("asyncio")
async def test_graph_remediation_repairs_permissions_and_retries_once_on_403():
    graph_patch = AsyncMock(side_effect=[M365Error("forbidden", http_status=403), {}])
    result = await _remediate(
        "bp_self_service_password_reset",
        _graph_patch=graph_patch,
        acquire_delegated_token=AsyncMock(return_value="delegated"),
        try_grant_missing_permissions=AsyncMock(return_value=True),
    )
    assert result["success"] is True
    assert graph_patch.await_count == 2


@pytest.mark.anyio("asyncio")
async def test_graph_remediation_403_without_repair_explains_permission():
    result = await _remediate(
        "bp_self_service_password_reset",
        _graph_patch=AsyncMock(side_effect=M365Error("forbidden", http_status=403)),
        acquire_delegated_token=AsyncMock(return_value=None),
    )
    assert result["success"] is False
    assert "Authorize portal access" in result["message"]


# ---------------------------------------------------------------------------
# Microsoft Teams
# ---------------------------------------------------------------------------


@pytest.mark.anyio("asyncio")
async def test_teams_remediation_requires_provider():
    invoke = AsyncMock()
    result = await _remediate(
        "bp_restrict_anon_users_join_meeting",
        provider_enabled=lambda company_id, provider: False,
        invoke_teams_command=invoke,
    )
    assert result["success"] is False
    assert "Teams provider" in result["message"]
    invoke.assert_not_awaited()


@pytest.mark.anyio("asyncio")
async def test_teams_remediation_runs_set_cmdlet():
    invoke = AsyncMock(return_value={"value": []})
    result = await _remediate(
        "bp_teams_external_files_approved_storage",
        provider_enabled=lambda company_id, provider: True,
        _acquire_teams_access_tokens=AsyncMock(return_value=("g", "t", "tenant")),
        invoke_teams_command=invoke,
    )
    assert result["success"] is True
    kwargs = invoke.await_args.kwargs
    assert kwargs["command"] == "Set-CsTeamsClientConfiguration"
    assert kwargs["parameters"]["Identity"] == "Global"
    assert kwargs["parameters"]["AllowDropBox"] is False


@pytest.mark.anyio("asyncio")
async def test_dialin_lobby_check_evaluates_meeting_policy():
    runner = _entry("bp_dialin_cannot_bypass_lobby")["source"]
    with patch(f"{_SVC}.invoke_teams_command", AsyncMock(
        return_value={"value": [{"AllowPSTNUsersToBypassLobby": True}]}
    )):
        failing = await runner(("g", "t"), "tenant")
    with patch(f"{_SVC}.invoke_teams_command", AsyncMock(
        return_value={"value": [{"AllowPSTNUsersToBypassLobby": False}]}
    )):
        passing = await runner(("g", "t"), "tenant")
    assert failing["status"] == "fail"
    assert passing["status"] == "pass"


@pytest.mark.anyio("asyncio")
async def test_only_org_lobby_check_accepts_stricter_invited_users():
    with patch(f"{_SVC}.invoke_teams_command", AsyncMock(
        return_value={"value": [{"AutoAdmittedUsers": "InvitedUsers"}]}
    )):
        result = await bp_service._check_only_org_bypass_lobby(("g", "t"), "tenant")
    assert result["status"] == "pass"
