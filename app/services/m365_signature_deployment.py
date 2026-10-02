"""Deploy MyPortal signature templates to Exchange Online mailboxes.

Microsoft Graph cannot write Outlook signatures, so deployment uses the
Exchange Online ``Set-MailboxMessageConfiguration`` cmdlet through the
InvokeCommand REST endpoint.  That cmdlet sets the mailbox signature used by
Outlook on the web and the new Outlook for Windows.  In tenants where roaming
signatures are active Exchange ignores these values unless the organization has
``PostponeRoamingSignaturesUntilLater`` enabled, so the deployment status of that
setting is surfaced to the operator alongside each deployment.
"""

from __future__ import annotations

import asyncio
import hashlib
from typing import Any

from app.repositories import companies as companies_repo
from app.repositories import staff as staff_repo
from app.services import m365 as m365_service
from app.services import m365_out_of_office as oof_service
from app.services import m365_signatures as signatures_service

MAX_DEPLOY_MAILBOXES = 200
MAX_CLASSIC_OUTLOOK_ADDRESSES = 20
_DEPLOY_CONCURRENCY = 4


async def list_deployment_targets(
    company_id: int, template: dict[str, Any] | None = None
) -> list[dict[str, Any]]:
    """Return company user mailboxes with the staff record used to render each signature.

    When ``template`` is given each target reports in ``targeted`` whether the
    template's targeting conditions include that mailbox's staff member.
    """
    mailboxes = await oof_service.get_selectable_mailboxes(company_id)
    needs_custom_fields = bool(template) and any(
        str(rule.get("field") or "").startswith("custom.")
        for rule in (template or {}).get("targeting_rules") or []
    )
    targets: list[dict[str, Any]] = []
    for mailbox in mailboxes:
        upn = str(mailbox.get("user_principal_name") or "").strip()
        if not upn:
            continue
        staff = await staff_repo.get_staff_by_company_and_email(company_id, upn)
        staff_label = None
        if staff:
            staff_label = " ".join(
                part
                for part in (
                    str(staff.get("first_name") or "").strip(),
                    str(staff.get("last_name") or "").strip(),
                )
                if part
            ) or str(staff.get("email") or upn)
            if needs_custom_fields:
                staff = await signatures_service.with_custom_fields(company_id, staff)
        targets.append(
            {
                "user_principal_name": upn,
                "display_name": str(mailbox.get("display_name") or upn),
                "staff_id": int(staff["id"]) if staff else None,
                "staff_label": staff_label,
                "targeted": (
                    signatures_service.template_applies_to_staff(template, staff)
                    if template
                    else True
                ),
            }
        )
    return targets


async def get_roaming_signature_status(company_id: int) -> dict[str, Any]:
    """Report whether Exchange-managed signatures will take effect in the tenant.

    Returns ``{"postponed": bool | None, "error": str | None}`` where ``postponed``
    is ``True`` when ``PostponeRoamingSignaturesUntilLater`` is enabled.
    """
    try:
        exo_token, tenant_id = await m365_service._acquire_exo_access_token(company_id)
        data = await m365_service._exo_invoke_command(
            exo_token, tenant_id, "Get-OrganizationConfig"
        )
    except m365_service.M365Error as exc:
        return {"postponed": None, "error": str(exc)}
    rows = data.get("value") or []
    config = rows[0] if isinstance(rows, list) and rows and isinstance(rows[0], dict) else {}
    value = config.get("PostponeRoamingSignaturesUntilLater")
    return {"postponed": value if isinstance(value, bool) else None, "error": None}


async def postpone_roaming_signatures(company_id: int) -> None:
    """Enable ``PostponeRoamingSignaturesUntilLater`` so deployed signatures apply."""
    exo_token, tenant_id = await m365_service._acquire_exo_access_token(company_id)
    await m365_service._exo_invoke_command(
        exo_token,
        tenant_id,
        "Set-OrganizationConfig",
        {"PostponeRoamingSignaturesUntilLater": True},
    )


