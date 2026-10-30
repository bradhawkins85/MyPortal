"""Built-in Microsoft 365 standards registered with ``m365_standards``.

Importing this module registers every standard below. Each ``check`` returns
``{"status": "pass"|"fail"|"unknown", "details": str, "affected": [...]}``
and each ``apply`` returns ``{"status": "remediated"|"partial"|"failed",
"details": str}``.
"""

from __future__ import annotations

from typing import Any

from app.services import m365_standards as standards
from app.services.m365 import M365Error, _acquire_exo_access_token, _exo_invoke_command

_MAX_LISTED = 5

# ---------------------------------------------------------------------------
# Delegated Sent Items
# ---------------------------------------------------------------------------

DELEGATED_SENT_ITEMS = "exo_delegated_sent_items"
_SENT_ITEMS_TYPES = ("UserMailbox", "SharedMailbox")


def _mailbox_name(row: dict[str, Any]) -> str:
    return str(
        row.get("PrimarySmtpAddress") or row.get("UserPrincipalName") or row.get("DisplayName")
        or row.get("Identity") or "Unknown mailbox"
    )


def _mailbox_identity(row: dict[str, Any]) -> str:
    return str(row.get("ExchangeGuid") or row.get("PrimarySmtpAddress") or row.get("Identity") or "")


def _sent_items_types(settings: dict[str, Any]) -> list[str]:
    types = [value for value in settings.get("mailbox_types") or _SENT_ITEMS_TYPES if value in _SENT_ITEMS_TYPES]
    return types or list(_SENT_ITEMS_TYPES)


def _sent_items_drift(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        row for row in rows
        if row.get("MessageCopyForSentAsEnabled") is not True
        or row.get("MessageCopyForSendOnBehalfEnabled") is not True
    ]


async def _sent_items_mailboxes(company_id: int, settings: dict[str, Any]) -> tuple[str, str, list[dict[str, Any]]]:
    exo_token, tenant_id = await _acquire_exo_access_token(company_id)
    data = await _exo_invoke_command(
        exo_token, tenant_id, "Get-Mailbox",
        {"ResultSize": "Unlimited", "RecipientTypeDetails": _sent_items_types(settings)},
    )
    rows = [row for row in data.get("value") or [] if isinstance(row, dict)]
    return exo_token, tenant_id, rows


def _listed(names: list[str]) -> str:
    return ", ".join(names[:_MAX_LISTED]) + ("…" if len(names) > _MAX_LISTED else "")


async def check_delegated_sent_items(company_id: int, settings: dict[str, Any]) -> dict[str, Any]:
    try:
        _, _, rows = await _sent_items_mailboxes(company_id, settings)
    except M365Error as exc:
        return {"status": "unknown", "details": f"Unable to read mailboxes from Exchange Online: {exc}", "affected": []}
    if not rows:
        return {"status": "unknown", "details": "No user or shared mailboxes were found to check.", "affected": []}
    drift = [_mailbox_name(row) for row in _sent_items_drift(rows)]
    if not drift:
        return {"status": "pass",
                "details": f"All {len(rows)} mailbox(es) keep a copy of mail sent by delegates.", "affected": []}
    return {
        "status": "fail",
        "details": f"{len(drift)} of {len(rows)} mailbox(es) do not keep delegate-sent mail in Sent Items: {_listed(drift)}",
        "affected": drift,
    }


async def apply_delegated_sent_items(company_id: int, settings: dict[str, Any]) -> dict[str, Any]:
    try:
        exo_token, tenant_id, rows = await _sent_items_mailboxes(company_id, settings)
    except M365Error as exc:
        return {"status": "failed", "details": f"Unable to read mailboxes from Exchange Online: {exc}"}
    drift = _sent_items_drift(rows)
    fixed: list[str] = []
    failed: list[str] = []
    for row in drift:
        identity = _mailbox_identity(row)
        if not identity:
            failed.append(_mailbox_name(row))
            continue
        try:
            await _exo_invoke_command(exo_token, tenant_id, "Set-Mailbox", {
                "Identity": identity,
                "MessageCopyForSentAsEnabled": True,
                "MessageCopyForSendOnBehalfEnabled": True,
            })
            fixed.append(_mailbox_name(row))
        except M365Error:
            failed.append(_mailbox_name(row))
    if not failed:
        return {"status": "remediated", "details": f"Updated {len(fixed)} mailbox(es) to keep delegate-sent mail in Sent Items."}
    if not fixed:
        return {"status": "failed", "details": f"Could not update {len(failed)} mailbox(es): {_listed(failed)}"}
    return {"status": "partial",
            "details": f"Updated {len(fixed)} mailbox(es); could not update {len(failed)}: {_listed(failed)}"}


standards.register(standards.Standard(
    id=DELEGATED_SENT_ITEMS,
    name="Keep delegate-sent mail in Sent Items",
    category="Exchange Online",
    description=(
        "When someone sends as, or on behalf of, a user or shared mailbox, a copy is "
        "saved to that mailbox's Sent Items as well as the sender's."
    ),
    check=check_delegated_sent_items,
    apply=apply_delegated_sent_items,
    default_settings={"mailbox_types": list(_SENT_ITEMS_TYPES)},
))
