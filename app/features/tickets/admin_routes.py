"""Admin ticket routes for the ``tickets`` feature pack.

Mirrors the routes that used to live in ``app/main.py``:

* ``GET  /admin/tickets``
* ``GET  /admin/tickets/{ticket_id}``
* ``POST /admin/tickets``
* ``POST /admin/tickets/{ticket_id}/status``
* ``POST /admin/tickets/statuses``
* ``POST /admin/tickets/labour-types``
* ``POST /admin/tickets/{ticket_id}/description``
* ``POST /admin/tickets/{ticket_id}/description/replace``
* ``POST /admin/tickets/{ticket_id}/details``
* ``POST /admin/tickets/{ticket_id}/ai/reprocess``
* ``POST /admin/tickets/{ticket_id}/delete``
* ``POST /admin/tickets/bulk-delete``
* ``POST /admin/tickets/{ticket_id}/replies``
"""

from __future__ import annotations

import asyncio
import re
from collections.abc import Sequence
from typing import Any, Mapping
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from app.core.logging import log_error, log_info
from app.features.tickets.form_helpers import get_last_form_value
from app.security.flash import flash_redirect
from app.repositories import assets as assets_repo
from app.repositories import companies as company_repo
from app.repositories import company_memberships as membership_repo
from app.repositories import staff as staff_repo
from app.repositories import ticket_statuses as ticket_status_repo
from app.repositories import ticket_attachments as attachments_repo
from app.repositories import tickets as tickets_repo
from app.repositories import users as user_repo
from app.services import agent as agent_service
from app.services import labour_types as labour_types_service
from app.services import ticket_attachments as attachments_service
from app.services import rag_retrieval
from app.services import tickets as tickets_service
from app.services import tray as tray_service
from app.services.sanitization import sanitize_rich_text


router = APIRouter(tags=["Tickets"])

_RELATED_STOP_WORDS = {
    "about", "after", "again", "also", "and", "are", "attachment", "attachments",
    "before", "below", "can", "cannot", "client", "customer", "description",
    "does", "error", "from", "have", "helpdesk", "internal", "issue", "known",
    "message", "messages", "need", "needs", "please", "public", "reply", "replace",
    "replacement", "request", "service", "setup", "subject", "support", "that",
    "the", "their", "there", "this", "ticket", "with", "without", "would", "you",
    "your",
}



def _main():
    from app import main as main_module

    return main_module





def _strip_external_links(text: str) -> str:
    return re.sub(r"https?://\S+|www\.\S+", " ", text or "")


def _compact_ticket_text(value: Any, *, limit: int = 700) -> str:
    sanitized = sanitize_rich_text(_strip_external_links(str(value or "")))
    text = re.sub(r"\s+", " ", sanitized.text_content).strip()
    return text[:limit]


def _ticket_related_text_parts(
    ticket: dict[str, Any],
    replies: Sequence[dict[str, Any]],
    attachments: Sequence[dict[str, Any]],
) -> list[str]:
    parts = [
        _compact_ticket_text(ticket.get("subject"), limit=220),
        _compact_ticket_text(ticket.get("description"), limit=900),
        _compact_ticket_text(ticket.get("category"), limit=120),
    ]
    parts.extend(_compact_ticket_text(reply.get("body"), limit=600) for reply in replies[-8:])
    parts.extend(
        _compact_ticket_text(attachment.get("original_filename") or attachment.get("filename"), limit=160)
        for attachment in attachments[:12]
    )
    return [part for part in parts if part]


def _related_search_terms(text_parts: Sequence[str], *, limit: int = 30) -> list[str]:
    terms: list[str] = []
    seen: set[str] = set()
    for text in text_parts:
        for token in re.findall(r"[a-z0-9][a-z0-9_.:-]{2,}", text.lower()):
            token = token.strip("._:-")
            if len(token) < 3 or token in _RELATED_STOP_WORDS or token in seen:
                continue
            seen.add(token)
            terms.append(token)
            if len(terms) >= limit:
                return terms
    return terms


def _build_related_ticket_query(
    ticket: dict[str, Any],
    replies: Sequence[dict[str, Any]],
    attachments: Sequence[dict[str, Any]],
) -> str:
    text_parts = _ticket_related_text_parts(ticket, replies, attachments)
    terms = _related_search_terms(text_parts)
    if terms:
        return " ".join(terms)[:2000]
    return " ".join(text_parts).strip()[:2000]


def _safe_related_url(url: str | None) -> str | None:
    candidate = str(url or "").strip()
    if not candidate:
        return None
    parsed = urlsplit(candidate)
    if parsed.scheme or parsed.netloc:
        return None
    if not candidate.startswith("/") or candidate.startswith("//"):
        return None
    return candidate


def _source_url(source_type: str, item: dict[str, Any]) -> str | None:
    supplied_url = _safe_related_url(item.get("url"))
    if supplied_url:
        return supplied_url
    identifier = item.get("id")
    if source_type == "tickets" and identifier:
        return f"/admin/tickets/{identifier}"
    if source_type == "assets" and identifier:
        return f"/admin/assets/{identifier}"
    if source_type == "companies" and identifier:
        return f"/admin/companies/{identifier}"
    if source_type == "staff" and identifier:
        return f"/admin/staff/{identifier}"
    if source_type == "orders" and item.get("order_number"):
        return f"/admin/orders/{item['order_number']}"
    if source_type == "chats" and identifier:
        return f"/chat/{identifier}"
    if source_type == "issues" and identifier:
        return f"/admin/issues/{identifier}"
    return None


