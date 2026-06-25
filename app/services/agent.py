from __future__ import annotations

import importlib
import json
import re
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any, Mapping, Sequence

from app.core.database import db
from app.core.features import get_registry, module_name_for_slug
from app.core.config import get_settings
from app.core.logging import log_error
from app.repositories import m365_best_practices as m365_bp_repo
from app.repositories import reporting as reporting_repo
from app.repositories import shop as shop_repo
from app.repositories import staff as staff_repo
from app.repositories import staff_custom_fields as staff_custom_fields_repo
from app.repositories import tickets as tickets_repo
from app.services import company_access
from app.services import backup_jobs as backup_jobs_service
from app.services import issues as issues_service
from app.services import reports as reports_service
from app.services import knowledge_base as knowledge_base_service
from app.services import modules as modules_service
from app.services import rag_index as rag_index_service
from app.services import rag_retrieval

_KB_RESULT_LIMIT = 1000
_TICKET_RESULT_LIMIT = 1000
_PRODUCT_RESULT_LIMIT = 1000
_PACKAGE_RESULT_LIMIT = 1000
_CHAT_RESULT_LIMIT = 1000
_ORDER_RESULT_LIMIT = 1000
_ASSET_RESULT_LIMIT = 1000
_FEATURE_PACK_RESULT_LIMIT = 1000
_COMPANY_RESULT_LIMIT = 1000
_STAFF_RESULT_LIMIT = 1000
_ISSUE_RESULT_LIMIT = 1000
_SYSTEM_RESULT_LIMIT = 1000
_MAX_SNIPPET_LENGTH = 320
_LLM_SOURCE_LIMIT = 5
_LLM_RAG_CANDIDATE_LIMIT = 30
_LLM_COMPANY_CONTEXT_LIMIT = 3
_LLM_PROMPT_CHAR_LIMIT = 64000
_LLM_SNIPPET_LENGTH = 500
_EXPLICIT_TICKET_ID_RE = re.compile(
    r"(?:ticket\s*#?|#)\s*(\d{3,})|\b(\d{4,})\b", re.IGNORECASE
)


class AgentContextMode(str, Enum):
    RAG_ONLY = "rag_only"
    DISCOVERY = "discovery"
    FALLBACK = "fallback"


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


def _for_llm(
    items: Sequence[Mapping[str, Any]], limit: int = _LLM_SOURCE_LIMIT
) -> list[Mapping[str, Any]]:
    """Return the small, highest-ranked subset that is safe to send to the LLM."""

    return list(items[:limit])


def _truncate_prompt_sections(
    sections: list[str], *, limit: int = _LLM_PROMPT_CHAR_LIMIT
) -> str:
    """Join prompt sections while enforcing a hard character budget.

    Source searches can return many records for the UI, but the model should only
    receive the minimum necessary context.  If a future source type adds too much
    text, this guard truncates the prompt rather than leaking excess portal data
    or exhausting the model context window.
    """

    prompt = "\n".join(sections)
    if len(prompt) <= limit:
        return prompt
    notice = "\n\n[Context truncated to keep the LLM prompt within the configured data-minimisation budget.]"
    return prompt[: max(0, limit - len(notice))].rstrip() + notice


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


def _extract_explicit_ticket_ids(query: str) -> list[int]:
    ids: list[int] = []
    for match in _EXPLICIT_TICKET_ID_RE.findall(query or ""):
        value = next((part for part in match if part), "")
        try:
            ticket_id = int(value)
        except (TypeError, ValueError):
            continue
        if ticket_id > 0 and ticket_id not in ids:
            ids.append(ticket_id)
    return ids


def _threshold_for_source(source_type: str | None) -> float:
    thresholds = getattr(rag_retrieval, "_SOURCE_THRESHOLDS", {})
    default = 0.35
    if not source_type:
        return default
    return float(thresholds.get(str(source_type), default))


def _infer_allowed_rag_sources(query: str) -> set[str]:
    lowered = (query or "").casefold()
    tokens = set(re.findall(r"[a-z0-9]+", lowered))
    if _extract_explicit_ticket_ids(query) or {"ticket", "trello", "card"} & tokens:
        return {"tickets", "ticket_comments", "chats", "knowledge_base"}
    if {
        "product",
        "products",
        "price",
        "buy",
        "purchase",
        "compatible",
        "sku",
    } & tokens:
        return {"products", "packages", "knowledge_base"}
    if {"company", "customer", "client", "list", "show", "browse"} & tokens:
        return {"companies", "knowledge_base"}
    return {"knowledge_base", "tickets", "ticket_comments", "chats", "assets", "issues"}


