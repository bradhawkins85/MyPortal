from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services import m365_admin_consent as consent
from app.services import m365_best_practices as best_practices
from app.services import m365
from app.services.m365 import M365Error
from app.services.m365_access_baseline import REQUIRED_PERMISSIONS


@pytest.fixture
def tenant(monkeypatch):
    smtp = "myportal-admin-consent@customer.example"
    state = SimpleNamespace(
        smtp=smtp, mailbox=None, conflict=None, visible=True, group_exists=True,
        support="support@msp.example", user_enabled=False,
        policy={"isEnabled": False, "notifyReviewers": False,
                "remindersEnabled": False, "requestDurationInDays": 45,
                "reviewers": [
                    {"query": "/users/existing-admin", "queryType": "MicrosoftGraph", "queryRoot": None},
                    {"query": "/groups/admins/transitiveMembers", "queryType": "MicrosoftGraph"},
                ]},
    )
    monkeypatch.setattr(consent, "get_settings", lambda: SimpleNamespace(
        m365_it_support_external_email_address="support@msp.example"))
    monkeypatch.setattr(consent, "_acquire_exo_access_token", AsyncMock(return_value=("exo", "tenant")))

    async def get(_token, url):
        if url == consent.POLICY_URL:
            return deepcopy(state.policy)
        if not state.visible:
            raise M365Error("not found", http_status=404)
        return {"id": "reviewer-id", "mail": smtp, "accountEnabled": state.user_enabled}

    async def put(_token, url, payload):
        assert url == consent.POLICY_URL
        state.policy = deepcopy(payload)
        return {}

    async def command(_token, _tenant, cmdlet, params=None):
        if cmdlet == "Get-DistributionGroup":
            return {"value": [{"Name": "IT Support", "PrimarySmtpAddress": "itsupport@customer.example"}]
                    if state.group_exists else []}
        if cmdlet == "Get-DistributionGroupMember":
            return {"value": [{"PrimarySmtpAddress": state.support}]}
        if cmdlet == "Get-Recipient":
            return {"value": [state.conflict] if state.conflict else ([state.mailbox] if state.mailbox else [])}
        if cmdlet == "Get-Mailbox":
            return {"value": [deepcopy(state.mailbox)] if state.mailbox else []}
        if cmdlet == "New-Mailbox":
            assert params["Shared"] is True
            state.mailbox = {"Name": consent.MAILBOX_NAME, "Alias": consent.MAILBOX_ALIAS,
                             "PrimarySmtpAddress": smtp, "RecipientTypeDetails": "SharedMailbox",
                             "ExternalDirectoryObjectId": "reviewer-id"}
            return {"value": [deepcopy(state.mailbox)]}
        if cmdlet == "Set-Mailbox":
            state.mailbox.update({key: value for key, value in params.items() if key != "Identity"})
            return {"value": []}
        raise AssertionError(cmdlet)

    state.get = AsyncMock(side_effect=get)
    state.put = AsyncMock(side_effect=put)
    state.command = AsyncMock(side_effect=command)
    monkeypatch.setattr(consent, "_graph_get", state.get)
    monkeypatch.setattr(consent, "_graph_put", state.put)
    monkeypatch.setattr(consent, "_exo_invoke_command", state.command)
    return state


@pytest.mark.anyio
async def test_remediation_reuses_baseline_and_preserves_all_reviewers(tenant):
    original = deepcopy(tenant.policy["reviewers"])
    assert (await consent.check("token", 7))["status"] == "fail"
    success, message = await consent.remediate("token", 7)
    assert success, message
    assert tenant.policy == {
        "isEnabled": True, "notifyReviewers": True, "remindersEnabled": False,
        "requestDurationInDays": 45,
        "reviewers": original + [{"query": "/users/reviewer-id", "queryType": "MicrosoftGraph"}],
    }
    assert tenant.mailbox["ForwardingAddress"] == "itsupport@customer.example"
    assert tenant.mailbox["HiddenFromAddressListsEnabled"] is True
    assert tenant.mailbox["DeliverToMailboxAndForward"] is False
    assert (await consent.check("token", 7))["status"] == "pass"
    writes = [call.args[2] for call in tenant.command.await_args_list if not call.args[2].startswith("Get-")]
    assert writes == ["New-Mailbox", "Set-Mailbox"]


@pytest.mark.anyio
async def test_repeat_remediation_does_not_write_or_duplicate_reviewers(tenant):
    await consent.remediate("token", 7)
    tenant.command.reset_mock()
    tenant.put.reset_mock()
    assert (await consent.remediate("token", 7))[0]
    tenant.put.assert_not_awaited()
    assert all(call.args[2].startswith("Get-") for call in tenant.command.await_args_list)
    assert len(tenant.policy["reviewers"]) == 3


@pytest.mark.anyio
@pytest.mark.parametrize("recipient_type", ["UserMailbox", "MailContact", "MailUniversalDistributionGroup"])
async def test_conflicting_recipient_never_changed(tenant, recipient_type):
    tenant.conflict = {"Name": consent.MAILBOX_NAME, "RecipientTypeDetails": recipient_type}
    result = await consent.check("token", 7)
    assert result["status"] == "fail"
    with pytest.raises(consent.ConsentConfigurationError, match="another recipient"):
        await consent.remediate("token", 7)
    tenant.put.assert_not_awaited()
    assert all(call.args[2].startswith("Get-") for call in tenant.command.await_args_list)


