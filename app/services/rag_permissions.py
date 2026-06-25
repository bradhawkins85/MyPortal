from __future__ import annotations

from typing import Any, Mapping, Sequence


def _company_ids(memberships: Sequence[Mapping[str, Any]]) -> set[int]:
    ids: set[int] = set()
    for membership in memberships:
        try:
            ids.add(int(membership.get("company_id") or membership.get("id")))
        except (TypeError, ValueError):
            continue
    return ids


def _has_flag(memberships: Sequence[Mapping[str, Any]], flag: str) -> bool:
    return any(bool(membership.get(flag)) for membership in memberships)


def can_access_candidate(candidate: Mapping[str, Any], *, user: Mapping[str, Any], memberships: Sequence[Mapping[str, Any]]) -> bool:
    if bool(user.get("is_super_admin")):
        return True
    source_type = str(candidate.get("source_type") or "")
    company_id = candidate.get("company_id")
    company_ids = _company_ids(memberships)
    if company_id is not None:
        try:
            if int(company_id) not in company_ids:
                return False
        except (TypeError, ValueError):
            return False
    if source_type in {"products", "packages"}:
        return any(bool(m.get(flag)) for m in memberships for flag in ("can_access_shop", "can_access_orders", "can_access_cart"))
    if source_type == "chats":
        return _has_flag(memberships, "can_access_chat")
    if source_type == "orders":
        return _has_flag(memberships, "can_access_orders")
    if source_type == "assets":
        return _has_flag(memberships, "can_manage_assets")
    if source_type == "staff":
        return any(bool(m.get("can_manage_staff")) or int(m.get("staff_permission") or 0) > 0 for m in memberships)
    if source_type == "issues":
        return _has_flag(memberships, "can_manage_issues")
    if source_type == "mailboxes":
        return _has_flag(memberships, "can_view_m365_user_mailboxes") or _has_flag(memberships, "can_view_m365_shared_mailboxes")
    if source_type == "best_practices":
        return _has_flag(memberships, "can_view_m365_best_practices")
    return True