async def deploy_template(
    company_id: int,
    template_id: int,
    mailboxes: list[str],
    *,
    auto_add_new: bool = True,
    auto_add_reply: bool = True,
) -> list[dict[str, Any]]:
    """Render the template for each mailbox's staff record and apply it in Exchange.

    Raises ``ValueError`` for an unknown or unpublished template, or for mailbox
    selections outside the company's matched user mailboxes.  Per-mailbox
    Exchange failures are returned in the result list rather than raised.
    """
    template = await signatures_service.get_template(company_id, template_id)
    if not template:
        raise ValueError("Signature template not found")
    if template.get("status") != "published":
        raise ValueError("Publish the signature template before deploying it")

    requested: list[str] = []
    seen: set[str] = set()
    for mailbox in mailboxes:
        value = str(mailbox or "").strip()
        if value and value.casefold() not in seen:
            seen.add(value.casefold())
            requested.append(value)
    if not requested:
        raise ValueError("Select at least one mailbox")
    if len(requested) > MAX_DEPLOY_MAILBOXES:
        raise ValueError(f"Deploy to at most {MAX_DEPLOY_MAILBOXES} mailboxes at a time")

    targets = {
        str(target["user_principal_name"]).casefold(): target
        for target in await list_deployment_targets(company_id)
    }
    unknown = [mailbox for mailbox in requested if mailbox.casefold() not in targets]
    if unknown:
        raise ValueError("Unknown user mailbox selection: " + ", ".join(unknown))
    unmatched = [
        mailbox for mailbox in requested if not targets[mailbox.casefold()].get("staff_id")
    ]
    if unmatched:
        raise ValueError(
            "No staff record matches these mailboxes, so their signatures cannot be rendered: "
            + ", ".join(unmatched)
        )

    exo_token, tenant_id = await m365_service._acquire_exo_access_token(company_id)
    semaphore = asyncio.Semaphore(_DEPLOY_CONCURRENCY)
    html_source = str(template.get("html_content") or "")
    text_source = str(template.get("text_content") or "")

    async def _deploy(submitted: str) -> dict[str, Any]:
        target = targets[submitted.casefold()]
        mailbox = str(target["user_principal_name"])
        async with semaphore:
            try:
                rendered = await signatures_service.render_preview(
                    company_id,
                    html_content=html_source,
                    text_content=text_source,
                    staff_id=int(target["staff_id"]),
                )
                await m365_service._exo_invoke_command(
                    exo_token,
                    tenant_id,
                    "Set-MailboxMessageConfiguration",
                    {
                        "Identity": mailbox,
                        "SignatureHtml": rendered["html"],
                        "SignatureText": rendered["text"],
                        "SignatureTextOnMobile": rendered["text"],
                        "AutoAddSignature": bool(auto_add_new),
                        "AutoAddSignatureOnReply": bool(auto_add_reply),
                    },
                )
            except (m365_service.M365Error, TypeError, ValueError) as exc:
                return {
                    "mailbox": mailbox,
                    "success": False,
                    "error": str(exc),
                    "missing_tokens": [],
                }
        return {
            "mailbox": mailbox,
            "success": True,
            "error": None,
            "missing_tokens": list(rendered.get("missing_tokens") or []),
        }

    return list(await asyncio.gather(*(_deploy(mailbox) for mailbox in requested)))


def classic_outlook_signature_name(address: str) -> str:
    """Stable Outlook signature name, so re-syncs overwrite the same files."""
    return f"MyPortal ({address})"


def classic_outlook_additional_signature_name(template: dict[str, Any], address: str) -> str:
    """Outlook name for an additional (non-default) signature template."""
    label = str(template.get("name") or template.get("slug") or "Signature").strip()
    return f"MyPortal - {label} ({address})"


