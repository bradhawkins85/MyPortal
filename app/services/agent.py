from __future__ import annotations

import importlib
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Mapping, Sequence

from app.core.database import db
from app.core.features import get_registry, module_name_for_slug
from app.core.logging import log_error
from app.repositories import m365_best_practices as m365_bp_repo
from app.repositories import reporting as reporting_repo
from app.repositories import shop as shop_repo
from app.repositories import tickets as tickets_repo
from app.services import company_access
from app.services import backup_jobs as backup_jobs_service
from app.services import issues as issues_service
from app.services import reports as reports_service
from app.services import knowledge_base as knowledge_base_service
from app.services import modules as modules_service

_KB_RESULT_LIMIT = 5
_TICKET_RESULT_LIMIT = 5
_PRODUCT_RESULT_LIMIT = 5
_PACKAGE_RESULT_LIMIT = 5
_CHAT_RESULT_LIMIT = 5
_ORDER_RESULT_LIMIT = 5
_ASSET_RESULT_LIMIT = 5
_FEATURE_PACK_RESULT_LIMIT = 5
_COMPANY_RESULT_LIMIT = 5
_ISSUE_RESULT_LIMIT = 5
_SYSTEM_RESULT_LIMIT = 5
_MAX_SNIPPET_LENGTH = 320


def _utc_iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if not isinstance(dt, datetime):
        return str(dt)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def _format_currency(amount: Decimal | None) -> str | None:
    if amount is None:
        return None
    quantized = amount.quantize(Decimal("0.01"))
    return f"${quantized:,.2f}"