def _source_relevance_score(item: dict[str, Any], search_terms: set[str]) -> int:
    if not search_terms:
        return 0
    source_text = " ".join(
        str(item.get(key) or "")
        for key in (
            "title", "subject", "name", "summary", "excerpt", "description",
            "serial_number", "os_name", "status", "order_number", "key",
        )
    ).lower()
    return sum(1 for term in search_terms if term in source_text)


def _related_items_from_agent_sources(
    sources: dict[str, Any],
    current_ticket_id: int,
    *,
    search_terms: Sequence[str] = (),
) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    meaningful_terms = {term for term in search_terms if len(term) >= 4}

    for source_type, values in sources.items():
        if source_type == "feature_packs" and isinstance(values, dict):
            iterable = [(f"feature:{slug}", item) for slug, records in values.items() for item in (records or [])]
        else:
            iterable = [(source_type, item) for item in (values or [])]
        for item_type, raw_item in iterable:
            if not isinstance(raw_item, dict):
                continue
            if item_type == "tickets":
                try:
                    if int(raw_item.get("id")) == current_ticket_id:
                        continue
                except (TypeError, ValueError):
                    pass
            if meaningful_terms and _source_relevance_score(raw_item, meaningful_terms) == 0:
                continue
            url = _source_url(item_type, raw_item)
            if not url:
                continue
            label = (
                raw_item.get("title") or raw_item.get("subject") or raw_item.get("name")
                or raw_item.get("order_number") or raw_item.get("key") or f"{item_type.title()} {raw_item.get('id', '')}"
            )
            items.append({"type": item_type, "label": str(label).strip()[:180], "url": url})
            if len(items) >= 12:
                return items
    return items


async def _retrieve_related_rag_candidates(
    query: str,
    current_user: Mapping[str, Any],
    *,
    active_company_id: int | None,
    memberships: Sequence[Mapping[str, Any]] | None,
    ticket_id: int,
) -> list[dict[str, Any]]:
    try:
        return await rag_retrieval.retrieve_candidates(
            query,
            current_user,
            active_company_id=active_company_id,
            memberships=memberships,
            limit=12,
            min_score=0.08,
        )
    except Exception as exc:  # pragma: no cover - defensive guard
        log_error(
            "Ticket related RAG retrieval failed",
            ticket_id=ticket_id,
            error=str(exc),
        )
        return []

def _parse_requester_value(raw: Any) -> tuple[str | None, int | None]:
    value = str(raw or "").strip()
    if not value:
        return None, None
    if ":" in value:
        prefix, identifier = value.split(":", 1)
        prefix = prefix.strip().lower()
    else:
        prefix, identifier = "user", value
    if prefix not in {"user", "staff"}:
        raise ValueError("Unsupported requester selector value.")
    numeric_id = int(identifier)
    if numeric_id <= 0:
        raise ValueError("Requester ID must be positive.")
    return prefix, numeric_id

def _safe_local_redirect_target(raw: str | None, *, fallback: str) -> str:
    candidate = (raw or "").strip()
    if not candidate:
        return fallback
    parsed = urlsplit(candidate)
    if parsed.scheme or parsed.netloc:
        return fallback
    if not candidate.startswith("/") or candidate.startswith("//"):
        return fallback
    return candidate


@router.get("/admin/tickets", response_class=HTMLResponse)
async def admin_tickets_page(
    request: Request,
    phoneNumber: str | None = Query(default=None),
):
    main_module = _main()
    current_user, redirect = await main_module._require_helpdesk_page(request)
    if redirect:
        return redirect
    return await main_module._render_tickets_dashboard(
        request,
        current_user,
        phone_number=phoneNumber,
    )


@router.get("/admin/tickets/{ticket_id:int}", response_class=HTMLResponse)
async def admin_ticket_detail(
    ticket_id: int,
    request: Request,
):
    main_module = _main()
    current_user, redirect = await main_module._require_helpdesk_page(request)
    if redirect:
        return redirect
    return await main_module._render_ticket_detail(
        request,
        current_user,
        ticket_id=ticket_id,
    )



@router.post("/admin/tickets/{ticket_id:int}/related/rescan", response_class=JSONResponse)
async def admin_rescan_ticket_related(ticket_id: int, request: Request):
    main_module = _main()
    current_user, redirect = await main_module._require_helpdesk_page(request)
    if redirect:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not permitted")
    ticket = await tickets_repo.get_ticket(ticket_id)
    if not ticket:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ticket not found")

    replies = await tickets_repo.list_replies(ticket_id, include_internal=True)
    attachments = await attachments_repo.list_attachments(ticket_id)
    query = _build_related_ticket_query(ticket, replies, attachments)
    search_terms = _related_search_terms(_ticket_related_text_parts(ticket, replies, attachments))
    if not query:
        return JSONResponse({
            "items": [],
            "scanned": True,
            "skipped": False,
            "generated_at": None,
        })
    active_company_id = getattr(request.state, "active_company_id", None)
    available_companies = getattr(request.state, "available_companies", None)
    result, rag_candidates = await asyncio.gather(
        agent_service.execute_agent_query(
            query,
            current_user,
            active_company_id=active_company_id,
            memberships=available_companies,
        ),
        _retrieve_related_rag_candidates(
            query,
            current_user,
            active_company_id=active_company_id,
            memberships=available_companies,
            ticket_id=ticket_id,
        ),
    )

    items: list[dict[str, str]] = []
    for candidate in rag_candidates:
        source_type = str(candidate.get("source_type") or "")
        source_id = candidate.get("source_id")
        if source_type == "tickets":
            try:
                if int(source_id) == ticket_id:
                    continue
            except (TypeError, ValueError):
                pass
        url = _safe_related_url(candidate.get("url"))
        if not url:
            url = _source_url(source_type, {"id": source_id, "url": candidate.get("url")})
        if not url:
            continue
        label = str(candidate.get("title") or f"{source_type.title()} {source_id}").strip()[:180]
        items.append({
            "type": source_type,
            "label": label,
            "url": url,
            "score": str(candidate.get("score") or ""),
        })
        if len(items) >= 12:
            break

    if not items:
        items = _related_items_from_agent_sources(
            dict(result.get("sources") or {}),
            ticket_id,
            search_terms=search_terms,
        )
    return JSONResponse({
        "items": items,
        "scanned": True,
        "skipped": False,
        "generated_at": result.get("generated_at"),
    })