async def render_classic_outlook_signatures(
    company_id: int, addresses: list[str]
) -> dict[str, Any]:
    """Render the signatures available to each Outlook account on a tray device.

    Each address gets its primary signature in ``signatures`` (the agent makes
    it the account default) and any other signatures that apply to the staff
    member in ``additional_signatures`` (written so they can be picked in
    Outlook, never set as the default). Only addresses on the company's email
    domains that match an active staff record are rendered; every other
    address is reported in ``skipped`` with a reason so the agent can log why
    it left that account untouched. An address with no applicable template is
    neither rendered nor skipped, which tells the agent that the signatures it
    previously added for that account no longer apply.
    """
    domains = {
        domain.casefold()
        for domain in await companies_repo.get_email_domains_for_company(company_id)
    }
    requested: list[str] = []
    seen: set[str] = set()
    for address in addresses[:MAX_CLASSIC_OUTLOOK_ADDRESSES]:
        value = str(address or "").strip()
        if value and value.casefold() not in seen:
            seen.add(value.casefold())
            requested.append(value)

    skipped: dict[str, str] = {}
    templates = await signatures_service.list_templates(company_id)
    on_date = signatures_service.current_schedule_date()

    signatures: list[dict[str, str]] = []
    additional_signatures: list[dict[str, str]] = []
    primary_slugs: list[str] = []
    for address in requested:
        _, separator, domain = address.rpartition("@")
        if not separator or domain.casefold() not in domains:
            skipped[address] = "Address is not on a company email domain"
            continue
        staff = await staff_repo.get_staff_by_company_and_email(company_id, address)
        if not staff or not staff.get("enabled", True) or staff.get("is_ex_staff"):
            skipped[address] = "No active staff record matches this address"
            continue
        staff = await signatures_service.with_custom_fields(company_id, staff)
        resolved = signatures_service.resolve_staff_signatures(
            templates, staff=staff, on_date=on_date
        )
        entries: list[tuple[dict[str, Any], str, list[dict[str, str]]]] = []
        if resolved["primary"]:
            entries.append(
                (resolved["primary"], classic_outlook_signature_name(address), signatures)
            )
        used_names = {classic_outlook_signature_name(address).casefold()}
        for template in resolved["additional"]:
            name = classic_outlook_additional_signature_name(template, address)
            if name.casefold() in used_names:
                name = f"MyPortal - {template.get('slug')} ({address})"
            used_names.add(name.casefold())
            entries.append((template, name, additional_signatures))
        rendered_entries: list[tuple[list[dict[str, str]], dict[str, str]]] = []
        try:
            for template, name, bucket in entries:
                rendered = await signatures_service.render_preview(
                    company_id,
                    html_content=str(template.get("html_content") or ""),
                    text_content=str(template.get("text_content") or ""),
                    staff_id=int(staff["id"]),
                )
                digest = hashlib.sha256(
                    "\x00".join(
                        (
                            str(template.get("id")),
                            address.casefold(),
                            rendered["html"],
                            rendered["text"],
                        )
                    ).encode("utf-8")
                ).hexdigest()
                rendered_entries.append(
                    (
                        bucket,
                        {
                            "address": address,
                            "name": name,
                            "html": rendered["html"],
                            "text": rendered["text"],
                            "hash": digest,
                        },
                    )
                )
        except (TypeError, ValueError) as exc:
            skipped[address] = f"Signature could not be rendered: {exc}"
            continue
        for bucket, entry in rendered_entries:
            bucket.append(entry)
        if resolved["primary"]:
            slug = str(resolved["primary"].get("slug") or "")
            if slug and slug not in primary_slugs:
                primary_slugs.append(slug)
    return {
        "template_slug": ", ".join(primary_slugs) or None,
        "signatures": signatures,
        "additional_signatures": additional_signatures,
        "skipped": skipped,
    }


__all__ = [
    "MAX_CLASSIC_OUTLOOK_ADDRESSES",
    "MAX_DEPLOY_MAILBOXES",
    "classic_outlook_additional_signature_name",
    "classic_outlook_signature_name",
    "deploy_template",
    "get_roaming_signature_status",
    "list_deployment_targets",
    "postpone_roaming_signatures",
    "render_classic_outlook_signatures",
]
