from unittest.mock import AsyncMock

import pytest

from app.services import m365_best_practices as service
from app.services.m365 import M365Error


@pytest.fixture
def baseline_config(monkeypatch):
    profiles = [
        {"contact": "Hawkins IT", "group": "IT", "alias": "it", "external": "it@msp.example"},
        {"contact": "Hawkins IT Support", "group": "IT Support", "alias": "itsupport", "external": "support@msp.example"},
    ]
    monkeypatch.setattr(service, "_it_baseline_config", lambda: (profiles, "msp.example"))
    return profiles


@pytest.mark.anyio
async def test_it_contact_baseline_remediation_creates_only_missing_objects(monkeypatch, baseline_config):
    async def invoke(_token, _tenant, cmdlet, params=None):
        if cmdlet == "Get-AcceptedDomain":
            return {"value": [{"Name": "customer.example", "DomainType": "Authoritative", "Default": True}]}
        if cmdlet.startswith("Get-"):
            return {"value": []}
        return {"value": []}

    command = AsyncMock(side_effect=invoke)
    monkeypatch.setattr(service, "_exo_invoke_command", command)

    success, message = await service._remediate_it_contact_baseline("token", "tenant")

    assert success is True
    assert "Created" in message
    calls = [(call.args[2], call.args[3] if len(call.args) > 3 else None) for call in command.await_args_list]
    assert ("New-MailContact", {"Name": "Hawkins IT", "ExternalEmailAddress": "it@msp.example"}) in calls
    assert ("New-DistributionGroup", {
        "Name": "IT Support", "Members": "support@msp.example",
        "PrimarySmtpAddress": "itsupport@customer.example",
        "RequireSenderAuthenticationEnabled": False,
    }) in calls
    assert any(cmdlet == "New-TransportRule" and params["RecipientAddressContainsWords"] == "msp.example"
               for cmdlet, params in calls)


@pytest.mark.anyio
async def test_it_contact_baseline_refuses_to_change_conflicting_existing_object(monkeypatch, baseline_config):
    async def invoke(_token, _tenant, cmdlet, params=None):
        responses = {
            "Get-AcceptedDomain": {"value": [{"Name": "customer.example", "DomainType": "Authoritative", "Default": True}]},
            "Get-MailContact": {"value": [{"Name": "Hawkins IT", "ExternalEmailAddress": "wrong@msp.example", "HiddenFromAddressListsEnabled": True}]},
            "Get-DistributionGroup": {"value": []},
            "Get-Recipient": {"value": []},
            "Get-TransportRule": {"value": []},
        }
        return responses[cmdlet]

    command = AsyncMock(side_effect=invoke)
    monkeypatch.setattr(service, "_exo_invoke_command", command)

    success, message = await service._remediate_it_contact_baseline("token", "tenant")

    assert success is False
    assert "differs" in message
    assert all(not call.args[2].startswith(("New-", "Set-")) for call in command.await_args_list)


@pytest.mark.anyio
async def test_it_contact_baseline_refuses_to_create_when_recipient_conflicts(monkeypatch, baseline_config):
    async def invoke(_token, _tenant, cmdlet, params=None):
        responses = {
            "Get-AcceptedDomain": {"value": [{"Name": "customer.example", "DomainType": "Authoritative", "Default": True}]},
            "Get-MailContact": {"value": []},
            "Get-DistributionGroup": {"value": []},
            "Get-Recipient": {"value": [{"Name": "Hawkins IT", "RecipientTypeDetails": "UserMailbox"}]},
            "Get-TransportRule": {"value": []},
        }
        return responses[cmdlet]

    command = AsyncMock(side_effect=invoke)
    monkeypatch.setattr(service, "_exo_invoke_command", command)

    success, message = await service._remediate_it_contact_baseline("token", "tenant")

    assert success is False
    assert "conflicts with an existing Exchange recipient" in message
    assert all(not call.args[2].startswith(("New-", "Set-")) for call in command.await_args_list)


@pytest.mark.anyio
async def test_it_contact_baseline_treats_create_conflict_as_success_after_reinspect(monkeypatch, baseline_config):
    mail_contacts = [
        [{"Name": "Hawkins IT Support", "ExternalEmailAddress": "support@msp.example", "HiddenFromAddressListsEnabled": True}],
        [
            {"Name": "Hawkins IT", "ExternalEmailAddress": "it@msp.example", "HiddenFromAddressListsEnabled": True},
            {"Name": "Hawkins IT Support", "ExternalEmailAddress": "support@msp.example", "HiddenFromAddressListsEnabled": True},
        ],
    ]

    async def invoke(_token, _tenant, cmdlet, params=None):
        if cmdlet == "Get-AcceptedDomain":
            return {"value": [{"Name": "customer.example", "DomainType": "Authoritative", "Default": True}]}
        if cmdlet == "Get-MailContact":
            current = mail_contacts[0]
            if len(mail_contacts) > 1:
                mail_contacts.pop(0)
            return {"value": current}
        if cmdlet == "Get-DistributionGroup":
            return {"value": [
                {"Name": "IT", "PrimarySmtpAddress": "it@customer.example", "HiddenFromAddressListsEnabled": True, "RequireSenderAuthenticationEnabled": False},
                {"Name": "IT Support", "PrimarySmtpAddress": "itsupport@customer.example", "HiddenFromAddressListsEnabled": True, "RequireSenderAuthenticationEnabled": False},
            ]}
        if cmdlet == "Get-DistributionGroupMember":
            group = params["Identity"]
            external = "it@msp.example" if group == "IT" else "support@msp.example"
            return {"value": [{"ExternalEmailAddress": external}]}
        if cmdlet == "Get-Recipient":
            return {"value": []}
        if cmdlet == "Get-TransportRule":
            return {"value": [{
                "Name": service._IT_BASELINE_RULE_NAME,
                "RecipientAddressContainsWords": ["msp.example"],
                "StopRuleProcessing": True,
                "Enabled": True,
            }]}
        if cmdlet == "New-MailContact":
            raise M365Error("Exchange Online New-MailContact failed (409)", http_status=409)
        return {"value": []}

    command = AsyncMock(side_effect=invoke)
    monkeypatch.setattr(service, "_exo_invoke_command", command)

    success, message = await service._remediate_it_contact_baseline("token", "tenant")

    assert success is True
    assert "Created" in message
    assert any(call.args[2] == "New-MailContact" for call in command.await_args_list)


@pytest.mark.anyio
async def test_it_contact_baseline_rejects_onmicrosoft_default_domain(monkeypatch, baseline_config):
    monkeypatch.setattr(
        service,
        "_exo_invoke_command",
        AsyncMock(return_value={"value": [{
            "Name": "tenant.onmicrosoft.com", "DomainType": "Authoritative", "Default": True
        }]}),
    )

    result = await service._check_it_contact_baseline("token", "tenant")

    assert result["status"] == service.STATUS_FAIL
    assert "onmicrosoft.com" in result["details"]
