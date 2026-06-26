from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Mapping, Sequence

from loguru import logger

from app.core.config import get_settings
from app.repositories import rag_relationships as rel_repo
from app.services import modules as modules_service


class RelationshipType(StrEnum):
    DIRECT_MATCH = "DIRECT_MATCH"
    RELATED = "RELATED"
    SUPPORTING = "SUPPORTING"
    DUPLICATE = "DUPLICATE"
    NOT_RELEVANT = "NOT_RELEVANT"
    FOLLOW_UP = "FOLLOW_UP"
    KNOWN_ISSUE = "KNOWN_ISSUE"
    PARENT_CHILD = "PARENT_CHILD"


class MatchStatus(StrEnum):
    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    MATCH = "MATCH"
    NO_MATCH = "NO_MATCH"
    FAILED = "FAILED"
    STALE = "STALE"


_POSITIVE_TYPES = {
    RelationshipType.DIRECT_MATCH,
    RelationshipType.RELATED,
    RelationshipType.SUPPORTING,
    RelationshipType.DUPLICATE,
    RelationshipType.FOLLOW_UP,
    RelationshipType.KNOWN_ISSUE,
    RelationshipType.PARENT_CHILD,
}
_MATCH_ORDER = {
    RelationshipType.DIRECT_MATCH.value: 0,
    RelationshipType.RELATED.value: 1,
    RelationshipType.SUPPORTING.value: 2,
}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def on_document_indexed(document_id: int, *, content_changed: bool) -> int:
    settings = get_settings()
    if not settings.enable_background_relationships:
        return 0
    if not content_changed:
        return 0
    await rel_repo.mark_relationships_stale(document_id)
    return await enqueue_relationships_for_document(document_id)


async def enqueue_relationships_for_document(document_id: int) -> int:
    settings = get_settings()
    if await rel_repo.matching_paused():
        return 0
    source = await rel_repo.get_document(document_id)
    if not source:
        return 0
    targets = await rel_repo.list_compatible_targets(
        document_id,
        include_tickets=bool(settings.enable_ticket_relationships),
    )
    queued = 0
    for target in targets:
        if _skip_pair(source, target, bool(settings.enable_ticket_relationships)):
            continue
        if await rel_repo.relationship_current(document_id, int(target["id"])):
            continue
        if await rel_repo.enqueue(
            document_id,
            int(target["id"]),
            priority=_relationship_queue_priority(source, target),
        ):
            queued += 1
    return queued


def _skip_pair(
    source: Mapping[str, Any], target: Mapping[str, Any], include_tickets: bool
) -> bool:
    if int(source["id"]) == int(target["id"]):
        return True
    if (
        source.get("company_id")
        and target.get("company_id")
        and int(source["company_id"]) != int(target["company_id"])
    ):
        return True
    source_type = str(source.get("source_type") or "")
    target_type = str(target.get("source_type") or "")
    if source_type == "tickets" and target_type == "tickets" and not include_tickets:
        return True
    return False


def _relationship_queue_priority(
    source: Mapping[str, Any], target: Mapping[str, Any]
) -> int:
    """Return queue priority for a relationship candidate pair."""

    source_type = str(source.get("source_type") or "")
    target_type = str(target.get("source_type") or "")
    if source_type == "tickets" or target_type == "tickets":
        return 1100
    return 1000


def _evaluation_document_order(
    source: Mapping[str, Any], target: Mapping[str, Any]
) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
    """Return the source/target order to present to the relationship evaluator.

    Tickets should be the anchor document whenever a ticket is compared with
    another document type so relationship prompts consistently present the
    support request as Document A. Ticket-to-ticket and non-ticket pairs retain
    their queued order.
    """

    source_type = str(source.get("source_type") or "")
    target_type = str(target.get("source_type") or "")
    if source_type != "tickets" and target_type == "tickets":
        return target, source
    return source, target


def _prompt(source: Mapping[str, Any], target: Mapping[str, Any]) -> str:
    source, target = _evaluation_document_order(source, target)
    return f"""You evaluate MyPortal RAG document relationships. Return JSON only.

Document A
{source.get('source_type')} #{source.get('source_id')}
{source.get('title')}
{source.get('content') or ''}
----------------------------
Document B
{target.get('source_type')} #{target.get('source_id')}
{target.get('title')}
{target.get('content') or ''}

Determine whether these documents are related. Store negative results too.
Use one relationship value: DIRECT_MATCH, RELATED, SUPPORTING, DUPLICATE, NOT_RELEVANT, FOLLOW_UP, KNOWN_ISSUE, PARENT_CHILD.
Return JSON only:
{{"relationship":"DIRECT_MATCH","confidence":0.94,"score":0.93,"reason":"...","supporting_excerpt":"..."}}"""


def _extract_json_object(text: str) -> str:
    stripped = text.strip()
    if not stripped:
        raise ValueError("Relationship evaluator returned an empty response")
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if lines and lines[0].lstrip().startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        stripped = "\n".join(lines).strip()
    if stripped.startswith("{"):
        return stripped
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start >= 0 and end > start:
        return stripped[start : end + 1]
    raise ValueError("Relationship evaluator did not return a JSON object")


