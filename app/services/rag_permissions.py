from __future__ import annotations

from typing import Any, Mapping, Sequence


def _int_set(values: Any) -> set[int]:
    if not isinstance(values, (list, tuple, set)):
        values = [values]
    result: set[int] = set()
    for value in values:
        try:
            result.add(int(value))
        except (TypeError, ValueError):
            continue
    return result


def _membership_map(
    memberships: Sequence[Mapping[str, Any]],
) -> dict[int, Mapping[str, Any]]:
    result: dict[int, Mapping[str, Any]] = {}
    for membership in memberships:
        ids = _int_set(membership.get("company_id") or membership.get("id"))
        if ids:
            result[next(iter(ids))] = membership
    return result


def can_access_candidate(
    candidate: Mapping[str, Any],
    *,
    user: Mapping[str, Any],
    memberships: Sequence[Mapping[str, Any]],
) -> bool:
    """Authorise indexed evidence using its persisted, source-specific policy.

    The index is shared, so an absent, malformed, or unknown policy must never
    inherit the permissions of the user who happened to index the document.
    """

    scope = candidate.get("permission_scope")
    if not isinstance(scope, Mapping) or int(scope.get("version") or 0) != 1:
        return False
    visibility = str(scope.get("visibility") or "")
    if visibility not in {
        "anonymous",
        "authenticated",
        "company",
        "company_admin",
        "user",
        "super_admin",
    }:
        return False
    if bool(user.get("is_super_admin")):
        return True
    if visibility == "anonymous":
        return True
    try:
        user_id = int(user.get("id") or 0)
    except (TypeError, ValueError):
        user_id = 0
    if visibility == "super_admin" or user_id <= 0:
        return False
    if visibility == "user":
        return user_id in _int_set(scope.get("user_ids"))
    if visibility not in {"authenticated", "company", "company_admin"}:
        return False

    membership_by_company = _membership_map(memberships)
    restricted_companies = _int_set(scope.get("company_ids"))
    matching = (
        [
            membership_by_company[c]
            for c in restricted_companies
            if c in membership_by_company
        ]
        if restricted_companies
        else list(membership_by_company.values())
    )
    if visibility == "authenticated" and not restricted_companies:
        matching = [{}]
    if not matching:
        return False
    if visibility == "company_admin" and not any(
        bool(m.get("is_admin")) for m in matching
    ):
        return False
    required_any = scope.get("required_any") or []
    if required_any and not any(
        any(bool(m.get(flag)) for flag in required_any) for m in matching
    ):
        return False
    return True