def _source_allowed(source_type: str | None, allowed_sources: set[str]) -> bool:
    normalised = str(source_type or "").casefold()
    aliases = {
        "ticket": "tickets",
        "ticket_reply": "ticket_comments",
        "ticket_replies": "ticket_comments",
        "chat": "chats",
        "kb": "knowledge_base",
        "product": "products",
        "package": "packages",
        "company": "companies",
        "asset": "assets",
        "issue": "issues",
    }
    return aliases.get(normalised, normalised) in allowed_sources


def _filter_rag_candidates(
    candidates: Sequence[Mapping[str, Any]], *, allowed_sources: set[str]
) -> list[dict[str, Any]]:
    filtered: list[dict[str, Any]] = []
    for candidate in candidates:
        source_type = str(candidate.get("source_type") or "")
        if not _source_allowed(source_type, allowed_sources):
            continue
        try:
            score = float(candidate.get("score") or 0)
        except (TypeError, ValueError):
            score = 0.0
        if score < _threshold_for_source(source_type):
            continue
        item = dict(candidate)
        item["score"] = score
        item["was_selected_by_rag"] = True
        filtered.append(item)
    return filtered


def _stage(
    name: str, status: str = "complete", data: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    payload: dict[str, Any] = {"name": name, "status": status}
    if data is not None:
        payload["data"] = dict(data)
    return payload


def _summarise_rag_by_source(
    candidates: Sequence[Mapping[str, Any]],
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, int]]:
    evidence: dict[str, list[dict[str, Any]]] = {
        "tickets": [],
        "ticket_comments": [],
        "knowledge_base": [],
        "products": [],
        "assets": [],
        "orders": [],
        "issues": [],
        "chats": [],
        "best_practices": [],
    }
    duplicate_count = 0
    for candidate in candidates:
        source_type = str(candidate.get("source_type") or "unknown")
        item = {
            "label": _candidate_label(candidate),
            "source_type": source_type,
            "source_id": candidate.get("source_id"),
            "title": candidate.get("title") or candidate.get("subject"),
            "url": candidate.get("url"),
            "score": candidate.get("score"),
            "summary": _truncate(
                candidate.get("excerpt") or candidate.get("summary"),
                _LLM_SNIPPET_LENGTH,
            ),
            "duplicates": candidate.get("duplicates") or [],
            "duplicate_count": int(candidate.get("duplicate_count") or 0),
        }
        duplicate_count += item["duplicate_count"]
        evidence.setdefault(source_type, []).append(item)
    counts = {source_type: len(items) for source_type, items in evidence.items()}
    counts["duplicates_grouped"] = duplicate_count
    return evidence, counts


def _extract_module_text(
    module_response: Mapping[str, Any],
) -> tuple[str | None, str | None]:
    payload = module_response.get("response")
    if isinstance(payload, Mapping):
        return (
            payload.get("response") or payload.get("message"),
            payload.get("model") or module_response.get("model"),
        )
    if isinstance(payload, str):
        return payload.strip(), module_response.get("model")
    return module_response.get("message"), module_response.get("model")


async def _invoke_agent_llm(stage_name: str, prompt: str) -> dict[str, Any]:
    """Invoke the configured LLM module for one internal agent stage."""

    stage_prompt = f"MyPortal internal stage: {stage_name}\n\n{prompt}"
    try:
        module_response = await modules_service.trigger_module(
            "ollama",
            {
                "prompt": stage_prompt,
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "You are running a MyPortal multi-stage RAG "
                            f"pipeline step named {stage_name}."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                "stage": stage_name,
            },
            background=False,
        )
    except ValueError as exc:
        return {
            "status": "error",
            "message": str(exc),
            "text": None,
            "model": None,
            "event_id": None,
        }
    except Exception as exc:  # pragma: no cover - network or module failure
        log_error("Agent LLM stage failed", stage=stage_name, error=str(exc))
        return {
            "status": "error",
            "message": "Failed to contact Ollama module",
            "text": None,
            "model": None,
            "event_id": None,
        }

    text, model_name = _extract_module_text(module_response)
    event_candidate = module_response.get("event_id")
    return {
        "status": str(module_response.get("status") or "unknown"),
        "message": module_response.get("message"),
        "text": text,
        "model": model_name,
        "event_id": event_candidate if isinstance(event_candidate, int) else None,
    }


