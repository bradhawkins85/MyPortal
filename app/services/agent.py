from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Mapping, Sequence

from app.core.logging import log_error
from app.repositories import shop as shop_repo
from app.repositories import tickets as tickets_repo
from app.services import company_access
from app.services import knowledge_base as knowledge_base_service
from app.services import modules as modules_service

_KB_RESULT_LIMIT = 5
_TICKET_RESULT_LIMIT = 5
_PRODUCT_RESULT_LIMIT = 5
_MAX_SNIPPET_LENGTH = 320


def _utc_iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
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


def _can_access_shop(memberships: Sequence[Mapping[str, Any]], *, is_super_admin: bool) -> bool:
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
                "company_name": membership.get("company_name") or f"Company #{company_id}",
            }
        )
    return summary


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
            "sources": {"knowledge_base": [], "tickets": [], "products": []},
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
                    "summary": _truncate(ticket.get("ai_summary") or ticket.get("description")),
                    "company_id": ticket.get("company_id"),
                }
            )

    product_sources: list[dict[str, Any]] = []
    include_products = _can_access_shop(resolved_memberships, is_super_admin=is_super_admin)
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

    context_sections: list[str] = [
        "You are the MyPortal Agent. Answer the user using only the supplied context.",
        "If the portal context does not contain the answer, say so and recommend contacting support.",
        "Never reference systems, data, or permissions outside the provided information.",
        "Use Markdown and cite sources inline with [KB:slug], [Ticket:#id], or [Product:SKU].",
        f"User query: {query_text}",
        "",
    ]

    if company_context:
        company_lines = ", ".join(
            f"{entry['company_name']} (#{entry['company_id']})" for entry in company_context
        )
        context_sections.extend([
            "Companies available to the user:",
            company_lines,
            "",
        ])

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

    if product_sources:
        context_sections.append("Products and hardware recommendations available to the user:")
        for product in product_sources:
            parts = [
                f"[Product:{product['sku']}] {product['name']}" if product.get("sku") else product.get("name", "Product"),
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

    if len(context_sections) == 6:  # only preamble, query, and blank line
        context_sections.append("No portal records matched the query. Explain the absence to the user.")

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
        "sources": {
            "knowledge_base": knowledge_base_sources,
            "tickets": ticket_sources,
            "products": product_sources,
        },
        "context": {"companies": company_context},
    }
