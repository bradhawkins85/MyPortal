"""Suggest knowledge base articles and resolved tickets while a ticket is raised.

The portal ticket form calls this as the requester types so an existing answer
can resolve the issue before a duplicate ticket is created. Retrieval reuses
the permission-checked RAG index, then every suggestion is re-checked against
its live source so a requester only ever sees what the portal would show them.
"""

from __future__ import annotations

import html
import re
from typing import Any, Collection, Mapping, Sequence

from app.core.logging import log_error
from app.repositories import knowledge_base as kb_repo
from app.repositories import tickets as tickets_repo
from app.services import ai_consent, rag_retrieval

SOURCE_TYPES = ("knowledge_base", "tickets")
MIN_QUERY_LENGTH = 12
MAX_QUERY_LENGTH = 1500
DEFAULT_LIMIT = 3
_CANDIDATE_LIMIT = 10
_RESOLVED_STATUSES = {"resolved", "closed"}
# Excluding "<" from the body keeps matching linear on runs of unclosed "<".
_TAG_RE = re.compile(r"<[^<>]*>")
# Drafts are cut before any regex runs so typed input bounds the work done.
_MAX_INPUT_LENGTH = 20000
_SPACE_RE = re.compile(r"\s+")


def build_query(subject: str | None, description: str | None) -> str:
    """Combine the form fields into plain retrieval text."""

    subject = (subject or "")[:_MAX_INPUT_LENGTH]
    description = (description or "")[:_MAX_INPUT_LENGTH]
    text = f"{subject}\n{_TAG_RE.sub(' ', description)}"
    return _SPACE_RE.sub(" ", html.unescape(text)).strip()[:MAX_QUERY_LENGTH]


def _summary(value: Any, length: int = 220) -> str:
    text = _SPACE_RE.sub(" ", html.unescape(_TAG_RE.sub(" ", str(value or "")))).strip()
    if len(text) <= length:
        return text
    return text[: length - 1].rstrip() + "…"


async def _kb_suggestion(candidate: Mapping[str, Any]) -> dict[str, Any] | None:
    # Retrieval already re-authorised the live article; load it for its slug.
    source_id = str(candidate.get("source_id") or "")
    try:
        article = await kb_repo.get_article_by_id(int(source_id))
    except (TypeError, ValueError):
        article = await kb_repo.get_article_by_slug(source_id)
    if not article or not article.get("is_published") or not article.get("slug"):
        return None
    return {
        "type": "knowledge_base",
        "id": int(article["id"]),
        "title": str(article.get("title") or "Knowledge base article"),
        "summary": _summary(article.get("summary") or article.get("content")),
        "url": f"/knowledge-base/articles/{article['slug']}",
    }


async def _ticket_suggestion(
    candidate: Mapping[str, Any],
    *,
    user_id: int,
    full_ticket_access: bool,
    company_ticket_ids: Collection[int],
) -> dict[str, Any] | None:
    try:
        ticket_id = int(candidate.get("source_id"))
    except (TypeError, ValueError):
        return None
    ticket = await tickets_repo.get_ticket(ticket_id)
    if not ticket:
        return None
    if str(ticket.get("status") or "").casefold() not in _RESOLVED_STATUSES:
        return None
    # Mirror the portal ticket detail rule so every link opens for this user.
    allowed = full_ticket_access or ticket.get("requester_id") == user_id
    if not allowed:
        try:
            allowed = int(ticket.get("company_id")) in company_ticket_ids
        except (TypeError, ValueError):
            allowed = False
    if not allowed:
        allowed = await tickets_repo.is_ticket_watcher(ticket_id, user_id)
    if not allowed:
        return None
    # Indexed ticket text and AI resolution steps can draw on internal notes,
    # so only the subject the requester could already read is shown.
    return {
        "type": "ticket",
        "id": ticket_id,
        "title": str(ticket.get("subject") or f"Ticket #{ticket_id}"),
        "summary": f"Resolved ticket #{ticket_id}",
        "url": f"/tickets/{ticket_id}",
    }


async def suggest_for_new_ticket(
    subject: str | None,
    description: str | None,
    user: Mapping[str, Any],
    *,
    active_company_id: int | None = None,
    memberships: Sequence[Mapping[str, Any]] | None = None,
    full_ticket_access: bool = False,
    company_ticket_ids: Collection[int] = (),
    limit: int = DEFAULT_LIMIT,
) -> list[dict[str, Any]]:
    """Return up to ``limit`` articles and resolved tickets matching the draft."""

    query = build_query(subject, description)
    if len(query) < MIN_QUERY_LENGTH:
        return []
    try:
        user_id = int(user.get("id") or 0)
    except (TypeError, ValueError):
        return []
    if user_id <= 0:
        return []
    # The draft would be embedded for retrieval; honour the user's AI opt-out.
    if ai_consent.is_opted_out_record(user) or not await ai_consent.is_ai_allowed_for_user(user_id):
        return []
    try:
        # The LLM reranker is skipped: this runs on every pause in typing.
        candidates = await rag_retrieval.retrieve_candidates(
            query,
            user,
            active_company_id=active_company_id,
            memberships=memberships,
            source_filters=SOURCE_TYPES,
            limit=_CANDIDATE_LIMIT,
            rerank=False,
        )
    except Exception as exc:  # pragma: no cover - suggestions must never block a ticket
        log_error("Ticket form suggestion retrieval failed", error=str(exc))
        return []

    suggestions: list[dict[str, Any]] = []
    seen: set[tuple[str, int]] = set()
    for candidate in candidates:
        source_type = candidate.get("source_type")
        if source_type == "knowledge_base":
            suggestion = await _kb_suggestion(candidate)
        elif source_type == "tickets":
            suggestion = await _ticket_suggestion(
                candidate,
                user_id=user_id,
                full_ticket_access=full_ticket_access,
                company_ticket_ids=company_ticket_ids,
            )
        else:
            continue
        if not suggestion:
            continue
        key = (suggestion["type"], suggestion["id"])
        if key in seen:
            continue
        seen.add(key)
        suggestions.append(suggestion)
        if len(suggestions) >= max(1, limit):
            break
    return suggestions
