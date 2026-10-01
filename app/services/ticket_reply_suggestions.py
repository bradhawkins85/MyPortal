"""Draft a technician reply from a ticket's stored resolution relationships.

Only DIRECT_MATCH and KNOWN_ISSUE relationships are used: they are the
relationship types that assert the related record fixes, or is the same
recurring fault as, the current ticket.  The model only sees records the
technician is authorised to read, and its draft is rejected if it cites a
record it was not given.  The draft is never sent automatically; it is
inserted into the reply editor for the technician to review.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from app.core.logging import log_error
from app.repositories import rag_index as rag_index_repo
from app.repositories import rag_relationships as rag_relationship_repo
from app.repositories import tickets as tickets_repo
from app.services import modules as modules_service
from app.services import rag_index as rag_index_service
from app.services.ai_prompt_security import UntrustedRecord, build_prompt, validate_references
from app.services.rag_permissions import can_access_candidate
from app.services.rag_urls import canonical_source_url
from app.services.sanitization import sanitize_rich_text

REPLY_RELATIONSHIP_TYPES = ("DIRECT_MATCH", "KNOWN_ISSUE")
_RELATIONSHIP_LABELS = {"DIRECT_MATCH": "Direct match", "KNOWN_ISSUE": "Known issue"}
_MAX_SOURCES = 6
_MAX_SOURCE_CHARS = 2000
_MAX_TICKET_CHARS = 2000

_INSTRUCTIONS = (
    "You draft helpdesk replies for a technician to review before sending. "
    "Write a polite, concise reply to the requester of the current ticket that "
    "explains the resolution steps found in the supplied related records. Use only "
    "the supplied resolution evidence; do not invent steps, products, or facts. "
    "After each sentence or step that relies on a related record, cite it with that "
    "record's exact record_id in square brackets, for example [KB:vpn-reset] or "
    "[Ticket:123]. Never cite the current ticket and never cite any other identifier. "
    "If the evidence does not fit the current ticket, say so briefly instead of guessing."
)
_TASK = (
    "Return only the reply body as plain text with short paragraphs or a numbered "
    "list of steps. Do not include a subject line, signature, or Markdown headings."
)


@dataclass(frozen=True)
class ReplySource:
    reference: str
    relationship_type: str
    source_type: str
    title: str
    url: str
    resolution: str

    def public_dict(self) -> dict[str, str]:
        return {
            "reference": self.reference,
            "relationship_label": _RELATIONSHIP_LABELS.get(self.relationship_type, "Related"),
            "title": self.title,
            "url": self.url,
        }


class ReplySuggestionError(Exception):
    """Raised when a draft cannot be produced safely."""


def _json(value: Any) -> Any:
    if isinstance(value, (Mapping, list)):
        return value
    try:
        return json.loads(str(value or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}


def _plain_text(value: Any, limit: int) -> str:
    text = sanitize_rich_text(str(value or "")).text_content
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text[:limit]


def reference_for(source_type: str, source_id: Any, metadata: Mapping[str, Any]) -> str | None:
    if source_type == "knowledge_base":
        slug = str(metadata.get("slug") or "").strip()
        return f"[KB:{slug}]" if slug and "]" not in slug else None
    if source_type == "tickets":
        try:
            return f"[Ticket:{int(source_id)}]"
        except (TypeError, ValueError):
            return None
    return None


async def collect_reply_sources(
    ticket_id: int,
    *,
    user: Mapping[str, Any],
    memberships: Sequence[Mapping[str, Any]],
) -> list[ReplySource]:
    """Return authorised DIRECT_MATCH/KNOWN_ISSUE records with resolution text."""
    document = await rag_index_repo.get_document_by_source(
        "tickets", str(ticket_id), rag_index_service.embedding_model()
    )
    if not document:
        return []
    rows = await rag_relationship_repo.list_relationship_evidence(int(document["id"]), limit=24)
    rows = [
        row
        for row in rows
        if str(row.get("relationship_type") or "") in REPLY_RELATIONSHIP_TYPES
        and bool(row.get("target_available"))
    ]
    rows.sort(
        key=lambda row: (
            REPLY_RELATIONSHIP_TYPES.index(str(row.get("relationship_type"))),
            -float(row.get("relevance_score") or 0),
        )
    )

    sources: list[ReplySource] = []
    seen: set[str] = set()
    for row in rows:
        if len(sources) >= _MAX_SOURCES:
            break
        if not can_access_candidate(
            {"permission_scope": _json(row.get("permission_scope_json"))},
            user=user,
            memberships=memberships,
        ):
            continue
        source_type = str(row.get("source_type") or "").strip()
        source_id = row.get("source_id")
        metadata = _json(row.get("metadata_json"))
        if not isinstance(metadata, Mapping):
            metadata = {}
        reference = reference_for(source_type, source_id, metadata)
        if not reference or reference in seen or reference == f"[Ticket:{ticket_id}]":
            continue
        if source_type == "tickets":
            related = await tickets_repo.get_ticket(int(source_id))
            resolution = _plain_text((related or {}).get("resolution_steps"), _MAX_SOURCE_CHARS)
        else:
            resolution = _plain_text(row.get("content"), _MAX_SOURCE_CHARS)
        if not resolution:
            continue
        url = canonical_source_url(
            source_type, source_id, metadata=metadata, supplied_url=row.get("url")
        )
        if not url:
            continue
        seen.add(reference)
        sources.append(
            ReplySource(
                reference=reference,
                relationship_type=str(row.get("relationship_type")),
                source_type=source_type,
                title=str(row.get("title") or reference).strip()[:180],
                url=url,
                resolution=resolution,
            )
        )
    return sources


def build_reply_prompt(ticket: Mapping[str, Any], sources: Sequence[ReplySource]) -> str:
    records = [
        UntrustedRecord(
            "current-ticket",
            "helpdesk ticket being answered (not citable)",
            {
                "subject": str(ticket.get("subject") or "")[:300],
                "description": _plain_text(ticket.get("description"), _MAX_TICKET_CHARS),
            },
            "Use only to understand what the requester needs",
        )
    ]
    for source in sources:
        records.append(
            UntrustedRecord(
                source.reference,
                "resolved helpdesk ticket"
                if source.source_type == "tickets"
                else "knowledge-base article",
                {
                    "relationship": source.relationship_type,
                    "title": source.title,
                    "resolution": source.resolution,
                },
                "Use as resolution evidence and cite by record_id",
            )
        )
    return build_prompt(_INSTRUCTIONS, records, task=_TASK)


def _response_text(result: Mapping[str, Any]) -> str:
    payload = result.get("response")
    if isinstance(payload, Mapping):
        return str(payload.get("response") or payload.get("message") or payload.get("text") or "")
    return str(payload or "")


def _clean_draft(text: str) -> str:
    text = re.sub(r"<think>.*?</think>", "", text or "", flags=re.DOTALL | re.IGNORECASE)
    text = re.sub(r"^```\w*\s*|\s*```$", "", text.strip())
    return text.strip()


async def suggest_reply(
    ticket: Mapping[str, Any],
    *,
    user: Mapping[str, Any],
    memberships: Sequence[Mapping[str, Any]],
) -> dict[str, Any]:
    """Return ``{"draft", "sources"}``; raise ReplySuggestionError otherwise."""
    ticket_id = int(ticket["id"])
    sources = await collect_reply_sources(ticket_id, user=user, memberships=memberships)
    if not sources:
        raise ReplySuggestionError(
            "No direct matches or known issues with resolution steps are linked to this ticket yet."
        )
    prompt = build_reply_prompt(ticket, sources)
    try:
        result = await modules_service.trigger_module(
            "ollama",
            {"prompt": prompt, "temperature": 0.2, "max_tokens": 1024},
            background=False,
        )
    except Exception as exc:  # pragma: no cover - network interaction
        log_error("Suggested reply generation failed", ticket_id=ticket_id, error=str(exc))
        raise ReplySuggestionError("The configured LLM could not draft a reply.") from exc
    if not modules_service.module_result_succeeded(result):
        raise ReplySuggestionError("The configured LLM could not draft a reply.")
    draft = _clean_draft(_response_text(result))
    if not draft:
        raise ReplySuggestionError("The configured LLM returned an empty draft.")
    try:
        validate_references(draft, {source.reference for source in sources})
    except ValueError as exc:
        log_error("Suggested reply cited unsupplied records", ticket_id=ticket_id, error=str(exc))
        raise ReplySuggestionError(
            "The drafted reply cited records it was not given, so it was discarded. Please try again."
        ) from exc
    cited = [source for source in sources if source.reference in draft]
    return {"draft": draft, "sources": [source.public_dict() for source in cited or sources]}
