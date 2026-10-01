from __future__ import annotations

from typing import Any, Mapping, Sequence

from app.repositories import assets as assets_repo
from app.repositories import customer_content_audience as audience_repo
from app.repositories import knowledge_base as kb_repo


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


def _membership_for_company(
    memberships: Sequence[Mapping[str, Any]], company_id: int
) -> Mapping[str, Any] | None:
    return _membership_map(memberships).get(company_id)


def _has_content_permission(membership: Mapping[str, Any], key: str) -> bool:
    permissions = membership.get("menu_permissions") or membership.get("permissions") or {}
    return isinstance(permissions, Mapping) and str(permissions.get(key) or "none") in {
        "read",
        "write",
    }


def _current_kb_scope_visible(
    article: Mapping[str, Any], user: Mapping[str, Any], memberships: Sequence[Mapping[str, Any]]
) -> bool:
    scope = str(article.get("permission_scope") or "anonymous")
    if scope == "anonymous":
        return True
    if bool(user.get("is_super_admin")):
        return True
    try:
        user_id = int(user.get("id") or 0)
    except (TypeError, ValueError):
        user_id = 0
    if not user_id or scope == "super_admin":
        return False
    if scope == "user":
        return user_id in _int_set(article.get("allowed_user_ids"))
    membership_by_company = _membership_map(memberships)
    company_ids = _int_set(article.get("company_ids"))
    matching = [membership_by_company[c] for c in company_ids if c in membership_by_company]
    if not company_ids:
        matching = list(membership_by_company.values())
    if scope == "company":
        return bool(matching)
    if scope == "company_admin":
        admin_ids = _int_set(article.get("company_admin_ids"))
        if admin_ids:
            matching = [membership_by_company[c] for c in admin_ids if c in membership_by_company]
        return any(bool(membership.get("is_admin")) for membership in matching)
    return False


async def can_access_current_candidate(
    candidate: Mapping[str, Any],
    *,
    user: Mapping[str, Any],
    memberships: Sequence[Mapping[str, Any]],
    cache: dict[tuple[str, str], bool] | None = None,
) -> bool:
    """Re-authorise mutable published records against their live source state.

    Persisted index ACLs remain a fail-closed first gate, but are never trusted to
    grant access to KB articles or assets because publication and role audiences
    can change before the asynchronous index catches up.
    """
    if not can_access_candidate(candidate, user=user, memberships=memberships):
        return False
    source_type = str(candidate.get("source_type") or "")
    source_id = str(candidate.get("source_id") or "")
    key = (source_type, source_id)
    if cache is not None and key in cache:
        return cache[key]
    allowed = True
    if source_type == "knowledge_base":
        try:
            article = await kb_repo.get_article_by_id(int(source_id))
        except (TypeError, ValueError):
            article = await kb_repo.get_article_by_slug(source_id)
        allowed = bool(
            article
            and article.get("is_published")
            and _current_kb_scope_visible(article, user, memberships)
        )
        if allowed and not bool(user.get("is_super_admin")):
            article_id = int(article["id"])
            scope = str(article.get("permission_scope") or "anonymous")
            if scope not in {"anonymous", "user", "super_admin"}:
                allowed = False
                for company_id, membership in _membership_map(memberships).items():
                    role_id = membership.get("role_id")
                    if (
                        role_id is not None
                        and _has_content_permission(membership, "content.knowledge_base")
                        and await audience_repo.role_can_access(
                            company_id, "knowledge_base", article_id, int(role_id)
                        )
                    ):
                        allowed = True
                        break
    elif source_type == "assets":
        try:
            asset = await assets_repo.get_asset_by_id(int(source_id))
        except (TypeError, ValueError):
            asset = None
        allowed = bool(asset)
        if allowed and not bool(user.get("is_super_admin")):
            company_id = int(asset["company_id"])
            membership = _membership_for_company(memberships, company_id)
            allowed = bool(asset.get("customer_visible") and membership)
            if allowed:
                role_id = membership.get("role_id")
                if role_id is None:
                    allowed = not await audience_repo.list_role_ids(
                        company_id, "asset", int(asset["id"])
                    )
                else:
                    allowed = _has_content_permission(membership, "content.assets") and (
                        await audience_repo.role_can_access(
                            company_id, "asset", int(asset["id"]), int(role_id)
                        )
                    )
    if cache is not None:
        cache[key] = allowed
    return allowed