def _build_query_understanding_prompt(
    query_text: str, allowed_sources: set[str]
) -> str:
    return _truncate_prompt_sections(
        [
            "You are classifying a MyPortal user query for staged RAG retrieval.",
            "Return JSON only.",
            f"User query: {query_text}",
            "Return keys: intent, entities, preferred_sources, blocked_sources.",
            "Allowed source types: "
            + ", ".join(sorted(allowed_sources | {"products", "orders", "assets"})),
        ],
        limit=12000,
    )


def _build_evidence_review_prompt(
    query_text: str, candidates: Sequence[Mapping[str, Any]]
) -> str:
    compact_candidates = [
        {
            "source": _candidate_label(candidate),
            "source_type": candidate.get("source_type"),
            "title": candidate.get("title") or candidate.get("subject"),
            "excerpt": _truncate(
                candidate.get("excerpt") or candidate.get("summary"),
                _LLM_SNIPPET_LENGTH,
            ),
            "score": candidate.get("score"),
            "duplicate_count": candidate.get("duplicate_count", 0),
        }
        for candidate in candidates[:60]
    ]
    return _truncate_prompt_sections(
        [
            "You are reviewing retrieved MyPortal evidence.",
            f"User query:\n{query_text}",
            "Candidate evidence:",
            json.dumps(compact_candidates, ensure_ascii=False, default=str),
            "Classify each item as direct, supporting, duplicate, or unrelated.",
            "Keep only evidence that directly helps answer the query.",
            "Return JSON only.",
        ],
        limit=32000,
    )


def _build_category_summary_prompt(
    query_text: str, evidence: Mapping[str, Sequence[Mapping[str, Any]]]
) -> str:
    compact = {
        source_type: [
            {
                "label": item.get("label"),
                "title": item.get("title"),
                "summary": item.get("summary"),
                "duplicate_count": item.get("duplicate_count", 0),
            }
            for item in items[:12]
        ]
        for source_type, items in evidence.items()
    }
    return _truncate_prompt_sections(
        [
            "Summarise curated MyPortal evidence by source type.",
            f"User query:\n{query_text}",
            "Curated evidence:",
            json.dumps(compact, ensure_ascii=False, default=str),
            "Return concise category summaries and explicitly note zero-match categories.",
        ],
        limit=32000,
    )


def _candidate_label(candidate: Mapping[str, Any]) -> str:
    source_type = str(candidate.get("source_type") or "source")
    source_id = candidate.get("source_id") or candidate.get("document_id")
    if source_type in {"tickets", "ticket"}:
        return f"[Ticket:#{source_id}]"
    if source_type == "knowledge_base":
        return f"[KB:{source_id}]"
    if source_type in {"products", "product"}:
        return f"[Product:{source_id}]"
    if source_type in {"chats", "chat"}:
        return f"[Chat:#{source_id}]"
    return f"[RAG:{source_type}:{source_id}]"


