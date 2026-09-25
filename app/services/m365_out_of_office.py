from __future__ import annotations

from urllib.parse import quote

from app.repositories import companies as companies_repo
from app.repositories import m365 as m365_repo
from app.schemas.m365_out_of_office import OutOfOfficeCreate, OutOfOfficeDisable
from app.services import m365 as m365_service


def _mailbox_uses_company_domain(mailbox: dict[str, object], domains: set[str]) -> bool:
    """Return whether a mailbox UPN uses an explicitly configured company domain."""
    upn = str(mailbox.get("user_principal_name") or "").strip()
    _, separator, domain = upn.rpartition("@")
    return bool(separator and domain.casefold() in domains)


async def get_selectable_mailboxes(company_id: int) -> list[dict[str, object]]:
    """Return cached user mailboxes restricted to the company's email domains."""
    domains = {
        domain.casefold()
        for domain in await companies_repo.get_email_domains_for_company(company_id)
    }
    mailboxes = await m365_repo.get_mailboxes(company_id, "UserMailbox")
    return [mailbox for mailbox in mailboxes if _mailbox_uses_company_domain(mailbox, domains)]


async def set_automatic_replies(company_id: int, payload: OutOfOfficeCreate) -> list[dict[str, object]]:
    """Set scheduled automatic replies, restricted to cached user mailboxes."""
    allowed = {
        str(row["user_principal_name"]).casefold(): str(row["user_principal_name"])
        for row in await get_selectable_mailboxes(company_id)
    }
    requested = [str(mailbox) for mailbox in payload.mailboxes]
    unknown = [mailbox for mailbox in requested if mailbox.casefold() not in allowed]
    if unknown:
        raise ValueError("Unknown user mailbox selection: " + ", ".join(unknown))

    token = await m365_service.acquire_access_token(company_id, force_client_credentials=True)
    setting = {
        "automaticRepliesSetting": {
            "status": "scheduled",
            "externalAudience": payload.external_audience,
            "scheduledStartDateTime": {
                "dateTime": payload.start_time.replace(tzinfo=None).isoformat(),
                "timeZone": "UTC",
            },
            "scheduledEndDateTime": {
                "dateTime": payload.end_time.replace(tzinfo=None).isoformat(),
                "timeZone": "UTC",
            },
            "internalReplyMessage": payload.internal_message,
            "externalReplyMessage": payload.external_message,
        }
    }
    results: list[dict[str, object]] = []
    for submitted in requested:
        mailbox = allowed[submitted.casefold()]
        url = "https://graph.microsoft.com/v1.0/users/" + quote(mailbox, safe="") + "/mailboxSettings"
        try:
            await m365_service._graph_patch(token, url, setting)
            results.append({"mailbox": mailbox, "success": True, "error": None})
        except m365_service.M365Error as exc:
            results.append({"mailbox": mailbox, "success": False, "error": str(exc)})
    return results


async def get_automatic_replies(company_id: int) -> list[dict[str, object]]:
    """Read current Graph state for each selectable mailbox, retaining individual errors."""
    mailboxes = await get_selectable_mailboxes(company_id)
    if not mailboxes:
        return []
    token = await m365_service.acquire_access_token(company_id, force_client_credentials=True)
    results: list[dict[str, object]] = []
    for row in mailboxes:
        mailbox = str(row["user_principal_name"])
        url = "https://graph.microsoft.com/v1.0/users/" + quote(mailbox, safe="") + "/mailboxSettings"
        try:
            response = await m365_service._graph_get(token, url)
            setting = response.get("automaticRepliesSetting") or {}
            results.append({"mailbox": mailbox, "success": True, "setting": setting, "error": None})
        except m365_service.M365Error as exc:
            results.append({"mailbox": mailbox, "success": False, "setting": None, "error": str(exc)})
    return results


async def disable_automatic_replies(
    company_id: int, payload: OutOfOfficeDisable
) -> list[dict[str, object]]:
    """Disable replies without overwriting messages, audience, or schedule fields."""
    allowed = {
        str(row["user_principal_name"]).casefold(): str(row["user_principal_name"])
        for row in await get_selectable_mailboxes(company_id)
    }
    requested = [str(mailbox) for mailbox in payload.mailboxes]
    unknown = [mailbox for mailbox in requested if mailbox.casefold() not in allowed]
    if unknown:
        raise ValueError("Unknown user mailbox selection: " + ", ".join(unknown))
    token = await m365_service.acquire_access_token(company_id, force_client_credentials=True)
    results: list[dict[str, object]] = []
    for submitted in requested:
        mailbox = allowed[submitted.casefold()]
        url = "https://graph.microsoft.com/v1.0/users/" + quote(mailbox, safe="") + "/mailboxSettings"
        try:
            await m365_service._graph_patch(
                token, url, {"automaticRepliesSetting": {"status": "disabled"}}
            )
            results.append({"mailbox": mailbox, "success": True, "error": None})
        except m365_service.M365Error as exc:
            results.append({"mailbox": mailbox, "success": False, "error": str(exc)})
    return results