def _truncate(value: str | None, limit: int = _MAX_SNIPPET_LENGTH) -> str | None:
    if not value:
        return None
    text = " ".join(str(value).split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _normalise_memberships(
    memberships: Sequence[Mapping[str, Any]] | None,
) -> list[Mapping[str, Any]]:
    if not memberships:
        return []
    normalised: list[Mapping[str, Any]] = []
    for membership in memberships:
        if isinstance(membership, Mapping):
            normalised.append(membership)
    return normalised


def _extract_company_ids(memberships: Sequence[Mapping[str, Any]]) -> list[int]:
    identifiers: list[int] = []
    for membership in memberships:
        company_id = membership.get("company_id")
        try:
            company_id_int = int(company_id)
        except (TypeError, ValueError):
            continue
        if company_id_int > 0:
            identifiers.append(company_id_int)
    return sorted(set(identifiers))


def _can_access_shop(
    memberships: Sequence[Mapping[str, Any]], *, is_super_admin: bool
) -> bool:
    if is_super_admin:
        return True
    for membership in memberships:
        if any(
            bool(membership.get(flag))
            for flag in ("can_access_shop", "can_access_orders", "can_access_cart")
        ):
            return True
    return False


def _company_summary(memberships: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    summary: list[dict[str, Any]] = []
    for membership in memberships:
        try:
            company_id = int(membership.get("company_id"))
        except (TypeError, ValueError):
            continue
        summary.append(
            {
                "company_id": company_id,
                "company_name": membership.get("company_name")
                or f"Company #{company_id}",
            }
        )
    return summary


def _has_membership_flag(
    memberships: Sequence[Mapping[str, Any]],
    flag: str,
    *,
    is_super_admin: bool,
) -> bool:
    if is_super_admin:
        return True
    return any(bool(membership.get(flag)) for membership in memberships)


def _coerce_user_id(user: Mapping[str, Any]) -> int:
    try:
        return int(user.get("id") or 0)
    except (TypeError, ValueError):
        return 0


def _search_company_sources(
    query: str, memberships: Sequence[Mapping[str, Any]]
) -> list[dict[str, Any]]:
    needle = query.casefold()
    matches: list[dict[str, Any]] = []
    for membership in memberships:
        try:
            company_id = int(membership.get("company_id") or membership.get("id"))
        except (TypeError, ValueError):
            continue
        name = str(
            membership.get("company_name") or membership.get("name") or ""
        ).strip()
        syncro_id = str(membership.get("syncro_company_id") or "").strip()
        searchable = " ".join(
            part for part in (name, syncro_id, str(company_id)) if part
        ).casefold()
        if needle not in searchable:
            continue
        matches.append(
            {
                "id": company_id,
                "name": name or f"Company #{company_id}",
                "syncro_company_id": syncro_id or None,
            }
        )
        if len(matches) >= _COMPANY_RESULT_LIMIT:
            break
    return matches


async def _search_issue_sources(
    query: str,
    *,
    memberships: Sequence[Mapping[str, Any]],
    company_ids: Sequence[int],
    is_super_admin: bool,
) -> list[dict[str, Any]]:
    if not _has_membership_flag(
        memberships, "can_manage_issues", is_super_admin=is_super_admin
    ):
        return []
    overviews = await issues_service.build_issue_overview(
        search=query.strip().lower(),
        company_ids=company_ids or None,
    )
    sources: list[dict[str, Any]] = []
    for overview in overviews[:_ISSUE_RESULT_LIMIT]:
        assignments = [
            {
                "company_id": assignment.company_id,
                "company_name": assignment.company_name,
                "status": assignment.status,
                "status_label": assignment.status_label,
            }
            for assignment in overview.assignments[:3]
        ]
        sources.append(
            {
                "id": overview.issue_id,
                "name": overview.name,
                "slug": overview.slug,
                "description": _truncate(overview.description),
                "updated_at": overview.updated_at_iso,
                "assignments": assignments,
            }
        )
    return sources


def _matches_query(query: str, *values: Any) -> bool:
    needle = query.casefold()
    return needle in " ".join(str(value or "") for value in values).casefold()


async def _search_service_status_sources(
    query: str, *, company_ids: Sequence[int], is_super_admin: bool
) -> list[dict[str, Any]]:
    if not is_super_admin and not company_ids:
        return []
    rows = await db.fetch_all(
        """
        SELECT s.id, s.name, s.description, s.status, s.status_message, s.updated_at
        FROM service_status_services s
        WHERE s.is_active = 1
          AND (
              ? = 1
              OR NOT EXISTS (
                  SELECT 1 FROM service_status_service_companies sc
                  WHERE sc.service_id = s.id
              )
              OR EXISTS (
                  SELECT 1 FROM service_status_service_companies sc
                  WHERE sc.service_id = s.id AND sc.company_id IN ({placeholders})
              )
          )
          AND (s.name LIKE ? OR s.description LIKE ? OR s.status LIKE ? OR s.status_message LIKE ?)
        ORDER BY s.display_order ASC, s.name ASC
        LIMIT ?
        """.format(placeholders=", ".join(["?"] * len(company_ids)) or "NULL"),
        (
            1 if is_super_admin else 0,
            *company_ids,
            f"%{query}%",
            f"%{query}%",
            f"%{query}%",
            f"%{query}%",
            _SYSTEM_RESULT_LIMIT,
        ),
    )
    return [
        {
            "id": row.get("id"),
            "name": row.get("name") or f"Service #{row.get('id')}",
            "description": _truncate(row.get("description")),
            "status": row.get("status"),
            "status_message": _truncate(row.get("status_message")),
        }
        for row in rows or []
    ]


async def _search_backup_job_sources(
    query: str, *, company_ids: Sequence[int], is_super_admin: bool
) -> list[dict[str, Any]]:
    sources: list[dict[str, Any]] = []
    scoped_ids: Sequence[int | None]
    if company_ids:
        scoped_ids = company_ids
    elif is_super_admin:
        scoped_ids = [None]
    else:
        return []
    for company_id in scoped_ids:
        jobs = await backup_jobs_service.list_jobs_with_latest(
            company_id=company_id, include_inactive=is_super_admin
        )
        for job in jobs:
            if not _matches_query(
                query,
                job.get("name"),
                job.get("description"),
                job.get("latest_status"),
                job.get("today_status"),
            ):
                continue
            sources.append(
                {
                    "id": job.get("id"),
                    "company_id": job.get("company_id"),
                    "name": job.get("name") or f"Backup job #{job.get('id')}",
                    "description": _truncate(job.get("description")),
                    "latest_status": job.get("latest_status"),
                    "today_status": job.get("today_status"),
                }
            )
            if len(sources) >= _SYSTEM_RESULT_LIMIT:
                return sources
    return sources


def _search_company_report_sources(query: str) -> list[dict[str, Any]]:
    sources: list[dict[str, Any]] = []
    for section in reports_service.REPORT_SECTIONS:
        if not _matches_query(query, section.key, section.label, section.description):
            continue
        sources.append(
            {
                "key": section.key,
                "title": section.label,
                "description": _truncate(section.description),
                "source_type": "company_report_section",
            }
        )
        if len(sources) >= _SYSTEM_RESULT_LIMIT:
            break
    return sources


async def _search_reporting_query_sources(
    query: str, *, user: Mapping[str, Any], is_super_admin: bool
) -> list[dict[str, Any]]:
    user_id = _coerce_user_id(user)
    if not is_super_admin and user_id <= 0:
        return []
    queries = await reporting_repo.list_queries_for_user(
        user_id, include_all=is_super_admin
    )
    sources: list[dict[str, Any]] = []
    for report in queries:
        if not _matches_query(
            query, report.get("name"), report.get("slug"), report.get("description")
        ):
            continue
        sources.append(
            {
                "key": str(report.get("slug") or report.get("id")),
                "title": report.get("name") or f"Report #{report.get('id')}",
                "description": _truncate(report.get("description")),
                "source_type": "reporting_query",
            }
        )
        if len(sources) >= _SYSTEM_RESULT_LIMIT:
            break
    return sources


async def _search_mailbox_sources(
    query: str, *, memberships: Sequence[Mapping[str, Any]], company_ids: Sequence[int], is_super_admin: bool
) -> list[dict[str, Any]]:
    can_user = _has_membership_flag(
        memberships, "can_view_m365_user_mailboxes", is_super_admin=is_super_admin
    )
    can_shared = _has_membership_flag(
        memberships, "can_view_m365_shared_mailboxes", is_super_admin=is_super_admin
    )
    if (not can_user and not can_shared) or not company_ids:
        return []
    mailbox_types = []
    if can_user:
        mailbox_types.append("UserMailbox")
    if can_shared:
        mailbox_types.append("SharedMailbox")
    placeholders_companies = ", ".join(["?"] * len(company_ids)) or "NULL"
    placeholders_types = ", ".join(["?"] * len(mailbox_types))
    rows = await db.fetch_all(
        f"""
        SELECT company_id, user_principal_name, display_name, mailbox_type, storage_used_bytes
        FROM m365_mailboxes
        WHERE company_id IN ({placeholders_companies})
          AND mailbox_type IN ({placeholders_types})
          AND (user_principal_name LIKE ? OR display_name LIKE ? OR mailbox_type LIKE ?)
        ORDER BY display_name ASC
        LIMIT ?
        """,
        (
            *company_ids,
            *mailbox_types,
            f"%{query}%",
            f"%{query}%",
            f"%{query}%",
            _SYSTEM_RESULT_LIMIT,
        ),
    )
    return [
        {
            "company_id": row.get("company_id"),
            "user_principal_name": row.get("user_principal_name"),
            "display_name": row.get("display_name") or row.get("user_principal_name"),
            "mailbox_type": row.get("mailbox_type"),
            "storage_used_bytes": row.get("storage_used_bytes"),
        }
        for row in rows or []
        if row.get("user_principal_name")
    ]


async def _search_best_practice_sources(
    query: str, *, memberships: Sequence[Mapping[str, Any]], company_ids: Sequence[int], is_super_admin: bool
) -> list[dict[str, Any]]:
    if (
        not _has_membership_flag(
            memberships, "can_view_m365_best_practices", is_super_admin=is_super_admin
        )
        or not company_ids
    ):
        return []
    sources: list[dict[str, Any]] = []
    for company_id in company_ids:
        results = await m365_bp_repo.list_results(company_id)
        for result in results:
            if not _matches_query(
                query,
                result.get("check_id"),
                result.get("check_name"),
                result.get("status"),
                result.get("details"),
            ):
                continue
            sources.append(
                {
                    "company_id": company_id,
                    "check_id": result.get("check_id"),
                    "check_name": result.get("check_name") or result.get("check_id"),
                    "status": result.get("status"),
                    "details": _truncate(result.get("details")),
                }
            )
            if len(sources) >= _SYSTEM_RESULT_LIMIT:
                return sources
    return sources


async def _search_chat_sources(
    query: str,
    *,
    user: Mapping[str, Any],
    is_super_admin: bool,
    can_access_chat: bool,
) -> list[dict[str, Any]]:
    if not can_access_chat:
        return []
    user_id = _coerce_user_id(user)
    if not is_super_admin and user_id <= 0:
        return []
    like = f"%{query}%"
    rows = await db.fetch_all(
        """
        SELECT r.id, r.subject, r.status, r.company_id, r.updated_at, r.linked_ticket_id,
               MAX(m.sent_at) AS last_message_at,
               SUBSTR(MAX(m.body), 1, 320) AS matching_message
        FROM chat_rooms r
        LEFT JOIN chat_messages m ON m.room_id = r.id AND m.redacted_at IS NULL
        WHERE (? = 1
               OR EXISTS (
                   SELECT 1 FROM user_companies uc
                   WHERE uc.company_id = r.company_id AND uc.user_id = ?
               )
               OR EXISTS (
                   SELECT 1 FROM chat_room_participants cp
                   WHERE cp.room_id = r.id AND cp.user_id = ?
               ))
          AND (r.subject LIKE ? OR r.room_alias LIKE ? OR m.body LIKE ?)
        GROUP BY r.id, r.subject, r.status, r.company_id, r.updated_at, r.linked_ticket_id
        ORDER BY COALESCE(MAX(m.sent_at), r.updated_at) DESC
        LIMIT ?
        """,
        (
            1 if is_super_admin else 0,
            user_id,
            user_id,
            like,
            like,
            like,
            _CHAT_RESULT_LIMIT,
        ),
    )
    return [dict(row) for row in rows or []]


async def _search_order_sources(
    query: str, *, user: Mapping[str, Any], is_super_admin: bool
) -> list[dict[str, Any]]:
    user_id = _coerce_user_id(user)
    if not is_super_admin and user_id <= 0:
        return []
    like = f"%{query}%"
    rows = await db.fetch_all(
        """
        SELECT o.order_number, o.company_id, MAX(o.order_date) AS order_date,
               MAX(o.status) AS status, MAX(o.shipping_status) AS shipping_status,
               MAX(o.po_number) AS po_number, MAX(o.consignment_id) AS consignment_id,
               MAX(o.notes) AS notes, COUNT(*) AS item_count
        FROM shop_orders o
        LEFT JOIN shop_products p ON p.id = o.product_id
        WHERE (? = 1
               OR EXISTS (
                   SELECT 1 FROM user_companies uc
                   WHERE uc.company_id = o.company_id AND uc.user_id = ?
               ))
          AND (o.order_number LIKE ? OR o.po_number LIKE ? OR o.notes LIKE ? OR p.name LIKE ?)
        GROUP BY o.order_number, o.company_id
        ORDER BY MAX(o.order_date) DESC
        LIMIT ?
        """,
        (
            1 if is_super_admin else 0,
            user_id,
            like,
            like,
            like,
            like,
            _ORDER_RESULT_LIMIT,
        ),
    )
    return [dict(row) for row in rows or []]


async def _search_asset_sources(
    query: str, *, user: Mapping[str, Any], is_super_admin: bool
) -> list[dict[str, Any]]:
    user_id = _coerce_user_id(user)
    if not is_super_admin and user_id <= 0:
        return []
    like = f"%{query}%"
    rows = await db.fetch_all(
        """
        SELECT a.id, a.company_id, a.name, a.type, a.serial_number, a.status,
               a.os_name, a.last_user, a.warranty_status, a.last_sync
        FROM assets a
        WHERE (? = 1
               OR EXISTS (
                   SELECT 1 FROM user_companies uc
                   WHERE uc.company_id = a.company_id AND uc.user_id = ?
               ))
          AND (a.name LIKE ? OR a.type LIKE ? OR a.serial_number LIKE ? OR a.status LIKE ?
               OR a.os_name LIKE ? OR a.last_user LIKE ? OR a.syncro_asset_id LIKE ? OR a.tactical_asset_id LIKE ?)
        ORDER BY COALESCE(a.last_sync, a.name) DESC, a.id DESC
        LIMIT ?
        """,
        (
            1 if is_super_admin else 0,
            user_id,
            like,
            like,
            like,
            like,
            like,
            like,
            like,
            like,
            _ASSET_RESULT_LIMIT,
        ),
    )
    return [dict(row) for row in rows or []]


async def _search_feature_pack_sources(
    query: str,
    *,
    user: Mapping[str, Any],
    active_company_id: int | None,
    memberships: Sequence[Mapping[str, Any]],
    company_ids: Sequence[int],
    is_super_admin: bool,
) -> dict[str, list[dict[str, Any]]]:
    """Ask loaded feature packs for permission-aware agent search results.

    Feature packs can opt in by exposing either ``AGENT_SEARCH_PROVIDER`` or
    ``get_agent_search_provider()`` from their package module.  Providers are
    called with keyword-only context and are expected to enforce any
    feature-specific permissions before returning records.  The agent caps each
    pack's returned records defensively so a single pack cannot dominate the
    prompt.
    """

    try:
        registry = get_registry()
    except Exception as exc:  # pragma: no cover - startup/test fallback
        log_error("Agent feature pack registry unavailable", error=str(exc))
        return {}

    sources: dict[str, list[dict[str, Any]]] = {}
    for state in getattr(registry, "_states", {}).values():
        pack = getattr(state, "pack", None)
        slug = getattr(pack, "slug", "")
        if not slug:
            continue
        try:
            module = importlib.import_module(module_name_for_slug(slug))
            provider = getattr(module, "AGENT_SEARCH_PROVIDER", None)
            if provider is None:
                provider_factory = getattr(module, "get_agent_search_provider", None)
                if callable(provider_factory):
                    provider = provider_factory()
            if not callable(provider):
                continue
            result = provider(
                query=query,
                user=user,
                active_company_id=active_company_id,
                memberships=memberships,
                company_ids=company_ids,
                is_super_admin=is_super_admin,
                limit=_FEATURE_PACK_RESULT_LIMIT,
            )
            if hasattr(result, "__await__"):
                result = await result
        except Exception as exc:  # pragma: no cover - defensive guard
            log_error(
                "Agent feature pack lookup failed", feature_pack=slug, error=str(exc)
            )
            continue
        if not result:
            continue
        items = list(result if isinstance(result, list) else result.get("results", []))
        normalised: list[dict[str, Any]] = []
        for item in items[:_FEATURE_PACK_RESULT_LIMIT]:
            if not isinstance(item, Mapping):
                continue
            title = str(
                item.get("title") or item.get("name") or item.get("label") or ""
            ).strip()
            if not title:
                continue
            metadata = (
                item.get("metadata")
                if isinstance(item.get("metadata"), Mapping)
                else {}
            )
            normalised.append(
                {
                    "title": title,
                    "summary": _truncate(
                        item.get("summary")
                        or item.get("description")
                        or item.get("excerpt")
                    ),
                    "url": item.get("url"),
                    "source_type": item.get("source_type") or slug,
                    "metadata": dict(metadata),
                }
            )
        if normalised:
            sources[slug] = normalised
    return sources


async def execute_agent_query(
    query: str,
    user: Mapping[str, Any],
    *,
    active_company_id: int | None = None,
    memberships: Sequence[Mapping[str, Any]] | None = None,
) -> dict[str, Any]:
    """Execute an agent query using the configured Ollama module."""

    query_text = (query or "").strip()
    if not query_text:
        return {
            "query": "",
            "status": "error",
            "answer": None,
            "model": None,
            "event_id": None,
            "message": "Query must not be empty.",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "sources": {
                "knowledge_base": [],
                "tickets": [],
                "products": [],
                "packages": [],
                "chats": [],
                "orders": [],
                "assets": [],
                "companies": [],
                "issues": [],
                "service_status": [],
                "backup_jobs": [],
                "reports": [],
                "mailboxes": [],
                "best_practices": [],
                "feature_packs": {},
            },
            "context": {"companies": []},
        }

    resolved_memberships = _normalise_memberships(memberships)
    if not resolved_memberships:
        try:
            resolved_memberships = await company_access.list_accessible_companies(user)
        except Exception as exc:  # pragma: no cover - defensive guard
            log_error("Agent failed to load accessible companies", error=str(exc))
            resolved_memberships = []

    accessible_company_ids = _extract_company_ids(resolved_memberships)
    company_context = _company_summary(resolved_memberships)
    is_super_admin = bool(user.get("is_super_admin"))

    kb_context = await knowledge_base_service.build_access_context(user)
    try:
        kb_search = await knowledge_base_service.search_articles(
            query_text,
            kb_context,
            limit=_KB_RESULT_LIMIT,
            use_ollama=False,
        )
    except Exception as exc:  # pragma: no cover - defensive guard
        log_error("Agent knowledge base search failed", error=str(exc))
        kb_results: list[dict[str, Any]] = []
    else:
        kb_results = list(kb_search.get("results") or [])

    knowledge_base_sources: list[dict[str, Any]] = []
    for article in kb_results[:_KB_RESULT_LIMIT]:
        slug = str(article.get("slug") or "").strip()
        if not slug:
            continue
        knowledge_base_sources.append(
            {
                "slug": slug,
                "title": article.get("title") or slug.replace("-", " ").title(),
                "summary": _truncate(article.get("summary")),
                "excerpt": _truncate(article.get("excerpt")),
                "updated_at": article.get("updated_at_iso"),
                "url": f"/knowledge-base/articles/{slug}",
            }
        )

    user_id_value = user.get("id")
    ticket_sources: list[dict[str, Any]] = []
    try:
        user_id = int(user_id_value)
    except (TypeError, ValueError):
        user_id = 0
    if user_id > 0:
        try:
            tickets = await tickets_repo.list_tickets_for_user(
                user_id,
                company_ids=accessible_company_ids,
                search=query_text,
                limit=_TICKET_RESULT_LIMIT,
            )
        except Exception as exc:  # pragma: no cover - defensive guard
            log_error("Agent ticket lookup failed", error=str(exc))
            tickets = []
        for ticket in tickets[:_TICKET_RESULT_LIMIT]:
            subject = ticket.get("subject")
            if not isinstance(subject, str) or not subject.strip():
                subject = f"Ticket #{ticket.get('id')}"
            status = ticket.get("status") or "unknown"
            priority = ticket.get("priority") or "normal"
            ticket_sources.append(
                {
                    "id": ticket.get("id"),
                    "subject": subject.strip(),
                    "status": str(status).strip() or "unknown",
                    "priority": str(priority).strip() or "normal",
                    "updated_at": _utc_iso(ticket.get("updated_at")),
                    "summary": _truncate(
                        ticket.get("ai_summary") or ticket.get("description")
                    ),
                    "company_id": ticket.get("company_id"),
                }
            )

    product_sources: list[dict[str, Any]] = []
    package_sources: list[dict[str, Any]] = []
    chat_sources: list[dict[str, Any]] = []
    order_sources: list[dict[str, Any]] = []
    asset_sources: list[dict[str, Any]] = []
    company_sources: list[dict[str, Any]] = _search_company_sources(
        query_text, resolved_memberships
    )
    issue_sources: list[dict[str, Any]] = []
    service_status_sources: list[dict[str, Any]] = []
    backup_job_sources: list[dict[str, Any]] = []
    report_sources: list[dict[str, Any]] = []
    mailbox_sources: list[dict[str, Any]] = []
    best_practice_sources: list[dict[str, Any]] = []
    feature_pack_sources: dict[str, list[dict[str, Any]]] = {}
    include_products = _can_access_shop(
        resolved_memberships, is_super_admin=is_super_admin
    )
    if include_products:
        company_scope: int | None = None
        if active_company_id:
            try:
                company_scope = int(active_company_id)
            except (TypeError, ValueError):
                company_scope = None
        if company_scope is None and accessible_company_ids:
            company_scope = accessible_company_ids[0]

        filters = shop_repo.ProductFilters(
            include_archived=False,
            company_id=company_scope,
            search_term=query_text,
        )
        try:
            products = await shop_repo.list_products(filters)
        except Exception as exc:  # pragma: no cover - defensive guard
            log_error("Agent product lookup failed", error=str(exc))
            products = []
        for product in products[:_PRODUCT_RESULT_LIMIT]:
            recommendations: list[str] = []
            for related in product.get("cross_sell_products", [])[:2]:
                name = related.get("name")
                if isinstance(name, str) and name.strip():
                    recommendations.append(name.strip())
            for related in product.get("upsell_products", [])[:2]:
                if len(recommendations) >= 4:
                    break
                name = related.get("name")
                if isinstance(name, str) and name.strip():
                    recommendations.append(name.strip())
            price_value = product.get("price")
            price_display = None
            if isinstance(price_value, Decimal):
                price_display = _format_currency(price_value)
            name_value = product.get("name")
            if not isinstance(name_value, str) or not name_value.strip():
                name_value = f"Product #{product.get('id')}"
            product_sources.append(
                {
                    "id": product.get("id"),
                    "name": name_value.strip(),
                    "sku": product.get("sku"),
                    "vendor_sku": product.get("vendor_sku"),
                    "price": price_display,
                    "description": _truncate(product.get("description")),
                    "recommendations": recommendations,
                }
            )

        package_filters = shop_repo.PackageFilters(
            include_archived=False,
            search_term=query_text,
        )
        try:
            packages = await shop_repo.list_packages(package_filters)
        except Exception as exc:  # pragma: no cover - defensive guard
            log_error("Agent package lookup failed", error=str(exc))
            packages = []
        for package in packages[:_PACKAGE_RESULT_LIMIT]:
            name_value = package.get("name")
            if not isinstance(name_value, str) or not name_value.strip():
                name_value = f"Package #{package.get('id')}"
            package_sources.append(
                {
                    "id": package.get("id"),
                    "name": name_value.strip(),
                    "sku": package.get("sku"),
                    "description": _truncate(package.get("description")),
                    "product_count": package.get("product_count"),
                }
            )

    can_access_chat = _has_membership_flag(
        resolved_memberships, "can_access_chat", is_super_admin=is_super_admin
    )
    try:
        raw_chats = await _search_chat_sources(
            query_text,
            user=user,
            is_super_admin=is_super_admin,
            can_access_chat=can_access_chat,
        )
    except Exception as exc:  # pragma: no cover - defensive guard
        log_error("Agent chat lookup failed", error=str(exc))
        raw_chats = []
    for chat in raw_chats[:_CHAT_RESULT_LIMIT]:
        chat_sources.append(
            {
                "id": chat.get("id"),
                "subject": chat.get("subject") or f"Chat #{chat.get('id')}",
                "status": chat.get("status") or "unknown",
                "company_id": chat.get("company_id"),
                "updated_at": _utc_iso(
                    chat.get("updated_at") or chat.get("last_message_at")
                ),
                "linked_ticket_id": chat.get("linked_ticket_id"),
                "summary": _truncate(chat.get("matching_message")),
            }
        )

    can_access_orders = _has_membership_flag(
        resolved_memberships, "can_access_orders", is_super_admin=is_super_admin
    )
    if can_access_orders:
        try:
            raw_orders = await _search_order_sources(
                query_text, user=user, is_super_admin=is_super_admin
            )
        except Exception as exc:  # pragma: no cover - defensive guard
            log_error("Agent order lookup failed", error=str(exc))
            raw_orders = []
        for order in raw_orders[:_ORDER_RESULT_LIMIT]:
            order_sources.append(
                {
                    "order_number": order.get("order_number"),
                    "company_id": order.get("company_id"),
                    "status": order.get("status") or "unknown",
                    "shipping_status": order.get("shipping_status"),
                    "po_number": order.get("po_number"),
                    "consignment_id": order.get("consignment_id"),
                    "order_date": _utc_iso(order.get("order_date")),
                    "item_count": order.get("item_count"),
                    "summary": _truncate(order.get("notes")),
                }
            )

    can_manage_assets = _has_membership_flag(
        resolved_memberships, "can_manage_assets", is_super_admin=is_super_admin
    )
    if can_manage_assets:
        try:
            raw_assets = await _search_asset_sources(
                query_text, user=user, is_super_admin=is_super_admin
            )
        except Exception as exc:  # pragma: no cover - defensive guard
            log_error("Agent asset lookup failed", error=str(exc))
            raw_assets = []
        for asset in raw_assets[:_ASSET_RESULT_LIMIT]:
            asset_sources.append(
                {
                    "id": asset.get("id"),
                    "company_id": asset.get("company_id"),
                    "name": asset.get("name") or f"Asset #{asset.get('id')}",
                    "type": asset.get("type"),
                    "serial_number": asset.get("serial_number"),
                    "status": asset.get("status"),
                    "os_name": asset.get("os_name"),
                    "last_user": asset.get("last_user"),
                    "warranty_status": asset.get("warranty_status"),
                    "last_sync": _utc_iso(asset.get("last_sync")),
                }
            )

    try:
        issue_sources = await _search_issue_sources(
            query_text,
            memberships=resolved_memberships,
            company_ids=accessible_company_ids,
            is_super_admin=is_super_admin,
        )
    except Exception as exc:  # pragma: no cover - defensive guard
        log_error("Agent issue lookup failed", error=str(exc))
        issue_sources = []

    for label, lookup in (
        (
            "service status",
            lambda: _search_service_status_sources(
                query_text, company_ids=accessible_company_ids, is_super_admin=is_super_admin
            ),
        ),
        (
            "backup job",
            lambda: _search_backup_job_sources(
                query_text, company_ids=accessible_company_ids, is_super_admin=is_super_admin
            ),
        ),
        (
            "mailbox",
            lambda: _search_mailbox_sources(
                query_text,
                memberships=resolved_memberships,
                company_ids=accessible_company_ids,
                is_super_admin=is_super_admin,
            ),
        ),
        (
            "best practice",
            lambda: _search_best_practice_sources(
                query_text,
                memberships=resolved_memberships,
                company_ids=accessible_company_ids,
                is_super_admin=is_super_admin,
            ),
        ),
    ):
        try:
            result = await lookup()
        except Exception as exc:  # pragma: no cover - defensive guard
            log_error(f"Agent {label} lookup failed", error=str(exc))
            result = []
        if label == "service status":
            service_status_sources = result
        elif label == "backup job":
            backup_job_sources = result
        elif label == "mailbox":
            mailbox_sources = result
        elif label == "best practice":
            best_practice_sources = result

    try:
        report_sources = _search_company_report_sources(
            query_text
        ) + await _search_reporting_query_sources(
            query_text, user=user, is_super_admin=is_super_admin
        )
        report_sources = report_sources[:_SYSTEM_RESULT_LIMIT]
    except Exception as exc:  # pragma: no cover - defensive guard
        log_error("Agent report lookup failed", error=str(exc))
        report_sources = []

    feature_pack_sources = await _search_feature_pack_sources(
        query_text,
        user=user,
        active_company_id=active_company_id,
        memberships=resolved_memberships,
        company_ids=accessible_company_ids,
        is_super_admin=is_super_admin,
    )

    # Check if we have any relevant sources
    has_relevant_sources = bool(
        knowledge_base_sources
        or ticket_sources
        or product_sources
        or package_sources
        or chat_sources
        or order_sources
        or asset_sources
        or company_sources
        or issue_sources
        or service_status_sources
        or backup_job_sources
        or report_sources
        or mailbox_sources
        or best_practice_sources
        or any(feature_pack_sources.values())
    )

    context_sections: list[str] = [
        "You are the MyPortal Agent. Answer the user using only the supplied context.",
        "If the portal context does not contain information relevant to the user's question, "
        "explicitly state that you don't have that specific information available and suggest creating a support ticket.",
        "Do NOT suggest unrelated articles or products - only reference sources that directly answer the question.",
        "Never reference systems, data, or permissions outside the provided information.",
        "Use Markdown and cite sources inline with [KB:slug], [Ticket:#id], [Product:SKU], [Chat:#id], [Order:number], [Asset:#id], [Company:#id], [Issue:#id], [ServiceStatus:#id], [BackupJob:#id], [Report:key], [Mailbox:upn], or [BestPractice:check_id].",
        f"User query: {query_text}",
        "",
    ]

    if company_context:
        company_lines = ", ".join(
            f"{entry['company_name']} (#{entry['company_id']})"
            for entry in company_context
        )
        context_sections.extend(
            [
                "Companies available to the user:",
                company_lines,
                "",
            ]
        )

    if knowledge_base_sources:
        context_sections.append("Knowledge base articles:")
        for article in knowledge_base_sources:
            summary = article["summary"] or article["excerpt"] or "No summary available"
            context_sections.append(
                f"- [KB:{article['slug']}] {article['title']}: {summary}"
            )
        context_sections.append("")

    if ticket_sources:
        context_sections.append("Tickets created by or watched by the user:")
        for ticket in ticket_sources:
            summary = ticket["summary"] or "No summary available"
            context_sections.append(
                f"- [Ticket:#{ticket['id']}] {ticket['subject']} (status: {ticket['status']}, priority: {ticket['priority']}): {summary}"
            )
        context_sections.append("")

    if chat_sources:
        context_sections.append("Chats accessible to the user:")
        for chat in chat_sources:
            summary = chat["summary"] or "No matching message excerpt available"
            context_sections.append(
                f"- [Chat:#{chat['id']}] {chat['subject']} (status: {chat['status']}): {summary}"
            )
        context_sections.append("")

    if order_sources:
        context_sections.append("Orders accessible to the user:")
        for order in order_sources:
            parts = [f"[Order:{order['order_number']}] status: {order['status']}"]
            if order.get("shipping_status"):
                parts.append(f"shipping: {order['shipping_status']}")
            if order.get("po_number"):
                parts.append(f"PO: {order['po_number']}")
            if order.get("summary"):
                parts.append(order["summary"])
            context_sections.append("- " + " — ".join(parts))
        context_sections.append("")

    if asset_sources:
        context_sections.append("Assets accessible to the user:")
        for asset in asset_sources:
            parts = [f"[Asset:#{asset['id']}] {asset['name']}"]
            for key, label in (
                ("type", "type"),
                ("serial_number", "serial"),
                ("status", "status"),
                ("os_name", "OS"),
                ("last_user", "last user"),
            ):
                if asset.get(key):
                    parts.append(f"{label}: {asset[key]}")
            context_sections.append("- " + " — ".join(parts))
        context_sections.append("")

    if company_sources:
        context_sections.append("Companies accessible to the user:")
        for company in company_sources:
            parts = [f"[Company:#{company['id']}] {company['name']}"]
            if company.get("syncro_company_id"):
                parts.append(f"Syncro ID: {company['syncro_company_id']}")
            context_sections.append("- " + " — ".join(parts))
        context_sections.append("")

    if issue_sources:
        context_sections.append("Issues accessible to the user:")
        for issue in issue_sources:
            parts = [f"[Issue:#{issue['id']}] {issue['name']}"]
            if issue.get("description"):
                parts.append(issue["description"])
            assignment_labels = [
                (
                    f"{assignment.get('company_name') or assignment.get('company_id')}: "
                    f"{assignment.get('status_label') or assignment.get('status')}"
                )
                for assignment in issue.get("assignments", [])
            ]
            if assignment_labels:
                parts.append("Assignments: " + ", ".join(assignment_labels))
            context_sections.append("- " + " — ".join(parts))
        context_sections.append("")

    if service_status_sources:
        context_sections.append("Service statuses accessible to the user:")
        for service in service_status_sources:
            parts = [f"[ServiceStatus:#{service['id']}] {service['name']}"]
            if service.get("status"):
                parts.append(f"status: {service['status']}")
            if service.get("status_message"):
                parts.append(service["status_message"])
            elif service.get("description"):
                parts.append(service["description"])
            context_sections.append("- " + " — ".join(parts))
        context_sections.append("")

    if backup_job_sources:
        context_sections.append("Backup summary jobs accessible to the user:")
        for job in backup_job_sources:
            parts = [f"[BackupJob:#{job['id']}] {job['name']}"]
            if job.get("today_status"):
                parts.append(f"today: {job['today_status']}")
            if job.get("latest_status"):
                parts.append(f"latest: {job['latest_status']}")
            if job.get("description"):
                parts.append(job["description"])
            context_sections.append("- " + " — ".join(parts))
        context_sections.append("")

    if report_sources:
        context_sections.append("Reports accessible to the user:")
        for report in report_sources:
            parts = [f"[Report:{report['key']}] {report['title']}"]
            if report.get("source_type"):
                parts.append(f"type: {report['source_type']}")
            if report.get("description"):
                parts.append(report["description"])
            context_sections.append("- " + " — ".join(parts))
        context_sections.append("")

    if mailbox_sources:
        context_sections.append("Office 365 mailboxes accessible to the user:")
        for mailbox in mailbox_sources:
            parts = [f"[Mailbox:{mailbox['user_principal_name']}] {mailbox['display_name']}"]
            if mailbox.get("mailbox_type"):
                parts.append(f"type: {mailbox['mailbox_type']}")
            if mailbox.get("storage_used_bytes") is not None:
                parts.append(f"storage bytes: {mailbox['storage_used_bytes']}")
            context_sections.append("- " + " — ".join(parts))
        context_sections.append("")

    if best_practice_sources:
        context_sections.append("Microsoft 365 best practices accessible to the user:")
        for check in best_practice_sources:
            parts = [f"[BestPractice:{check['check_id']}] {check['check_name']}"]
            if check.get("status"):
                parts.append(f"status: {check['status']}")
            if check.get("details"):
                parts.append(check["details"])
            context_sections.append("- " + " — ".join(parts))
        context_sections.append("")

    if feature_pack_sources:
        context_sections.append("Feature pack results accessible to the user:")
        for slug, items in sorted(feature_pack_sources.items()):
            for item in items:
                parts = [f"[Feature:{slug}] {item['title']}"]
                if item.get("source_type"):
                    parts.append(f"type: {item['source_type']}")
                if item.get("summary"):
                    parts.append(item["summary"])
                context_sections.append("- " + " — ".join(parts))
        context_sections.append("")

    if product_sources:
        context_sections.append(
            "Products and hardware recommendations available to the user:"
        )
        for product in product_sources:
            parts = [
                (
                    f"[Product:{product['sku']}] {product['name']}"
                    if product.get("sku")
                    else product.get("name", "Product")
                ),
            ]
            if product.get("price"):
                parts.append(f"Price: {product['price']}")
            if product.get("description"):
                parts.append(product["description"])
            if product.get("recommendations"):
                recos = ", ".join(product["recommendations"])
                parts.append(f"Recommended with: {recos}")
            context_sections.append("- " + " — ".join(parts))
        context_sections.append("")

    if package_sources:
        context_sections.append(
            "Hardware bundles and service packages available to the user:"
        )
        for package in package_sources:
            parts = [
                (
                    f"[Package:{package['sku']}] {package['name']}"
                    if package.get("sku")
                    else package.get("name", "Package")
                ),
            ]
            count_value = package.get("product_count")
            try:
                count_int = int(count_value)
            except (TypeError, ValueError):
                count_int = None
            if count_int is not None and count_int >= 0:
                plural = "item" if count_int == 1 else "items"
                parts.append(f"Includes {count_int} {plural}")
            if package.get("description"):
                parts.append(package["description"])
            context_sections.append("- " + " — ".join(parts))
        context_sections.append("")

    if len(context_sections) == 8:  # only preamble, query, and blank line
        context_sections.append(
            "No portal records matched the query. "
            "Politely inform the user that you don't have specific information about their question "
            "and recommend they create a support ticket for personalized assistance."
        )

    prompt = "\n".join(context_sections)

    module_status = "skipped"
    model_name: str | None = None
    answer_text: str | None = None
    event_id: int | None = None
    message: str | None = None

    try:
        module_response = await modules_service.trigger_module(
            "ollama",
            {"prompt": prompt},
            background=False,
        )
    except ValueError as exc:
        module_status = "error"
        message = str(exc)
    except Exception as exc:  # pragma: no cover - network or module failure
        module_status = "error"
        message = "Failed to contact Ollama module"
        log_error("Agent Ollama invocation failed", error=str(exc))
    else:
        module_status = str(module_response.get("status") or "unknown")
        event_candidate = module_response.get("event_id")
        if isinstance(event_candidate, int):
            event_id = event_candidate
        payload = module_response.get("response")
        if isinstance(payload, Mapping):
            answer_text = payload.get("response") or payload.get("message")
            model_name = payload.get("model") or module_response.get("model")
        elif isinstance(module_response.get("response"), str):
            answer_text = str(module_response["response"]).strip()
            model_name = module_response.get("model")
        else:
            answer_text = module_response.get("message")
            model_name = module_response.get("model")

    return {
        "query": query_text,
        "status": module_status,
        "answer": answer_text,
        "model": model_name,
        "event_id": event_id,
        "message": message,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "has_relevant_sources": has_relevant_sources,
        "sources": {
            "knowledge_base": knowledge_base_sources,
            "tickets": ticket_sources,
            "products": product_sources,
            "packages": package_sources,
            "chats": chat_sources,
            "orders": order_sources,
            "assets": asset_sources,
            "companies": company_sources,
            "issues": issue_sources,
            "service_status": service_status_sources,
            "backup_jobs": backup_job_sources,
            "reports": report_sources,
            "mailboxes": mailbox_sources,
            "best_practices": best_practice_sources,
            "feature_packs": feature_pack_sources,
        },
        "context": {"companies": company_context},
    }
