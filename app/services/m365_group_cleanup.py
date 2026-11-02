"""Office 365 group cleanup.

Finds Microsoft 365 groups, distribution lists and security groups that still
contain disabled accounts or people recorded as ex-staff in MyPortal, and
removes those members on request.

The scan works from the people outward: it reads the tenant's users once,
picks the accounts that should no longer be in any group, then reads each of
those accounts' direct group memberships. Removal always re-reads the group's
current members from Microsoft 365, so a stale scan or a tampered request can
never remove someone who is active again.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable

from app.core.logging import log_info, log_warning
from app.repositories import m365_group_cleanup as cleanup_repo
from app.repositories import staff as staff_repo
from app.services import m365 as m365_service
from app.services.m365 import M365Error

GRAPH = "https://graph.microsoft.com/v1.0"
_GROUP_SELECT = "id,displayName,mail,mailEnabled,securityEnabled,groupTypes,onPremisesSyncEnabled"
_USER_SELECT = "id,displayName,userPrincipalName,mail,accountEnabled"

REASON_DISABLED = "account_disabled"
REASON_EX_STAFF = "ex_staff"
REASON_STAFF_DISABLED = "staff_disabled"
REASON_LABELS = {
    REASON_DISABLED: "Sign-in blocked in Microsoft 365",
    REASON_EX_STAFF: "Ex-staff in MyPortal",
    REASON_STAFF_DISABLED: "Disabled in MyPortal",
}

METHOD_GRAPH = "graph"
METHOD_EXCHANGE = "exchange"


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _norm(value: Any) -> str:
    return str(value or "").strip().lower()


def describe_group(group: dict[str, Any]) -> dict[str, Any]:
    """Return the plain-language kind of a group and how members are removed."""
    group_types = [str(t).lower() for t in (group.get("groupTypes") or [])]
    mail_enabled = bool(group.get("mailEnabled"))
    security_enabled = bool(group.get("securityEnabled"))
    if "unified" in group_types:
        kind, method = "Microsoft 365 group", METHOD_GRAPH
    elif mail_enabled and security_enabled:
        kind, method = "Mail-enabled security group", METHOD_EXCHANGE
    elif mail_enabled:
        kind, method = "Distribution list", METHOD_EXCHANGE
    else:
        kind, method = "Security group", METHOD_GRAPH

    blocked_reason = None
    if "dynamicmembership" in group_types:
        blocked_reason = "Membership is set by a dynamic rule. Update the rule in Microsoft Entra instead."
    elif group.get("onPremisesSyncEnabled"):
        blocked_reason = "Synced from on-premises Active Directory. Remove these members there."
    elif method == METHOD_EXCHANGE and not _norm(group.get("mail")):
        blocked_reason = "This group has no email address, so Exchange Online cannot update it."
    return {
        "kind": kind,
        "method": method,
        "can_clean": blocked_reason is None,
        "blocked_reason": blocked_reason,
    }


def build_staff_reason_map(rows: Iterable[dict[str, Any]]) -> dict[str, str]:
    """Map lower-cased email -> removal reason for staff who have left.

    An address shared with any active staff record is never flagged, so a
    re-hired person or a duplicate record cannot be removed by mistake.
    """
    active: set[str] = set()
    reasons: dict[str, str] = {}
    for row in rows:
        email = _norm(row.get("email"))
        if not email:
            continue
        if row.get("is_ex_staff"):
            reasons[email] = REASON_EX_STAFF
        elif row.get("enabled") in (0, False, "0"):
            reasons.setdefault(email, REASON_STAFF_DISABLED)
        else:
            active.add(email)
    return {email: reason for email, reason in reasons.items() if email not in active}


def removal_reasons(user: dict[str, Any], staff_reasons: dict[str, str]) -> list[str]:
    """Return why a directory user should be removed from groups (empty = keep)."""
    reasons: list[str] = []
    if user.get("accountEnabled") is False:
        reasons.append(REASON_DISABLED)
    for address in {_norm(user.get("userPrincipalName")), _norm(user.get("mail"))}:
        reason = staff_reasons.get(address) if address else None
        if reason and reason not in reasons:
            reasons.append(reason)
    return reasons


def _member_entry(user: dict[str, Any], reasons: list[str]) -> dict[str, Any]:
    upn = str(user.get("userPrincipalName") or "").strip()
    return {
        "id": str(user.get("id") or ""),
        "display_name": str(user.get("displayName") or "").strip() or upn,
        "user_principal_name": upn,
        "mail": _norm(user.get("mail")),
        "reasons": reasons,
        "reason_labels": [REASON_LABELS[r] for r in reasons if r in REASON_LABELS],
    }


async def _staff_reasons(company_id: int) -> dict[str, str]:
    return build_staff_reason_map(await staff_repo.list_staff_email_statuses(company_id))


async def scan(company_id: int) -> dict[str, Any]:
    """Scan the tenant and cache groups that hold removable members."""
    token = await m365_service.acquire_access_token(company_id, force_client_credentials=True)
    staff_reasons = await _staff_reasons(company_id)
    users = await m365_service._graph_get_all(  # pyright: ignore[reportPrivateUsage]
        token, f"{GRAPH}/users?$select={_USER_SELECT}&$top=999"
    )
    candidates = [
        (user, reasons)
        for user in users
        if user.get("id") and (reasons := removal_reasons(user, staff_reasons))
    ]

    groups: dict[str, dict[str, Any]] = {}
    lookup_failures = 0
    for user, reasons in candidates:
        user_id = m365_service._graph_path_segment(user["id"])  # pyright: ignore[reportPrivateUsage]
        try:
            memberships = await m365_service._graph_get_all(  # pyright: ignore[reportPrivateUsage]
                token, f"{GRAPH}/users/{user_id}/memberOf/microsoft.graph.group?$select={_GROUP_SELECT}"
            )
        except M365Error as exc:
            lookup_failures += 1
            log_warning("Group cleanup: could not read memberships", company_id=company_id, error=str(exc))
            continue
        for group in memberships:
            group_id = str(group.get("id") or "")
            if not group_id:
                continue
            entry = groups.get(group_id)
            if entry is None:
                entry = {
                    "id": group_id,
                    "display_name": str(group.get("displayName") or "").strip() or group_id,
                    "mail": _norm(group.get("mail")),
                    **describe_group(group),
                    "members": [],
                }
                groups[group_id] = entry
            entry["members"].append(_member_entry(user, reasons))

    for entry in groups.values():
        entry["members"].sort(key=lambda m: (m["display_name"].lower(), m["user_principal_name"].lower()))
        entry["removable_count"] = len(entry["members"])
    ordered = sorted(groups.values(), key=lambda g: (-g["removable_count"], g["display_name"].lower()))
    result = {
        "groups": ordered,
        "users_checked": len(users),
        "removable_people": len(candidates),
        "lookup_failures": lookup_failures,
    }
    await cleanup_repo.save_scan(company_id, result, scanned_at=_now())
    log_info(
        "M365 group cleanup scan completed",
        company_id=company_id,
        groups=len(ordered),
        removable_people=len(candidates),
    )
    return result


async def get_latest(company_id: int) -> dict[str, Any] | None:
    return await cleanup_repo.get_scan(company_id)


async def _remove_member(
    *, company_id: int, group: dict[str, Any], member: dict[str, Any], graph_token: str,
    exo: tuple[str, str] | None,
) -> None:
    if describe_group(group)["method"] == METHOD_EXCHANGE:
        assert exo is not None
        exo_token, tenant_id = exo
        await m365_service._exo_invoke_command(  # pyright: ignore[reportPrivateUsage]
            exo_token,
            tenant_id,
            "Remove-DistributionGroupMember",
            {
                "Identity": _norm(group.get("mail")),
                "Member": member.get("userPrincipalName") or member["id"],
                "BypassSecurityGroupManagerCheck": True,
                "Confirm": False,
            },
        )
        return
    group_id = m365_service._graph_object_id(group["id"])  # pyright: ignore[reportPrivateUsage]
    member_id = m365_service._graph_object_id(member["id"])  # pyright: ignore[reportPrivateUsage]
    await m365_service._graph_delete(  # pyright: ignore[reportPrivateUsage]
        graph_token, f"{GRAPH}/groups/{group_id}/members/{member_id}/$ref"
    )


async def clean_group(
    company_id: int, group_id: str, member_ids: Iterable[str] | None = None
) -> dict[str, Any]:
    """Remove disabled/ex-staff members from one group.

    Members are re-read from Microsoft 365 and re-checked first. When
    ``member_ids`` is given only those (still removable) members are removed.
    """
    safe_group_id = m365_service._graph_object_id(group_id)  # pyright: ignore[reportPrivateUsage]
    token = await m365_service.acquire_access_token(company_id, force_client_credentials=True)
    group = await m365_service._graph_get(  # pyright: ignore[reportPrivateUsage]
        token, f"{GRAPH}/groups/{safe_group_id}?$select={_GROUP_SELECT}"
    )
    info = describe_group(group)
    if not info["can_clean"]:
        raise ValueError(info["blocked_reason"])

    staff_reasons = await _staff_reasons(company_id)
    members = await m365_service._graph_get_all(  # pyright: ignore[reportPrivateUsage]
        token, f"{GRAPH}/groups/{safe_group_id}/members/microsoft.graph.user?$select={_USER_SELECT}&$top=999"
    )
    removable = [
        (member, reasons)
        for member in members
        if member.get("id") and (reasons := removal_reasons(member, staff_reasons))
    ]
    wanted = {_norm(m) for m in member_ids} if member_ids is not None else None
    if wanted is not None:
        removable = [(m, r) for m, r in removable if _norm(m["id"]) in wanted]

    exo = None
    if info["method"] == METHOD_EXCHANGE and removable:
        exo = await m365_service._acquire_exo_access_token(company_id)  # pyright: ignore[reportPrivateUsage]

    removed: list[dict[str, Any]] = []
    failed: list[dict[str, Any]] = []
    for member, reasons in removable:
        entry = _member_entry(member, reasons)
        try:
            await _remove_member(
                company_id=company_id, group=group, member=member, graph_token=token, exo=exo
            )
        except M365Error as exc:
            log_warning(
                "Group cleanup: member removal failed",
                company_id=company_id, group_id=safe_group_id, http_status=exc.http_status, error=str(exc),
            )
            failed.append({**entry, "error": _friendly_error(exc)})
            continue
        removed.append(entry)

    remaining = [
        _member_entry(m, r) for m, r in
        ((m, removal_reasons(m, staff_reasons)) for m in members)
        if r and _norm(m.get("id")) not in {_norm(x["id"]) for x in removed}
    ]
    await _update_cached_group(company_id, safe_group_id, remaining)
    return {
        "group": {"id": safe_group_id, "display_name": str(group.get("displayName") or ""), **info},
        "removed": removed,
        "failed": failed,
        "remaining_count": len(remaining),
    }


def _friendly_error(exc: M365Error) -> str:
    if exc.http_status == 403:
        return (
            "Microsoft 365 refused the change. Grant the MyPortal app GroupMember.ReadWrite.All "
            "(groups) or the Exchange Administrator role (distribution lists), then try again."
        )
    if exc.http_status == 404:
        return "Microsoft 365 could not find this member in the group. It may already have been removed."
    return "Microsoft 365 could not remove this member. Try again later."


async def _update_cached_group(company_id: int, group_id: str, remaining: list[dict[str, Any]]) -> None:
    cached = await cleanup_repo.get_scan(company_id)
    if not cached:
        return
    result = cached.get("result") or {}
    groups = []
    for entry in result.get("groups") or []:
        if entry.get("id") == group_id:
            if not remaining:
                continue
            entry = {**entry, "members": remaining, "removable_count": len(remaining)}
        groups.append(entry)
    result["groups"] = groups
    await cleanup_repo.save_scan(company_id, result)