@router.post("/admin/tickets", response_class=HTMLResponse)
async def admin_create_ticket(request: Request):
    main_module = _main()
    current_user, redirect = await main_module._require_helpdesk_page(request)
    if redirect:
        return redirect
    form = await request.form()
    subject = str(form.get("subject", "")).strip()
    description = (str(form.get("description", "")).strip() or None)
    priority = (str(form.get("priority", "")).strip() or "normal")
    module_slug = (str(form.get("moduleSlug", "")).strip() or None)
    status_raw = str(form.get("status", "")).strip()
    company_raw = form.get("companyId")
    assigned_raw = form.get("assignedUserId")
    requester_raw = form.get("requesterId")
    try:
        company_id = int(company_raw) if company_raw else None
    except (TypeError, ValueError):
        company_id = None
    try:
        assigned_user_id = int(assigned_raw) if assigned_raw else None
    except (TypeError, ValueError):
        assigned_user_id = None
    requester_id: int | None = None
    requester_staff_id: int | None = None
    if requester_raw:
        try:
            requester_kind, requester_identifier = _parse_requester_value(requester_raw)
        except (TypeError, ValueError):
            return await main_module._render_tickets_dashboard(
                request,
                current_user,
                error_message="Invalid requester selection.",
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        if requester_kind == "staff":
            requester_staff_id = requester_identifier
        elif requester_kind == "user":
            requester_id = requester_identifier
    else:
        requester_id = current_user.get("id")

    if requester_staff_id and company_id is None:
        return await main_module._render_tickets_dashboard(
            request,
            current_user,
            error_message="Link the ticket to a company before selecting a requester.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    if requester_staff_id and company_id:
        staff_member = await staff_repo.get_enabled_staff_requester(company_id, requester_staff_id)
        if not staff_member:
            return await main_module._render_tickets_dashboard(
                request,
                current_user,
                error_message="The selected requester is not an enabled staff member for the selected company.",
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        requester_id = staff_member.get("user_id")
    elif requester_id and company_id and requester_id != current_user.get("id"):
        allowed_requesters = await staff_repo.list_enabled_staff_users(company_id)
        matched = next((option for option in allowed_requesters if option.get("user_id") == requester_id), None)
        if not matched:
            return await main_module._render_tickets_dashboard(
                request,
                current_user,
                error_message="The selected requester is not an enabled staff member for the selected company.",
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        requester_staff_id = matched.get("staff_id")

    if not subject:
        return await main_module._render_tickets_dashboard(
            request,
            current_user,
            error_message="Enter a ticket subject.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    try:
        if status_raw:
            status_value = await tickets_service.validate_status_choice(status_raw)
        else:
            status_value = await tickets_service.resolve_status_or_default(None)
        created = await tickets_service.create_ticket(
            subject=subject,
            description=description,
            requester_id=requester_id,
            requester_staff_id=requester_staff_id,
            company_id=company_id,
            assigned_user_id=assigned_user_id,
            priority=priority,
            status=status_value,
            category=str(form.get("category", "")).strip() or None,
            module_slug=module_slug,
            external_reference=str(form.get("externalReference", "")).strip() or None,
            trigger_automations=True,
            initial_reply_author_id=current_user.get("id"),
        )
        await tickets_repo.add_watcher(created["id"], current_user.get("id"))
        await tickets_service.refresh_ticket_ai_summary(created["id"])
        await tickets_service.refresh_ticket_ai_tags(created["id"])
    except Exception as exc:  # pragma: no cover - defensive logging
        log_error("Failed to create ticket", error=str(exc))
        if isinstance(exc, ValueError):
            error_detail = str(exc)
            status_code_value = status.HTTP_400_BAD_REQUEST
        else:
            log_error("Failed to create ticket", error=str(exc))
            error_detail = "Unable to create ticket. Please try again."
            status_code_value = status.HTTP_500_INTERNAL_SERVER_ERROR
        return await main_module._render_tickets_dashboard(
            request,
            current_user,
            error_message=error_detail,
            status_code=status_code_value,
        )
    return flash_redirect("/admin/tickets", "Ticket created.", "success")


@router.post("/admin/tickets/{ticket_id:int}/status", response_class=HTMLResponse)
async def admin_update_ticket_status(ticket_id: int, request: Request):
    main_module = _main()
    current_user, redirect = await main_module._require_helpdesk_page(request)
    if redirect:
        return redirect
    form = await request.form()
    status_raw = str(form.get("status", "")).strip()
    return_url_raw = form.get("returnUrl")
    return_url = str(return_url_raw).strip() if isinstance(return_url_raw, str) else None
    try:
        status_value = await tickets_service.validate_status_choice(status_raw)
    except ValueError as exc:
        error_message = str(exc)
        if return_url and return_url.startswith(f"/admin/tickets/{ticket_id}"):
            return await main_module._render_ticket_detail(
                request,
                current_user,
                ticket_id=ticket_id,
                error_message=error_message,
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        return await main_module._render_tickets_dashboard(
            request,
            current_user,
            error_message=error_message,
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    ticket = await tickets_repo.get_ticket(ticket_id)
    if not ticket:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ticket not found")
    await tickets_repo.set_ticket_status(ticket_id, status_value)
    await tickets_service.refresh_ticket_ai_summary(ticket_id)
    await tickets_service.refresh_ticket_ai_tags(ticket_id)
    await tickets_service.broadcast_ticket_event(action="updated", ticket_id=ticket_id)
    await tickets_service.emit_ticket_updated_event(
        ticket_id,
        actor_type="technician",
        actor=current_user,
    )
    message = f"Ticket {ticket_id} updated."
    destination = "/admin/tickets"
    safe_return_url = _safe_local_redirect_target(return_url, fallback="")
    if safe_return_url:
        destination = safe_return_url
    return flash_redirect(destination, message, "success")


def _build_ticket_status_payloads(
    tech_labels: Sequence[str],
    public_labels: Sequence[str],
    existing_slugs: Sequence[str],
    default_status_value: str,
) -> list[dict[str, Any]]:
    statuses: list[dict[str, Any]] = []
    max_length = (
        max(len(tech_labels), len(public_labels), len(existing_slugs))
        if (tech_labels or public_labels or existing_slugs)
        else 0
    )
    for index in range(max_length):
        tech_label = tech_labels[index] if index < len(tech_labels) else ""
        public_status = public_labels[index] if index < len(public_labels) else ""
        existing_slug = existing_slugs[index] if index < len(existing_slugs) else None
        if not tech_label and not public_status:
            continue
        candidate_slug = ticket_status_repo.slugify_status_label(tech_label)
        existing_slug_normalized = ticket_status_repo.slugify_status_label(str(existing_slug or ""))
        is_default = False
        if default_status_value:
            if existing_slug_normalized and existing_slug_normalized == default_status_value:
                is_default = True
            elif candidate_slug and candidate_slug == default_status_value:
                is_default = True
        statuses.append(
            {
                "techLabel": tech_label,
                "publicStatus": public_status,
                "existingSlug": existing_slug,
                "isDefault": is_default,
            }
        )
    return statuses


@router.post("/admin/tickets/statuses", response_class=HTMLResponse)
async def admin_replace_ticket_statuses(request: Request):
    main_module = _main()
    current_user, redirect = await main_module._require_super_admin_page(request)
    if redirect:
        return redirect

    form = await request.form()
    tech_labels = form.getlist("techLabel")
    public_labels = form.getlist("publicLabel")
    existing_slugs = form.getlist("existingSlug")
    default_status_value = ticket_status_repo.slugify_status_label(str(form.get("defaultStatus") or ""))

    statuses = _build_ticket_status_payloads(
        tech_labels,
        public_labels,
        existing_slugs,
        default_status_value,
    )

    try:
        await tickets_service.replace_ticket_statuses(statuses)
    except ValueError as exc:
        return await main_module._render_tickets_dashboard(
            request,
            current_user,
            error_message=str(exc),
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    return flash_redirect("/admin/tickets", "Ticket statuses updated.", "success")


@router.post("/admin/tickets/labour-types", response_class=HTMLResponse)
async def admin_replace_labour_types(request: Request):
    main_module = _main()
    current_user, redirect = await main_module._require_super_admin_page(request)
    if redirect:
        return redirect

    form = await request.form()
    ids = form.getlist("labourId")
    codes = form.getlist("labourCode")
    names = form.getlist("labourName")
    rates = form.getlist("labourRate")
    default_id = form.get("defaultLabourType")

    definitions: list[dict[str, Any]] = []
    max_length = max(len(ids), len(codes), len(names), len(rates))
    for index in range(max_length):
        identifier = ids[index] if index < len(ids) else None
        code = codes[index] if index < len(codes) else ""
        name = names[index] if index < len(names) else ""
        rate_str = rates[index] if index < len(rates) else ""
        if not code and not name:
            continue
        rate_value: float | None = None
        if rate_str and rate_str.strip():
            try:
                rate_value = float(rate_str.strip())
            except (ValueError, TypeError):
                rate_value = None
        is_default = identifier and str(identifier) == str(default_id)
        definitions.append(
            {
                "id": identifier,
                "code": code,
                "name": name,
                "rate": rate_value,
                "is_default": is_default,
            }
        )

    try:
        await labour_types_service.replace_labour_types(definitions)
    except ValueError as exc:
        return await main_module._render_tickets_dashboard(
            request,
            current_user,
            error_message=str(exc),
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    return flash_redirect("/admin/tickets", "Labour types updated.", "success")


@router.post("/admin/tickets/{ticket_id:int}/description", response_class=HTMLResponse)
async def admin_update_ticket_description(ticket_id: int, request: Request):
    main_module = _main()
    current_user, redirect = await main_module._require_helpdesk_page(request)
    if redirect:
        return redirect

    ticket = await tickets_repo.get_ticket(ticket_id)
    if not ticket:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ticket not found")

    form = await request.form()

    description_raw = form.get("description")
    description_value: str | None = None
    if isinstance(description_raw, str):
        normalised_description = description_raw.replace("\r\n", "\n").replace("\r", "\n")
        if normalised_description.strip():
            description_value = normalised_description
        else:
            description_value = None

    return_url_raw = form.get("returnUrl")
    return_url = str(return_url_raw).strip() if isinstance(return_url_raw, str) else ""

    await tickets_service.update_ticket_description(ticket_id, description_value)
    await tickets_service.refresh_ticket_ai_summary(ticket_id)
    await tickets_service.refresh_ticket_ai_tags(ticket_id)

    message = "Ticket description updated."
    destination = f"/admin/tickets/{ticket_id}"
    safe_return_url = _safe_local_redirect_target(return_url, fallback="")
    if safe_return_url.startswith(f"/admin/tickets/{ticket_id}"):
        destination = safe_return_url

    return flash_redirect(destination, message, "success")


@router.post("/admin/tickets/{ticket_id:int}/description/replace", response_class=JSONResponse)
async def admin_replace_ticket_description(ticket_id: int, request: Request):
    main_module = _main()
    current_user, redirect = await main_module._require_helpdesk_page(request)
    if redirect:
        return redirect

    ticket = await tickets_repo.get_ticket(ticket_id)
    if not ticket:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ticket not found")

    summary = ticket.get("ai_summary")
    summary_text = str(summary) if summary is not None else ""
    if not summary_text.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="AI summary is not available. Generate a summary before replacing the description.",
        )

    normalised_summary = summary_text.replace("\r\n", "\n").replace("\r", "\n")

    updated = await tickets_service.update_ticket_description(ticket_id, normalised_summary)
    if not updated:
        updated = await tickets_repo.get_ticket(ticket_id)

    sanitized = sanitize_rich_text(str((updated or {}).get("description") or ""))

    await tickets_service.emit_ticket_updated_event(
        ticket_id,
        actor_type="technician",
        actor=current_user,
    )

    return JSONResponse(
        {
            "status": "success",
            "message": "Ticket description replaced with the AI summary.",
            "description": str((updated or {}).get("description") or ""),
            "descriptionHtml": sanitized.html,
            "descriptionText": sanitized.text_content,
        }
    )


@router.post("/admin/tickets/{ticket_id:int}/details", response_class=HTMLResponse)
async def admin_update_ticket_details(ticket_id: int, request: Request):
    main_module = _main()
    current_user, redirect = await main_module._require_helpdesk_page(request)
    if redirect:
        return redirect

    ticket = await tickets_repo.get_ticket(ticket_id)
    if not ticket:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ticket not found")

    form = await request.form()

    description_raw = form.get("description")
    description_value: str | None = None
    if isinstance(description_raw, str):
        normalised_description = description_raw.replace("\r\n", "\n").replace("\r", "\n")
        if normalised_description.strip():
            description_value = normalised_description
        else:
            description_value = None

    existing_company_id: int | None = None
    raw_existing_company = ticket.get("company_id")
    if raw_existing_company is not None:
        try:
            existing_company_id = int(raw_existing_company)
        except (TypeError, ValueError):
            existing_company_id = None

    def _clean_text(value: Any) -> str:
        return str(value).strip() if isinstance(value, str) else ""

    subject_value = _clean_text(form.get("subject"))
    status_raw = _clean_text(form.get("status"))
    priority_value = _clean_text(form.get("priority")).lower()
    requester_raw = form.get("requesterId")
    assigned_raw = form.get("assignedUserId")
    company_raw = form.get("companyId")
    category_value = _clean_text(form.get("category")) or None
    external_reference = _clean_text(form.get("externalReference")) or None
    return_url_raw = form.get("returnUrl")
    return_url = _clean_text(return_url_raw)

    if status_raw:
        try:
            status_value = await tickets_service.validate_status_choice(status_raw)
        except ValueError as exc:
            return await main_module._render_ticket_detail(
                request,
                current_user,
                ticket_id=ticket_id,
                error_message=str(exc),
                status_code=status.HTTP_400_BAD_REQUEST,
            )
    else:
        status_value = ticket.get("status") or await tickets_service.resolve_status_or_default(None)

    if not subject_value:
        return await main_module._render_ticket_detail(
            request,
            current_user,
            ticket_id=ticket_id,
            error_message="Enter a ticket subject.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    if len(subject_value) > 255:
        return await main_module._render_ticket_detail(
            request,
            current_user,
            ticket_id=ticket_id,
            error_message="Subject must be 255 characters or fewer.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    default_priorities = {"urgent", "high", "normal", "low"}
    ticket_priority = (ticket.get("priority") or "normal").lower()
    allowed_priorities = default_priorities | {ticket_priority}
    if not priority_value:
        return await main_module._render_ticket_detail(
            request,
            current_user,
            ticket_id=ticket_id,
            error_message="Select a priority.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    if priority_value not in allowed_priorities:
        return await main_module._render_ticket_detail(
            request,
            current_user,
            ticket_id=ticket_id,
            error_message="Select a valid priority.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    if category_value and len(category_value) > 64:
        return await main_module._render_ticket_detail(
            request,
            current_user,
            ticket_id=ticket_id,
            error_message="Category must be 64 characters or fewer.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    if external_reference and len(external_reference) > 128:
        return await main_module._render_ticket_detail(
            request,
            current_user,
            ticket_id=ticket_id,
            error_message="External reference must be 128 characters or fewer.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    requester_id: int | None = None
    requester_staff_id: int | None = None
    requester_kind: str | None = None
    requester_identifier: int | None = None
    if requester_raw:
        try:
            requester_kind, requester_identifier = _parse_requester_value(requester_raw)
        except (TypeError, ValueError):
            return await main_module._render_ticket_detail(
                request,
                current_user,
                ticket_id=ticket_id,
                error_message="Select a valid requester.",
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        if requester_kind == "user":
            requester_id = requester_identifier
            requester = await user_repo.get_user_by_id(requester_id)
            if not requester:
                return await main_module._render_ticket_detail(
                    request,
                    current_user,
                    ticket_id=ticket_id,
                    error_message="Select a valid requester.",
                    status_code=status.HTTP_400_BAD_REQUEST,
                )
        elif requester_kind == "staff":
            requester_staff_id = requester_identifier

    assigned_user_id: int | None = None
    if assigned_raw:
        try:
            assigned_user_id = int(assigned_raw)
        except (TypeError, ValueError):
            return await main_module._render_ticket_detail(
                request,
                current_user,
                ticket_id=ticket_id,
                error_message="Select a valid assignee.",
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        has_permission = await membership_repo.user_has_permission(
            assigned_user_id,
            main_module.HELPDESK_PERMISSION_KEY,
        )
        if not has_permission:
            return await main_module._render_ticket_detail(
                request,
                current_user,
                ticket_id=ticket_id,
                error_message="Selected user cannot be assigned to tickets.",
                status_code=status.HTTP_400_BAD_REQUEST,
            )

    company_id: int | None = None
    if company_raw:
        try:
            company_id = int(company_raw)
        except (TypeError, ValueError):
            return await main_module._render_ticket_detail(
                request,
                current_user,
                ticket_id=ticket_id,
                error_message="Select a valid company.",
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        company_record = await company_repo.get_company_by_id(company_id)
        if not company_record:
            return await main_module._render_ticket_detail(
                request,
                current_user,
                ticket_id=ticket_id,
                error_message="Select a valid company.",
                status_code=status.HTTP_400_BAD_REQUEST,
            )

    final_company_id = company_id
    if company_raw is None:
        final_company_id = existing_company_id

    if requester_id is not None or requester_staff_id is not None:
        if final_company_id is None:
            return await main_module._render_ticket_detail(
                request,
                current_user,
                ticket_id=ticket_id,
                error_message="Link the ticket to a company before selecting a requester.",
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        allowed_requesters = await staff_repo.list_enabled_staff_users(final_company_id)
        matched: dict[str, Any] | None = None
        if requester_kind == "staff":
            matched = next((option for option in allowed_requesters if option.get("staff_id") == requester_staff_id), None)
        else:
            matched = next((option for option in allowed_requesters if option.get("user_id") == requester_id), None)
        if not matched:
            return await main_module._render_ticket_detail(
                request,
                current_user,
                ticket_id=ticket_id,
                error_message="Select a requester from the linked company's enabled staff list.",
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        requester_id = matched.get("user_id")
        requester_staff_id = matched.get("staff_id")

    update_fields: dict[str, Any] = {
        "subject": subject_value,
        "priority": priority_value,
        "requester_id": requester_id,
        "requester_staff_id": requester_staff_id,
        "assigned_user_id": assigned_user_id,
        "company_id": company_id,
        "category": category_value,
        "external_reference": external_reference,
    }

    raw_asset_values = form.getlist("assetIds") if hasattr(form, "getlist") else []
    notify_tray = str(form.get("sendTrayNotification", "")).lower() in {"1", "true", "on", "yes"}
    selected_asset_ids: list[int] = []
    for raw_value in raw_asset_values:
        try:
            asset_id_value = int(raw_value)
        except (TypeError, ValueError):
            continue
        if asset_id_value <= 0 or asset_id_value in selected_asset_ids:
            continue
        selected_asset_ids.append(asset_id_value)

    if selected_asset_ids and final_company_id is None:
        return await main_module._render_ticket_detail(
            request,
            current_user,
            ticket_id=ticket_id,
            error_message="Link the ticket to a company before assigning assets.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    validated_asset_ids: list[int] = []
    if selected_asset_ids and final_company_id is not None:
        for asset_id in selected_asset_ids:
            asset = await assets_repo.get_asset_by_id(asset_id)
            if not asset or asset.get("company_id") != final_company_id:
                continue
            validated_asset_ids.append(asset_id)

    await tickets_repo.update_ticket(ticket_id, **update_fields)
    await tickets_repo.set_ticket_status(ticket_id, status_value)
    if description_raw is not None:
        await tickets_service.update_ticket_description(ticket_id, description_value)
    await tickets_service.refresh_ticket_ai_summary(ticket_id)
    await tickets_service.refresh_ticket_ai_tags(ticket_id)
    await tickets_service.broadcast_ticket_event(action="updated", ticket_id=ticket_id)
    await tickets_service.emit_ticket_details_updated_event(
        ticket_id,
        actor_type="technician",
        actor=current_user,
    )

    await tickets_repo.replace_ticket_assets(ticket_id, validated_asset_ids)
    if notify_tray:
        ticket_reference = ticket.get("ticket_number") or ticket.get("id") or ticket_id
        await tray_service.push_notification_to_company_devices(
            company_id=final_company_id,
            title="Your ticket is updated",
            body=f"Ticket #{ticket_reference} has been updated.",
            asset_ids=validated_asset_ids,
            initiated_by_user_id=int(current_user["id"]),
        )

    message = "Ticket details updated."
    destination = f"/admin/tickets/{ticket_id}"
    safe_return_url = _safe_local_redirect_target(return_url, fallback="")
    if safe_return_url.startswith(f"/admin/tickets/{ticket_id}"):
        destination = safe_return_url

    return flash_redirect(destination, message, "success")


@router.post("/admin/tickets/{ticket_id:int}/ai/reprocess", response_class=JSONResponse)
async def admin_reprocess_ticket_ai(ticket_id: int, request: Request):
    main_module = _main()
    current_user, redirect = await main_module._require_helpdesk_page(request)
    if redirect:
        return redirect

    ticket = await tickets_repo.get_ticket(ticket_id)
    if not ticket:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ticket not found")

    try:
        await tickets_service.refresh_ticket_ai_summary(ticket_id)
    except Exception as exc:  # pragma: no cover - defensive against unexpected failures
        log_error(
            "Failed to queue ticket AI summary refresh",
            ticket_id=ticket_id,
            user_id=current_user.get("id"),
            error=str(exc),
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unable to refresh AI summary.",
        ) from exc

    try:
        await tickets_service.refresh_ticket_ai_tags(ticket_id)
    except Exception as exc:  # pragma: no cover - defensive against unexpected failures
        log_error(
            "Failed to queue ticket AI tags refresh",
            ticket_id=ticket_id,
            user_id=current_user.get("id"),
            error=str(exc),
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Unable to refresh AI tags.",
        ) from exc

    return JSONResponse(
        {
            "status": "queued",
            "message": "AI summary and tags will be regenerated shortly.",
        }
    )


@router.post("/admin/tickets/{ticket_id:int}/delete", response_class=HTMLResponse)
async def admin_delete_ticket(ticket_id: int, request: Request):
    main_module = _main()
    current_user, redirect = await main_module._require_super_admin_page(request)
    if redirect:
        return redirect

    ticket = await tickets_repo.get_ticket(ticket_id)
    if not ticket:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ticket not found")

    try:
        await tickets_repo.delete_ticket(ticket_id)
    except Exception as exc:  # pragma: no cover - defensive logging
        log_error("Failed to delete ticket", ticket_id=ticket_id, error=str(exc))
        return await main_module._render_ticket_detail(
            request,
            current_user,
            ticket_id=ticket_id,
            error_message="Unable to delete the ticket. Please try again.",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    log_info(
        "Ticket deleted",
        ticket_id=ticket_id,
        deleted_by=current_user.get("id") if current_user else None,
    )
    await tickets_service.broadcast_ticket_event(action="deleted", ticket_id=ticket_id)

    message = f"Ticket {ticket_id} deleted."
    return flash_redirect("/admin/tickets", message, "success")


@router.post("/admin/tickets/bulk-delete", response_class=HTMLResponse)
async def admin_bulk_delete_tickets(request: Request):
    main_module = _main()
    current_user, redirect = await main_module._require_super_admin_page(request)
    if redirect:
        return redirect

    form = await request.form()
    raw_ids = form.getlist("ticketIds")
    ticket_ids: list[int] = []
    seen: set[int] = set()
    for raw in raw_ids:
        try:
            identifier = int(raw)
        except (TypeError, ValueError):
            continue
        if identifier <= 0 or identifier in seen:
            continue
        seen.add(identifier)
        ticket_ids.append(identifier)

    if not ticket_ids:
        return await main_module._render_tickets_dashboard(
            request,
            current_user,
            error_message="Select at least one ticket to delete.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    try:
        deleted_count = await tickets_repo.delete_tickets(ticket_ids)
    except Exception as exc:  # pragma: no cover - defensive logging
        log_error(
            "Failed to bulk delete tickets",
            ticket_ids=ticket_ids,
            error=str(exc),
        )
        return await main_module._render_tickets_dashboard(
            request,
            current_user,
            error_message="Unable to delete the selected tickets. Please try again.",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    if deleted_count == 0:
        return await main_module._render_tickets_dashboard(
            request,
            current_user,
            error_message="No matching tickets were found to delete.",
            status_code=status.HTTP_404_NOT_FOUND,
        )

    log_info(
        "Tickets bulk deleted",
        deleted_count=deleted_count,
        deleted_by=current_user.get("id") if current_user else None,
        ticket_ids=ticket_ids,
    )
    await tickets_service.broadcast_ticket_event(action="deleted")

    message_suffix = "ticket" if deleted_count == 1 else "tickets"
    redirect_message = f"Deleted {deleted_count} {message_suffix}."
    if deleted_count < len(ticket_ids):
        redirect_message = (
            f"Deleted {deleted_count} {message_suffix}."
            " Some selected tickets were not found."
        )

    return_url_raw = form.get("returnUrl")
    return_url = str(return_url_raw) if isinstance(return_url_raw, str) else ""
    safe_return_url = _safe_local_redirect_target(return_url, fallback="")
    if safe_return_url:
        destination = safe_return_url
    else:
        destination = "/admin/tickets"

    return flash_redirect(destination, redirect_message, "success")


@router.post("/admin/tickets/{ticket_id:int}/replies", response_class=HTMLResponse)
async def admin_create_ticket_reply(ticket_id: int, request: Request):
    main_module = _main()
    current_user, redirect = await main_module._require_helpdesk_page(request)
    if redirect:
        return redirect
    form = await request.form()
    body_value = form.get("body", "")
    body_raw = str(body_value) if isinstance(body_value, str) else ""
    sanitized_body = sanitize_rich_text(body_raw)
    is_internal = str(form.get("isInternal", "")).lower() in {"1", "true", "on", "yes"}
    minutes_input_raw = form.get("minutesSpent", "")
    minutes_input = str(minutes_input_raw).strip() if isinstance(minutes_input_raw, str) else ""
    minutes_spent: int | None = None
    if minutes_input:
        try:
            minutes_candidate = int(minutes_input)
        except (TypeError, ValueError):
            return await main_module._render_ticket_detail(
                request,
                current_user,
                ticket_id=ticket_id,
                error_message="Enter the time spent in minutes as a whole number.",
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        if minutes_candidate < 0:
            return await main_module._render_ticket_detail(
                request,
                current_user,
                ticket_id=ticket_id,
                error_message="Minutes cannot be negative.",
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        if minutes_candidate > 1440:
            return await main_module._render_ticket_detail(
                request,
                current_user,
                ticket_id=ticket_id,
                error_message="Minutes cannot exceed 1440 per reply.",
                status_code=status.HTTP_400_BAD_REQUEST,
            )
        minutes_spent = minutes_candidate
    is_billable = str(form.get("isBillable", "")).lower() in {"1", "true", "on", "yes"}
    labour_type_raw = form.get("labourTypeId")
    labour_type_id: int | None = None
    if isinstance(labour_type_raw, str):
        labour_type_text = labour_type_raw.strip()
        if labour_type_text:
            try:
                labour_candidate = int(labour_type_text)
            except (TypeError, ValueError):
                return await main_module._render_ticket_detail(
                    request,
                    current_user,
                    ticket_id=ticket_id,
                    error_message="Select a valid labour type.",
                    status_code=status.HTTP_400_BAD_REQUEST,
                )
            if labour_candidate <= 0:
                return await main_module._render_ticket_detail(
                    request,
                    current_user,
                    ticket_id=ticket_id,
                    error_message="Select a valid labour type.",
                    status_code=status.HTTP_400_BAD_REQUEST,
                )
            labour_record = await labour_types_service.get_labour_type(labour_candidate)
            if not labour_record:
                return await main_module._render_ticket_detail(
                    request,
                    current_user,
                    ticket_id=ticket_id,
                    error_message="Selected labour type could not be found.",
                    status_code=status.HTTP_400_BAD_REQUEST,
                )
            labour_type_id = labour_candidate
    if minutes_spent and minutes_spent > 0 and labour_type_id is None:
        default_labour = await labour_types_service.get_default_labour_type()
        if default_labour:
            labour_type_id = default_labour.get("id")
    if not sanitized_body.has_rich_content:
        return await main_module._render_ticket_detail(
            request,
            current_user,
            ticket_id=ticket_id,
            error_message="Enter a reply before submitting.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    ticket = await tickets_repo.get_ticket(ticket_id)
    if not ticket:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ticket not found")

    status_definitions = await tickets_service.list_status_definitions()
    valid_reply_statuses = {definition.tech_status for definition in status_definitions}
    default_reply_status = next((definition.tech_status for definition in status_definitions if definition.is_default), None)
    if not default_reply_status:
        default_reply_status = "pending" if "pending" in valid_reply_statuses else (next(iter(valid_reply_statuses), "open"))
    reply_status = str(get_last_form_value(form, "replyStatus", default_reply_status) or default_reply_status).strip().lower()
    if reply_status not in valid_reply_statuses:
        return await main_module._render_ticket_detail(
            request,
            current_user,
            ticket_id=ticket_id,
            error_message="Select a valid ticket status for the reply.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    if ticket.get("xero_invoice_number"):
        return await main_module._render_ticket_detail(
            request,
            current_user,
            ticket_id=ticket_id,
            error_message="Cannot add replies to a billed ticket. This ticket has been invoiced and closed.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    has_failed_attachments = False
    try:
        author_id = current_user.get("id")
        if not isinstance(author_id, int):
            try:
                author_id = int(author_id)
            except (TypeError, ValueError, AttributeError):
                author_id = None
        created_reply = await tickets_repo.create_reply(
            ticket_id=ticket_id,
            author_id=author_id,
            body=sanitized_body.html,
            is_internal=is_internal,
            minutes_spent=minutes_spent,
            is_billable=is_billable,
            labour_type_id=labour_type_id,
        )
        attachments = form.getlist("attachments")
        if attachments:
            for attachment in attachments:
                filename = (attachment.filename or "") if attachment else ""
                if filename:
                    try:
                        await attachments_service.save_uploaded_file(
                            ticket_id=ticket_id,
                            file=attachment,
                            access_level="closed",
                            uploaded_by_user_id=author_id,
                        )
                    except Exception as exc:  # pragma: no cover - defensive logging
                        log_error(
                            "Failed to save attachment",
                            ticket_id=ticket_id,
                            filename=filename,
                            error=str(exc),
                        )
                        has_failed_attachments = True
                else:
                    log_error(
                        "Skipped attachment without filename; treating as failed upload",
                        ticket_id=ticket_id,
                    )
                    has_failed_attachments = True
        if isinstance(author_id, int):
            await tickets_repo.add_watcher(ticket_id, author_id)
        await tickets_repo.set_ticket_status(ticket_id, reply_status)
        await tickets_service.refresh_ticket_ai_summary(ticket_id)
        await tickets_service.refresh_ticket_ai_tags(ticket_id)
        await tickets_service.broadcast_ticket_event(action="reply", ticket_id=ticket_id)
        await tickets_service.emit_ticket_updated_event(
            ticket_id,
            actor_type="technician",
            actor=current_user,
        )
        await tickets_service.emit_ticket_replied_event(
            ticket_id,
            actor_type="technician",
            actor=current_user,
            reply=created_reply,
        )
    except Exception as exc:  # pragma: no cover - defensive logging
        log_error("Failed to create ticket reply", error=str(exc))
        return await main_module._render_ticket_detail(
            request,
            current_user,
            ticket_id=ticket_id,
            error_message="Unable to save the reply. Please try again.",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
    message_text = "Reply posted."
    if has_failed_attachments:
        message_text = "Reply posted, but some attachments failed to upload."
    return flash_redirect(f"/admin/tickets/{ticket_id}", message_text, "success")


__all__ = ["router"]