def _build_llm_context(
    query_text: str,
    rag_evidence: Sequence[Mapping[str, Any]],
    *,
    mode: AgentContextMode = AgentContextMode.RAG_ONLY,
) -> str:
    sections = [
        "You are the MyPortal Agent. Answer the user using only the supplied context.",
        "Use only the retrieved evidence below. If the retrieved evidence does not directly answer the user query, say that no relevant information was found.",
        "Accessible portal data is not relevant context unless it was selected as retrieved evidence.",
        "Never reference systems, data, or permissions outside the provided information.",
        "Use Markdown and cite sources inline with [KB:slug], [Ticket:#id], [Product:SKU], [Chat:#id], [Order:number], [Asset:#id], [Company:#id], [Staff:#id], [Issue:#id], [ServiceStatus:#id], [BackupJob:#id], [Report:key], [Mailbox:upn], or [BestPractice:check_id].",
        f"Context mode: {mode.value}",
        f"User query: {query_text}",
        "",
        "Relevant retrieved context:",
    ]
    if not rag_evidence:
        sections.append("No relevant RAG evidence was found.")
        return _truncate_prompt_sections(sections)

    rag_context_chars = 0
    rag_context_char_budget = max(int(get_settings().rag_max_context_tokens) * 4, 48000)
    for candidate in rag_evidence[:_LLM_RAG_CANDIDATE_LIMIT]:
        label = _candidate_label(candidate)
        title = candidate.get("title") or candidate.get("subject") or label
        excerpt = (
            _truncate(
                candidate.get("excerpt") or candidate.get("summary"),
                _LLM_SNIPPET_LENGTH,
            )
            or "No excerpt available"
        )
        score = candidate.get("score")
        duplicate_count = int(candidate.get("duplicate_count") or 0)
        duplicate_text = ""
        if duplicate_count:
            duplicate_labels = [
                _candidate_label(duplicate)
                for duplicate in (candidate.get("duplicates") or [])[:5]
                if isinstance(duplicate, Mapping)
            ]
            duplicate_text = f"\n  Also found in {duplicate_count} similar results" + (
                f": {', '.join(duplicate_labels)}" if duplicate_labels else ""
            )
        item_text = (
            f"- {label} {title}\n  Relevance: curated\n  Score: {score}\n"
            f"  Excerpt: {excerpt}{duplicate_text}"
        )
        if rag_context_chars + len(item_text) > rag_context_char_budget:
            continue
        rag_context_chars += len(item_text)
        sections.append(item_text)
    return _truncate_prompt_sections(sections)


def _can_access_staff(
    memberships: Sequence[Mapping[str, Any]], *, is_super_admin: bool
) -> bool:
    if is_super_admin:
        return True
    for membership in memberships:
        try:
            staff_permission = int(membership.get("staff_permission") or 0)
        except (TypeError, ValueError):
            staff_permission = 0
        if staff_permission > 0 or bool(membership.get("can_manage_staff")):
            return True
    return False