@pytest.mark.anyio
async def test_directory_propagation_reports_pending_and_retry_reuses_mailbox(tenant):
    tenant.visible = False
    success, message = await consent.remediate("token", 7)
    assert not success and "propagation" in message
    tenant.put.assert_not_awaited()
    tenant.visible = True
    assert (await consent.remediate("token", 7))[0]
    assert sum(call.args[2] == "New-Mailbox" for call in tenant.command.await_args_list) == 1


@pytest.mark.anyio
async def test_permission_failure_does_not_create_recipients(tenant):
    tenant.get.side_effect = M365Error("Forbidden", http_status=403)
    with pytest.raises(M365Error):
        await consent.remediate("token", 7)
    tenant.command.assert_not_awaited()
    tenant.put.assert_not_awaited()


@pytest.mark.anyio
async def test_bad_baseline_membership_refuses_routing(tenant):
    tenant.support = "someone-else@example.com"
    assert (await consent.check("token", 7))["status"] == "fail"
    with pytest.raises(consent.ConsentConfigurationError, match="membership"):
        await consent.remediate("token", 7)
    tenant.put.assert_not_awaited()
    assert tenant.mailbox is None


@pytest.mark.anyio
async def test_signin_enabled_mailbox_requires_manual_resolution(tenant):
    await consent.remediate("token", 7)
    tenant.user_enabled = True
    tenant.put.reset_mock()
    with pytest.raises(consent.ConsentConfigurationError, match="sign-in disabled"):
        await consent.remediate("token", 7)
    tenant.put.assert_not_awaited()


@pytest.mark.anyio
async def test_missing_config_fails_without_api_calls(tenant, monkeypatch):
    monkeypatch.setattr(consent, "get_settings", lambda: SimpleNamespace(
        m365_it_support_external_email_address=""))
    assert (await consent.check("token", 7))["status"] == "fail"
    tenant.get.assert_not_awaited()


@pytest.mark.anyio
async def test_remediation_dispatch_creates_missing_baseline_then_retries(tenant, monkeypatch):
    tenant.group_exists = False

    async def baseline(_token, _tenant):
        tenant.group_exists = True
        return True, "Created baseline"

    create_baseline = AsyncMock(side_effect=baseline)
    monkeypatch.setattr(best_practices, "_remediate_it_contact_baseline", create_baseline)
    monkeypatch.setattr(best_practices, "acquire_access_token", AsyncMock(return_value="graph"))
    monkeypatch.setattr(best_practices, "_acquire_exo_access_token", AsyncMock(return_value=("exo", "tenant")))
    monkeypatch.setattr(best_practices.bp_repo, "update_remediation_status", AsyncMock())
    result = await best_practices.remediate_check(7, consent.CHECK_ID)
    assert result["success"], result["message"]
    create_baseline.assert_awaited_once_with("exo", "tenant")


def test_catalog_and_permission_contract():
    entry = next(row for row in best_practices.list_best_practices() if row["id"] == consent.CHECK_ID)
    assert entry["has_remediation"] is True
    assert entry["default_enabled"] is True
    assert any(permission.name == "Policy.ReadWrite.ConsentRequest"
               and permission.feature == "admin_consent_requests" for permission in REQUIRED_PERMISSIONS)


@pytest.mark.anyio
async def test_permission_repair_discovers_and_grants_consent_role(tenant, monkeypatch):
    principal_id = "11111111-1111-4111-8111-111111111111"
    role_id = "22222222-2222-4222-8222-222222222222"
    assigned = [{"appRoleId": role} for role in m365._PROVISION_APP_ROLES]

    async def get(_token, url):
        if "appRoleAssignments" in url:
            return {"value": assigned}
        if m365._GRAPH_APP_ID in url:
            return {"value": [{"id": "graph-sp", "appId": m365._GRAPH_APP_ID,
                "appRoles": [{"id": role_id, "value": "Policy.ReadWrite.ConsentRequest",
                              "allowedMemberTypes": ["Application"], "isEnabled": True}]}]}
        if m365._EXO_APP_ID in url:
            return {"value": []}
        return {"value": [{"id": principal_id}]}

    async def post(_token, _url, payload):
        assigned.append(payload)
        return {}

    monkeypatch.setattr(m365, "get_credentials", AsyncMock(return_value={"client_id": "client"}))
    monkeypatch.setattr(m365, "_graph_get", AsyncMock(side_effect=get))
    grant = AsyncMock(side_effect=post)
    monkeypatch.setattr(m365, "_graph_post", grant)
    monkeypatch.setattr(m365.m365_repo, "update_app_token", AsyncMock())
    assert await m365.try_grant_missing_permissions(7, "admin-token") is True
    assert grant.await_args.args[2]["appRoleId"] == role_id
    assert await m365.try_grant_missing_permissions(7, "admin-token") is False
    grant.assert_awaited_once()

    # Provisioning uses the same permission name and actual tenant role ID.
    manifest, grants, _ = await m365._build_required_resource_access("admin-token")
    assert ("graph-sp", role_id) in grants
    assert any({"id": role_id, "type": "Role"} in entry["resourceAccess"] for entry in manifest)
