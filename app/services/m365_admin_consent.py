"""Admin-consent notifications delivered through the existing IT mail baseline.

Reviewer scopes resolve to Entra users, not Exchange mail contacts. A dedicated
shared mailbox supplies that identity without granting an administrator role;
Exchange forwards its notifications to the baseline support distribution group.
"""
from __future__ import annotations

from typing import Any
from urllib.parse import quote

from app.core.config import get_settings
from app.services.m365 import (
    M365Error, _acquire_exo_access_token, _coerce_exo_bool,
    _exo_invoke_command, _graph_get, _graph_put,
)

POLICY_URL = "https://graph.microsoft.com/v1.0/policies/adminConsentRequestPolicy"
MAILBOX_NAME = "MyPortal Admin Consent Requests"
MAILBOX_ALIAS = "myportal-admin-consent"
CHECK_ID = "bp_admin_consent_requests"
CHECK_NAME = "Route admin consent requests to IT support"
ROUTING_GROUP = "IT Support"


class ConsentConfigurationError(M365Error):
    """A remediable configuration gap, rather than a failed assessment."""


class MissingBaselineError(ConsentConfigurationError):
    """The existing IT baseline needs to be provisioned first."""


def _rows(response: dict[str, Any]) -> list[dict[str, Any]]:
    return [row for row in (response.get("value") or []) if isinstance(row, dict)]


def _smtp(value: Any) -> str:
    return str(value or "").strip().lower().removeprefix("smtp:")


def _recipient_addresses(recipient: dict[str, Any]) -> set[str]:
    aliases = recipient.get("EmailAddresses") or []
    if isinstance(aliases, str):
        aliases = [aliases]
    return {_smtp(recipient.get("PrimarySmtpAddress")), *(_smtp(value) for value in aliases)}


async def inspect(graph_token: str, company_id: int) -> dict[str, Any]:
    support = get_settings().m365_it_support_external_email_address.strip().lower()
    if not support or "@" not in support:
        raise ConsentConfigurationError("Configure M365_IT_SUPPORT_EXTERNAL_EMAIL_ADDRESS for ticket delivery.")
    # Read the policy before any Exchange work: missing Graph permissions must
    # not leave partially provisioned recipients behind.
    policy = await _graph_get(graph_token, POLICY_URL)
    exo_token, tenant_id = await _acquire_exo_access_token(company_id)

    async def command(name: str, params: dict[str, Any] | None = None):
        return await _exo_invoke_command(exo_token, tenant_id, name, params)

    groups = _rows(await command("Get-DistributionGroup", {"ResultSize": "Unlimited"}))
    group = next((row for row in groups if row.get("Name") == ROUTING_GROUP), None)
    if group is None:
        raise MissingBaselineError("Apply the IT contact forwarding baseline first; IT Support is missing.")
    members = _rows(await command("Get-DistributionGroupMember", {
        "Identity": group["Identity"] if group.get("Identity") else ROUTING_GROUP,
        "ResultSize": "Unlimited",
    }))
    addresses = {_smtp(row.get("ExternalEmailAddress") or row.get("PrimarySmtpAddress"))
                 for row in members}
    if addresses != {support}:
        raise ConsentConfigurationError("IT Support baseline membership does not match the configured support email; resolve it before enabling consent routing.")
    target = _smtp(group.get("PrimarySmtpAddress"))
    if not target or "@" not in target or target == support:
        raise ConsentConfigurationError("IT Support needs a tenant SMTP address distinct from its external support destination.")
    smtp = f"{MAILBOX_ALIAS}@{target.split('@', 1)[1]}"
    recipients = _rows(await command("Get-Recipient", {"ResultSize": "Unlimited"}))
    matches = [row for row in recipients if (
        str(row.get("Name") or "").casefold() == MAILBOX_NAME.casefold()
        or str(row.get("Alias") or "").casefold() == MAILBOX_ALIAS
        or smtp in _recipient_addresses(row)
    )]
    mailbox = None
    if matches:
        if len(matches) != 1 or matches[0].get("RecipientTypeDetails") != "SharedMailbox":
            raise ConsentConfigurationError("The admin consent mailbox name or address belongs to another recipient; no existing recipient will be repurposed.")
        mailbox = next(iter(_rows(await command("Get-Mailbox", {"Identity": smtp}))), None)
        if (not mailbox or mailbox.get("Name") != MAILBOX_NAME
                or _smtp(mailbox.get("PrimarySmtpAddress")) != smtp):
            raise ConsentConfigurationError("The admin consent shared mailbox conflicts with the expected name/address.")
    reviewer = None
    if mailbox and mailbox.get("ExternalDirectoryObjectId"):
        user_id = str(mailbox["ExternalDirectoryObjectId"])
        # A newly created mailbox can precede its Entra user. Do not claim that
        # notifications work until the identity is visible to Graph.
        try:
            user = await _graph_get(graph_token,
                f"https://graph.microsoft.com/v1.0/users/{quote(user_id, safe='')}?$select=id,mail,accountEnabled")
        except M365Error as exc:
            if exc.http_status != 404:
                raise
            user = {}
        if user and user.get("accountEnabled") is not False:
            raise ConsentConfigurationError("The consent notification mailbox must have sign-in disabled; disable it before retrying.")
        if _smtp(user.get("mail")) == smtp and user.get("id"):
            reviewer = {"query": f"/users/{user['id']}", "queryType": "MicrosoftGraph"}
    forwarding_ok = False
    if mailbox:
        forwarding = str(mailbox.get("ForwardingAddress") or "").casefold()
        group_identifiers = {str(group.get(key) or "").casefold() for key in (
            "Identity", "DistinguishedName", "Guid", "Name", "PrimarySmtpAddress")}
        group_identifiers.discard("")
        forwarding_ok = (forwarding in group_identifiers
                         and not mailbox.get("ForwardingSmtpAddress")
                         and not _coerce_exo_bool(mailbox.get("DeliverToMailboxAndForward")))
    scopes = policy.get("reviewers") or []
    scope_ok = reviewer is not None and any(
        row.get("query", "").rstrip("/").casefold() == reviewer["query"].casefold()
        and row.get("queryType") == "MicrosoftGraph" for row in scopes)
    compliant = (policy.get("isEnabled") is True and policy.get("notifyReviewers") is True
                 and scope_ok and forwarding_ok and mailbox is not None
                 and _coerce_exo_bool(mailbox.get("HiddenFromAddressListsEnabled")))
    return {"policy": policy, "mailbox": mailbox, "smtp": smtp, "target": target,
            "reviewer": reviewer, "compliant": compliant, "command": command}