def _relationship_response_payload(response: Any) -> Any:
    if not isinstance(response, Mapping):
        return response
    raw = response.get("payload")
    if raw is None:
        raw = response.get("response")
    if raw is None:
        raw = response.get("message")
    if isinstance(raw, Mapping):
        for key in ("response", "message", "text"):
            nested = raw.get(key)
            if nested is not None:
                return nested
    return raw


def parse_relationship_response(value: Any, *, min_score: float) -> dict[str, Any]:
    if isinstance(value, Mapping):
        payload = value
    else:
        text = str(value or "").strip()
        payload = json.loads(_extract_json_object(text))
    relationship = (
        str(
            payload.get("relationship")
            or payload.get("relationship_type")
            or "NOT_RELEVANT"
        )
        .strip()
        .upper()
    )
    try:
        relationship_type = RelationshipType(relationship)
    except ValueError:
        relationship_type = RelationshipType.NOT_RELEVANT
    score = max(
        0.0,
        min(1.0, float(payload.get("score") or payload.get("relevance_score") or 0.0)),
    )
    confidence = max(0.0, min(1.0, float(payload.get("confidence") or 0.0)))
    match_status = (
        MatchStatus.MATCH
        if relationship_type in _POSITIVE_TYPES and score >= min_score
        else MatchStatus.NO_MATCH
    )
    if match_status is MatchStatus.NO_MATCH:
        relationship_type = RelationshipType.NOT_RELEVANT
    return {
        "relationship_type": relationship_type.value,
        "match_status": match_status.value,
        "relevance_score": score,
        "confidence": confidence,
        "reason": str(payload.get("reason") or "")[:2000],
        "supporting_excerpt": str(payload.get("supporting_excerpt") or "")[:2000],
    }


async def evaluate_next_batch(*, limit: int | None = None) -> int:
    settings = get_settings()
    if not settings.enable_background_relationships:
        return 0
    if await rel_repo.matching_paused():
        return 0
    jobs = await rel_repo.claim_jobs(limit or settings.rag_relationship_batch_size)
    processed = 0
    semaphore = asyncio.Semaphore(max(1, int(settings.rag_relationship_max_concurrent)))

    async def _one(job: Mapping[str, Any]) -> None:
        nonlocal processed
        async with semaphore:
            started = time.perf_counter()
            try:
                source = await rel_repo.get_document_with_content(
                    int(job["source_document_id"])
                )
                target = await rel_repo.get_document_with_content(
                    int(job["target_document_id"])
                )
                if not source or not target:
                    raise RuntimeError("Source or target document no longer exists")
                if await rel_repo.relationship_current(
                    int(source["id"]), int(target["id"])
                ):
                    await rel_repo.complete_queue_item(int(job["id"]), "skipped")
                    return
                response = await modules_service.trigger_module(
                    "ollama",
                    {
                        "prompt": _prompt(source, target),
                        "format": "json",
                        "model": settings.rag_relationship_model,
                    },
                    background=False,
                )
                if isinstance(response, Mapping) and str(
                    response.get("status") or ""
                ).lower() in {"error", "failed", "skipped"}:
                    reason = (
                        response.get("last_error")
                        or response.get("error")
                        or response.get("reason")
                        or "module did not complete"
                    )
                    raise RuntimeError(f"Relationship evaluator unavailable: {reason}")
                raw = _relationship_response_payload(response)
                parsed = parse_relationship_response(
                    raw, min_score=settings.rag_relationship_min_score
                )
                await rel_repo.store_relationship(
                    int(source["id"]),
                    int(target["id"]),
                    parsed,
                    evaluated_model=settings.rag_relationship_model,
                    source_hash=str(source.get("content_hash") or ""),
                    target_hash=str(target.get("content_hash") or ""),
                    duration_ms=int((time.perf_counter() - started) * 1000),
                )
                await rel_repo.complete_queue_item(int(job["id"]), "completed")
                processed += 1
            except Exception as exc:
                await rel_repo.fail_queue_item(int(job["id"]), str(exc), max_retries=5)
                logger.warning("RAG relationship job failed: {}", exc)

    await asyncio.gather(*(_one(job) for job in jobs))
    return processed


async def relationship_worker(stop_event: asyncio.Event) -> None:
    settings = get_settings()
    while not stop_event.is_set():
        processed = await evaluate_next_batch()
        if not processed:
            await asyncio.sleep(
                max(0.1, settings.rag_relationship_idle_delay_ms / 1000)
            )


async def load_evidence_for_document(
    document_id: int, *, limit: int = 12
) -> list[dict[str, Any]]:
    rows = await rel_repo.list_relationship_evidence(document_id, limit=limit)
    return sorted(
        rows,
        key=lambda r: (
            _MATCH_ORDER.get(str(r.get("relationship_type")), 99),
            -float(r.get("relevance_score") or 0),
        ),
    )