async def _search_staff_sources(
    query: str, *, memberships: Sequence[Mapping[str, Any]], is_super_admin: bool
) -> list[dict[str, Any]]:
    if not _can_access_staff(memberships, is_super_admin=is_super_admin):
        return []

    sources: list[dict[str, Any]] = []
    for membership in memberships:
        try:
            company_id = int(membership.get("company_id"))
        except (TypeError, ValueError):
            continue
        try:
            staff_rows = await staff_repo.list_staff(company_id, page_size=500)
            staff_ids = [
                int(staff_member["id"])
                for staff_member in staff_rows
                if staff_member.get("id") is not None
            ]
            custom_field_values = (
                await staff_custom_fields_repo.get_all_staff_field_values(
                    company_id, staff_ids
                )
            )
        except Exception as exc:  # pragma: no cover - defensive guard
            log_error(
                "Agent staff lookup failed", company_id=company_id, error=str(exc)
            )
            continue
        for staff_member in staff_rows:
            if not _matches_query(
                query,
                staff_member.get("first_name"),
                staff_member.get("last_name"),
                staff_member.get("email"),
                staff_member.get("job_title"),
                staff_member.get("department"),
                staff_member.get("mobile_phone"),
                staff_member.get("org_company"),
                staff_member.get("manager_name"),
                staff_member.get("account_action"),
                staff_member.get("onboarding_status"),
                custom_field_values.get(int(staff_member.get("id") or 0), {}),
            ):
                continue
            full_name = " ".join(
                part
                for part in (
                    staff_member.get("first_name"),
                    staff_member.get("last_name"),
                )
                if str(part or "").strip()
            ).strip()
            staff_id = int(staff_member.get("id") or 0)
            custom_fields = custom_field_values.get(staff_id, {})
            sources.append(
                {
                    "id": staff_member.get("id"),
                    "company_id": staff_member.get("company_id"),
                    "name": full_name
                    or staff_member.get("email")
                    or f"Staff #{staff_member.get('id')}",
                    "email": staff_member.get("email"),
                    "job_title": staff_member.get("job_title"),
                    "department": staff_member.get("department"),
                    "mobile_phone": staff_member.get("mobile_phone"),
                    "org_company": staff_member.get("org_company"),
                    "manager_name": staff_member.get("manager_name"),
                    "account_action": staff_member.get("account_action"),
                    "custom_fields": dict(custom_fields),
                    "enabled": bool(staff_member.get("enabled")),
                    "is_ex_staff": bool(staff_member.get("is_ex_staff")),
                    "onboarding_status": staff_member.get("onboarding_status"),
                    "updated_at": staff_member.get("updated_at"),
                }
            )
            if len(sources) >= _STAFF_RESULT_LIMIT:
                return sources
    return sources


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
        SELECT
            s.id,
            s.name,
            s.description,
            s.status,
            s.status_message,
            s.updated_at,
            sc.company_id
        FROM service_status_services s
        LEFT JOIN service_status_service_companies sc ON sc.service_id = s.id
        WHERE s.is_active = 1
          AND (s.name LIKE ? OR s.description LIKE ? OR s.status LIKE ? OR s.status_message LIKE ?)
        ORDER BY s.display_order ASC, s.name ASC
        """,
        (
            f"%{query}%",
            f"%{query}%",
            f"%{query}%",
            f"%{query}%",
        ),
    )

    allowed_company_ids = set(company_ids)
    services: dict[int, dict[str, Any]] = {}
    service_company_ids: dict[int, set[int]] = {}
    for row in rows or []:
        service_id = row.get("id")
        if service_id is None:
            continue
        services.setdefault(service_id, row)
        company_id = row.get("company_id")
        if company_id is not None:
            service_company_ids.setdefault(service_id, set()).add(company_id)

    sources: list[dict[str, Any]] = []
    for service_id, row in services.items():
        restricted_company_ids = service_company_ids.get(service_id, set())
        is_restricted = bool(restricted_company_ids)
        is_allowed = bool(restricted_company_ids & allowed_company_ids)
        if not is_super_admin and is_restricted and not is_allowed:
            continue
        sources.append(
            {
                "id": row.get("id"),
                "name": row.get("name") or f"Service #{row.get('id')}",
                "description": _truncate(row.get("description")),
                "status": row.get("status"),
                "status_message": _truncate(row.get("status_message")),
            }
        )
        if len(sources) >= _SYSTEM_RESULT_LIMIT:
            break
    return sources


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
    query: str,
    *,
    memberships: Sequence[Mapping[str, Any]],
    company_ids: Sequence[int],
    is_super_admin: bool,
) -> list[dict[str, Any]]:
    can_user = _has_membership_flag(
        memberships, "can_view_m365_user_mailboxes", is_super_admin=is_super_admin
    )
    can_shared = _has_membership_flag(
        memberships, "can_view_m365_shared_mailboxes", is_super_admin=is_super_admin
    )
    if (not can_user and not can_shared) or not company_ids:
        return []
    include_user_mailboxes = 1 if can_user else 0
    include_shared_mailboxes = 1 if can_shared else 0
    rows = await db.fetch_all(
        """
        SELECT company_id, user_principal_name, display_name, mailbox_type, storage_used_bytes
        FROM m365_mailboxes
        WHERE ((? = 1 AND mailbox_type = ?) OR (? = 1 AND mailbox_type = ?))
          AND (user_principal_name LIKE ? OR display_name LIKE ? OR mailbox_type LIKE ?)
        ORDER BY display_name ASC
        """,
        (
            include_user_mailboxes,
            "UserMailbox",
            include_shared_mailboxes,
            "SharedMailbox",
            f"%{query}%",
            f"%{query}%",
            f"%{query}%",
        ),
    )
    allowed_company_ids = set(company_ids)
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
        and row.get("company_id") in allowed_company_ids
    ][:_SYSTEM_RESULT_LIMIT]


async def _search_best_practice_sources(
    query: str,
    *,
    memberships: Sequence[Mapping[str, Any]],
    company_ids: Sequence[int],
    is_super_admin: bool,
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
    allow_empty_query: bool = False,
    context_mode: AgentContextMode = AgentContextMode.RAG_ONLY,
) -> dict[str, Any]:
    """Execute an agent query using the configured Ollama module."""

    query_text = (query or "").strip()
    if not isinstance(context_mode, AgentContextMode):
        context_mode = AgentContextMode(str(context_mode))
    if not query_text and not allow_empty_query:
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
                "staff": [],
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
    explicit_ticket_ids = _extract_explicit_ticket_ids(query_text)
    allowed_rag_sources = _infer_allowed_rag_sources(query_text)
    stages: list[dict[str, Any]] = [
        _stage(
            "query_understanding",
            data={
                "intent": "mixed",
                "ticket_ids": explicit_ticket_ids,
                "preferred_sources": sorted(allowed_rag_sources),
            },
        )
    ]
    query_understanding_llm = await _invoke_agent_llm(
        "query_understanding",
        _build_query_understanding_prompt(query_text, allowed_rag_sources),
    )
    stages[0]["data"].update(
        {
            "llm_status": query_understanding_llm["status"],
            "event_id": query_understanding_llm["event_id"],
        }
    )

    kb_context = await knowledge_base_service.build_access_context(user)
    try:
        if allow_empty_query and not query_text:
            kb_results = await knowledge_base_service.list_accessible_search_articles(
                kb_context
            )
        else:
            kb_search = await knowledge_base_service.search_articles(
                query_text,
                kb_context,
                limit=_KB_RESULT_LIMIT,
                use_ollama=False,
            )
            kb_results = list(kb_search.get("results") or [])
    except Exception as exc:  # pragma: no cover - defensive guard
        log_error("Agent knowledge base search failed", error=str(exc))
        kb_results = []

    knowledge_base_sources: list[dict[str, Any]] = []
    for article in (
        kb_results
        if allow_empty_query and not query_text
        else kb_results[:_KB_RESULT_LIMIT]
    ):
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
    direct_ticket_evidence: list[dict[str, Any]] = []
    if user_id > 0:
        for explicit_ticket_id in explicit_ticket_ids[:3]:
            try:
                ticket = await tickets_repo.get_ticket(explicit_ticket_id)
                if not ticket:
                    continue
                ticket_company_id = ticket.get("company_id")
                requester_id = ticket.get("requester_id")
                can_access_ticket = is_super_admin or requester_id == user_id
                try:
                    can_access_ticket = (
                        can_access_ticket
                        or int(ticket_company_id or 0) in accessible_company_ids
                    )
                except (TypeError, ValueError):
                    pass
                if not can_access_ticket:
                    can_access_ticket = await tickets_repo.is_ticket_watcher(
                        explicit_ticket_id, user_id
                    )
                if not can_access_ticket:
                    continue
                replies = await tickets_repo.list_replies(
                    explicit_ticket_id, include_internal=is_super_admin
                )
            except Exception as exc:  # pragma: no cover - defensive guard
                log_error(
                    "Agent direct ticket lookup failed",
                    ticket_id=explicit_ticket_id,
                    error=str(exc),
                )
                continue
            subject = ticket.get("subject") or f"Ticket #{explicit_ticket_id}"
            summary_parts = [
                ticket.get("ai_summary") or ticket.get("description") or ""
            ]
            for reply in replies[:3]:
                if reply.get("body"):
                    summary_parts.append(str(reply.get("body")))
            direct_ticket_evidence.append(
                {
                    "source_type": "tickets",
                    "source_id": explicit_ticket_id,
                    "document_id": explicit_ticket_id,
                    "title": str(subject).strip(),
                    "excerpt": _truncate(
                        " ".join(part for part in summary_parts if part),
                        _MAX_SNIPPET_LENGTH,
                    ),
                    "score": 1.0,
                    "was_selected_by_rag": True,
                }
            )
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
    staff_sources: list[dict[str, Any]] = []
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
        staff_sources = await _search_staff_sources(
            query_text, memberships=resolved_memberships, is_super_admin=is_super_admin
        )
    except Exception as exc:  # pragma: no cover - defensive guard
        log_error("Agent staff lookup failed", error=str(exc))
        staff_sources = []

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
                query_text,
                company_ids=accessible_company_ids,
                is_super_admin=is_super_admin,
            ),
        ),
        (
            "backup job",
            lambda: _search_backup_job_sources(
                query_text,
                company_ids=accessible_company_ids,
                is_super_admin=is_super_admin,
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

    assembled_sources = {
        "knowledge_base": knowledge_base_sources,
        "tickets": ticket_sources,
        "products": product_sources,
        "packages": package_sources,
        "chats": chat_sources,
        "orders": order_sources,
        "assets": asset_sources,
        "companies": company_sources,
        "staff": staff_sources,
        "issues": issue_sources,
        "service_status": service_status_sources,
        "backup_jobs": backup_job_sources,
        "reports": report_sources,
        "mailboxes": mailbox_sources,
        "best_practices": best_practice_sources,
        "feature_packs": feature_pack_sources,
    }
    stages.append(
        _stage(
            "retrieval",
            data={
                "knowledge_base": len(knowledge_base_sources),
                "tickets": len(ticket_sources),
                "products": len(product_sources),
                "chats": len(chat_sources),
                "orders": len(order_sources),
                "assets": len(asset_sources),
                "issues": len(issue_sources),
                "best_practices": len(best_practice_sources),
            },
        )
    )
    try:
        await rag_index_service.index_agent_sources(assembled_sources)
        raw_rag_candidates = await rag_retrieval.retrieve_candidates(
            query_text,
            user,
            active_company_id=active_company_id,
            memberships=resolved_memberships,
            source_filters=sorted(allowed_rag_sources),
        )
    except Exception as exc:  # pragma: no cover - defensive guard
        log_error("Agent RAG retrieval failed", error=str(exc))
        raw_rag_candidates = []

    rag_candidates = direct_ticket_evidence + _filter_rag_candidates(
        raw_rag_candidates, allowed_sources=allowed_rag_sources
    )
    curated_evidence, evidence_counts = _summarise_rag_by_source(rag_candidates)
    stages.append(
        _stage(
            "deduplication",
            data={"duplicates_grouped": evidence_counts.get("duplicates_grouped", 0)},
        )
    )
    evidence_review_llm = await _invoke_agent_llm(
        "evidence_review",
        _build_evidence_review_prompt(query_text, rag_candidates),
    )
    stages.append(
        _stage(
            "evidence_review",
            data={
                "candidates_reviewed": len(raw_rag_candidates)
                + len(direct_ticket_evidence),
                "curated_evidence": len(rag_candidates),
                "removed_unrelated": max(
                    0,
                    len(raw_rag_candidates)
                    + len(direct_ticket_evidence)
                    - len(rag_candidates),
                ),
                "llm_status": evidence_review_llm["status"],
                "event_id": evidence_review_llm["event_id"],
            },
        )
    )
    category_summary_llm = await _invoke_agent_llm(
        "category_summaries",
        _build_category_summary_prompt(query_text, curated_evidence),
    )
    stages.append(
        _stage(
            "category_summaries",
            data={
                **{
                    source_type: count
                    for source_type, count in evidence_counts.items()
                    if source_type != "duplicates_grouped"
                },
                "summary": category_summary_llm["text"],
                "llm_status": category_summary_llm["status"],
                "event_id": category_summary_llm["event_id"],
            },
        )
    )

    # Check if we have any relevant RAG evidence for the prompt.
    has_relevant_sources = bool(rag_candidates)

    if context_mode is AgentContextMode.RAG_ONLY:
        prompt = _build_llm_context(
            query_text, rag_candidates, mode=AgentContextMode.RAG_ONLY
        )
    else:
        # Discovery/FALLBACK modes are opt-in compatibility paths. They still use
        # hard-gated RAG evidence first, but may be expanded by future callers that
        # explicitly request browsing/listing accessible records.
        prompt = _build_llm_context(query_text, rag_candidates, mode=context_mode)

    module_status = "skipped"
    model_name: str | None = None
    answer_text: str | None = None
    event_id: int | None = None
    message: str | None = None

    final_llm = await _invoke_agent_llm("final_answer", prompt)
    module_status = final_llm["status"]
    message = final_llm["message"]
    answer_text = final_llm["text"]
    model_name = final_llm["model"]
    event_id = final_llm["event_id"]
    stages.append(
        _stage("final_answer", status="complete" if answer_text else module_status)
    )

    return {
        "query": query_text,
        "status": module_status,
        "answer": answer_text,
        "model": model_name,
        "event_id": event_id,
        "message": message,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "has_relevant_sources": has_relevant_sources,
        "stages": stages,
        "evidence": curated_evidence,
        "sources": {
            "knowledge_base": knowledge_base_sources,
            "tickets": ticket_sources,
            "products": product_sources,
            "packages": package_sources,
            "chats": chat_sources,
            "orders": order_sources,
            "assets": asset_sources,
            "companies": company_sources,
            "staff": staff_sources,
            "issues": issue_sources,
            "service_status": service_status_sources,
            "backup_jobs": backup_job_sources,
            "reports": report_sources,
            "mailboxes": mailbox_sources,
            "best_practices": best_practice_sources,
            "feature_packs": feature_pack_sources,
        },
        "context": {"companies": company_context, "rag_candidates": rag_candidates},
    }