async def check(graph_token: str, company_id: int) -> dict[str, Any]:
    try:
        state = await inspect(graph_token, company_id)
    except ConsentConfigurationError as exc:
        return {"check_id": CHECK_ID, "check_name": CHECK_NAME,
                "status": "fail", "details": str(exc)}
    return {"check_id": CHECK_ID, "check_name": CHECK_NAME,
            "status": "pass" if state["compliant"] else "fail",
            "details": (
                f"Admin consent notifications are enabled and routed through {state['target']} to the support ticket email. Test a consent request to verify ticket ingestion."
                if state["compliant"] else
                "Admin consent requests, reviewer notifications, or support mailbox routing are incomplete. Run remediation; existing reviewers are preserved.")}


async def remediate(graph_token: str, company_id: int) -> tuple[bool, str]:
    state = await inspect(graph_token, company_id)
    if state["compliant"]:
        return True, "Admin consent request notifications are already configured for support."
    command = state["command"]
    if state["mailbox"] is None:
        await command("New-Mailbox", {"Shared": True, "Name": MAILBOX_NAME,
            "Alias": MAILBOX_ALIAS, "PrimarySmtpAddress": state["smtp"]})
    await command("Set-Mailbox", {"Identity": state["smtp"],
        "HiddenFromAddressListsEnabled": True, "ForwardingAddress": state["target"],
        "ForwardingSmtpAddress": None, "DeliverToMailboxAndForward": False})
    state = await inspect(graph_token, company_id)
    if state["reviewer"] is None:
        return False, "The hidden forwarding mailbox is configured, but its Entra user is not visible yet. Wait for directory propagation, then retry remediation."
    # PUT replaces the singleton. Re-read immediately before writing and retain
    # every existing scope (including role/group queries), expiry and reminders.
    policy = await _graph_get(graph_token, POLICY_URL)
    reviewers = [{key: value for key, value in row.items()
                  if key in {"query", "queryType", "queryRoot"}}
                 for row in (policy.get("reviewers") or [])]
    if not any(row.get("query", "").rstrip("/").casefold() == state["reviewer"]["query"].casefold()
               and row.get("queryType") == "MicrosoftGraph" for row in reviewers):
        reviewers.append(state["reviewer"])
    await _graph_put(graph_token, POLICY_URL, {
        "isEnabled": True, "notifyReviewers": True,
        "remindersEnabled": policy.get("remindersEnabled", True),
        "requestDurationInDays": policy.get("requestDurationInDays", 30),
        "reviewers": reviewers,
    })
    verified = await inspect(graph_token, company_id)
    if not verified["compliant"]:
        return False, "Consent routing was submitted but is not fully visible. Wait for propagation and rerun the check."
    return True, "Enabled admin consent requests and support notifications, preserving existing reviewers. Submit a test consent request to verify ticket creation."
