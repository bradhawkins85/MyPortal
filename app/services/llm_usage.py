"""LLM usage tracking.

Every request MyPortal sends to an LLM provider is recorded with the
MyPortal function that made it and the input/output token counts the
provider reported. The Administration > LLM Usage page reads these rows.

The calling function is identified without changing call sites:
``trigger_module`` resolves it from the caller's stack (or from the module
slug for the AI ticket actions) and stores it in a context variable that
follows the request into background tasks. ``_invoke_ollama`` and the
other direct provider calls read it back when they record usage.

Recording is best-effort: a failure here is logged and never interrupts
the LLM request it describes.
"""

from __future__ import annotations

import contextvars
import math
import sys
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Iterable, Mapping

from loguru import logger

from app.repositories import llm_usage as usage_repo


@dataclass(frozen=True)
class LLMFeature:
    key: str
    label: str
    description: str
    callers: tuple[tuple[str, str], ...] = ()
    module_slugs: tuple[str, ...] = ()


UNATTRIBUTED_FEATURE = "other"

# Every MyPortal function that sends requests to the LLM. ``callers`` are the
# (module, function) frames that identify the feature on the call stack;
# ``module_slugs`` attribute requests made by an AI ticket action module.
FEATURES: tuple[LLMFeature, ...] = (
    LLMFeature(
        "agent", "MyPortal Agent",
        "Answers questions from the Agent chat using RAG search results.",
        callers=(("app.services.agent", "_invoke_agent_llm"),),
    ),
    LLMFeature(
        "ticket_ai_summary", "Ticket AI summary",
        "Summarises the ticket conversation on the ticket page.",
        callers=(("app.services.tickets", "refresh_ticket_ai_summary"),),
    ),
    LLMFeature(
        "ticket_resolution_steps", "Ticket resolution steps",
        "Drafts resolution steps from a resolved ticket.",
        callers=(("app.services.tickets", "refresh_ticket_resolution_steps"),),
    ),
    LLMFeature(
        "ticket_ai_tags", "Ticket AI tags",
        "Generates searchable tags for tickets.",
        callers=(("app.services.tickets", "refresh_ticket_ai_tags"),),
    ),
    LLMFeature(
        "ticket_ai_insights", "Ticket AI insights",
        "Generates the insights panel for a ticket.",
        callers=(("app.services.tickets", "refresh_ticket_ai_insights"),),
    ),
    LLMFeature(
        "ticket_reply_suggestions", "Ticket reply suggestions",
        "Drafts suggested replies for technicians.",
        callers=(("app.services.ticket_reply_suggestions", "suggest_reply"),),
    ),
    LLMFeature(
        "ai_rename_ticket", "AI rename ticket",
        "Automation action that rewrites ticket subjects.",
        callers=(("app.services.modules", "_invoke_ai_rename_ticket"),),
        module_slugs=("ai-rename-ticket",),
    ),
    LLMFeature(
        "ai_classify_ticket", "AI classify ticket",
        "Automation action that classifies tickets.",
        callers=(("app.services.modules", "_invoke_ai_classify_ticket"),),
        module_slugs=("ai-classify-ticket",),
    ),
    LLMFeature(
        "ai_request_missing_info", "AI request missing info",
        "Automation action that asks requesters for missing details.",
        callers=(("app.services.modules", "_invoke_ai_request_missing_info"),),
        module_slugs=("ai-request-missing-info",),
    ),
    LLMFeature(
        "ai_link_related", "AI link related tickets",
        "Automation action that links related tickets.",
        module_slugs=("ai-link-related",),
    ),
    LLMFeature(
        "reprocess_ai", "Reprocess AI",
        "Re-runs the AI ticket processors for a ticket.",
        module_slugs=("reprocess-ai",),
    ),
    LLMFeature(
        "resolution_step_reviews", "Resolution step reviews",
        "Titles grouped resolution steps for review.",
        callers=(("app.services.resolution_step_reviews", "_generate_title"),),
    ),
    LLMFeature(
        "knowledge_base_tags", "Knowledge base AI tags",
        "Generates tags for knowledge base articles.",
        callers=(("app.services.knowledge_base", "_schedule_article_ai_tags"),),
    ),
    LLMFeature(
        "knowledge_base_search", "Knowledge base AI search",
        "Ranks and summarises knowledge base search results.",
        callers=(("app.services.knowledge_base", "search_articles"),),
    ),
    LLMFeature(
        "call_summaries", "Call recording summaries",
        "Summarises call recording transcriptions.",
        callers=(("app.services.call_recordings", "summarize_transcription"),),
    ),
    LLMFeature(
        "shipment_tracking", "Shipment tracking extraction",
        "Reads courier tracking pages for ticket shipment watches.",
        callers=(("app.services.ticket_shipment_tracking", "_extract_snapshot_with_llm"),),
    ),
    LLMFeature(
        "product_descriptions", "Shop product descriptions",
        "Improves shop product descriptions.",
        callers=(("app.services.product_descriptions", "improve_product_description"),),
    ),
    LLMFeature(
        "service_status_tags", "Service status tags",
        "Generates tags for service status entries.",
        callers=(("app.services.tag_generator", "generate_tags_for_service"),),
    ),
    LLMFeature(
        "service_status_lookup", "Service status AI lookup",
        "Checks third-party service status pages.",
        callers=(("app.services.service_status", "run_ai_lookup_for_service"),),
    ),
    LLMFeature(
        "rag_relationships", "RAG relationship evaluation",
        "Evaluates candidate relationships between indexed records.",
        callers=(("app.services.rag_relationships", "evaluate_next_batch"),),
    ),
    LLMFeature(
        "rag_embeddings", "RAG embeddings",
        "Creates embeddings for the RAG index and search queries.",
        callers=(("app.services.rag_index", "embed_text"),),
    ),
    LLMFeature(
        "reporting_ai_query", "Reporting AI query",
        "Builds report queries from plain-English questions.",
        callers=(("app.features.reporting.handlers", "admin_reporting_ai_query"),),
    ),
    LLMFeature(
        "matrix_waiting_assistant", "Chat waiting assistant",
        "Replies to customers waiting in Matrix chat.",
        callers=(("app.services.matrix_ai_waiting_assistant", "_ollama_generate"),),
    ),
)

FEATURES_BY_KEY: dict[str, LLMFeature] = {feature.key: feature for feature in FEATURES}
_FEATURES_BY_CALLER: dict[tuple[str, str], str] = {
    caller: feature.key for feature in FEATURES for caller in feature.callers
}
_FEATURES_BY_MODULE_SLUG: dict[str, str] = {
    slug: feature.key for feature in FEATURES for slug in feature.module_slugs
}

_current_feature: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "llm_usage_feature", default=None
)


def feature_label(key: str) -> str:
    feature = FEATURES_BY_KEY.get(key)
    if feature:
        return feature.label
    if key == UNATTRIBUTED_FEATURE:
        return "Other"
    return key


def feature_for_module_slug(slug: str) -> str | None:
    return _FEATURES_BY_MODULE_SLUG.get(slug)


def infer_feature_from_stack() -> str | None:
    """Return the feature key of the nearest known caller on the stack."""

    frame = sys._getframe(1)
    while frame is not None:
        key = _FEATURES_BY_CALLER.get(
            (frame.f_globals.get("__name__", ""), frame.f_code.co_name)
        )
        if key:
            return key
        frame = frame.f_back
    return None


def feature_for_trigger(slug: str, payload: Mapping[str, Any] | None) -> str | None:
    """Resolve the feature for ``trigger_module(slug, payload)`` at call time.

    Called synchronously from ``trigger_module`` so the caller's stack is
    still available before the module runs in a background task.
    """

    explicit = (payload or {}).get("usage_feature")
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip()
    module_feature = feature_for_module_slug(slug)
    if module_feature:
        return module_feature
    if slug != "ollama":
        return None
    return infer_feature_from_stack() or _current_feature.get()


def current_feature() -> str:
    """Return the feature for an LLM request being sent now."""

    return _current_feature.get() or infer_feature_from_stack() or UNATTRIBUTED_FEATURE


def set_current_feature(key: str | None) -> contextvars.Token[str | None]:
    return _current_feature.set(key)


def reset_current_feature(token: contextvars.Token[str | None]) -> None:
    _current_feature.reset(token)


def estimate_tokens(text: str | None) -> int:
    """Rough token estimate (~4 characters per token) for providers that do not report usage."""

    if not text:
        return 0
    return max(1, math.ceil(len(text) / 4))


def extract_token_usage(payload: Any) -> tuple[int, int] | None:
    """Return ``(input_tokens, output_tokens)`` reported in a provider response.

    Understands Ollama (``prompt_eval_count``/``eval_count``) and
    OpenAI-compatible (``usage.prompt_tokens``/``usage.completion_tokens`` or
    ``input_tokens``/``output_tokens``) payloads.
    """

    if not isinstance(payload, Mapping):
        return None
    if "prompt_eval_count" in payload or "eval_count" in payload:
        return (
            _as_int(payload.get("prompt_eval_count")),
            _as_int(payload.get("eval_count")),
        )
    usage = payload.get("usage")
    if isinstance(usage, Mapping):
        input_tokens = usage.get("prompt_tokens", usage.get("input_tokens"))
        output_tokens = usage.get("completion_tokens", usage.get("output_tokens"))
        if input_tokens is not None or output_tokens is not None:
            return _as_int(input_tokens), _as_int(output_tokens)
    return None


def _as_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def prompt_text(prompt: str | None, messages: Iterable[Any] | None = None) -> str:
    """Flatten a prompt or chat messages into text for token estimation."""

    if messages:
        parts: list[str] = []
        for message in messages:
            if isinstance(message, Mapping):
                content = message.get("content")
                parts.append(content if isinstance(content, str) else str(content or ""))
        if parts:
            return "\n".join(parts)
    return prompt or ""


async def record_usage(
    *,
    feature: str,
    provider: str | None,
    model: str | None,
    status: str,
    usage: tuple[int, int] | None,
    prompt: str | None = None,
    response_text: str | None = None,
    duration_ms: int | None = None,
    webhook_event_id: int | None = None,
) -> None:
    """Record one LLM request. Never raises."""

    estimated = usage is None
    if usage is None:
        input_tokens = estimate_tokens(prompt)
        output_tokens = estimate_tokens(response_text)
    else:
        input_tokens, output_tokens = usage
    try:
        await usage_repo.record_event(
            occurred_at=datetime.now(timezone.utc),
            feature=feature or UNATTRIBUTED_FEATURE,
            provider=provider,
            model=model,
            status=status,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            tokens_estimated=estimated,
            duration_ms=duration_ms,
            webhook_event_id=webhook_event_id,
        )
    except Exception as exc:  # pragma: no cover - usage tracking must not break LLM calls
        logger.warning("Failed to record LLM usage", feature=feature, error=str(exc))


def resolve_date_range(
    start: str | None, end: str | None, *, today: date | None = None
) -> tuple[date, date]:
    """Parse the page's inclusive ``YYYY-MM-DD`` range, defaulting to the last 30 days."""

    today = today or datetime.now(timezone.utc).date()
    end_date = _parse_date(end) or today
    start_date = _parse_date(start) or end_date - timedelta(days=29)
    if start_date > end_date:
        start_date, end_date = end_date, start_date
    return start_date, end_date


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        return None


def _iso_utc(value: Any) -> str | None:
    """Render a stored UTC timestamp as ISO 8601 for ``data-utc`` localisation."""

    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=None).isoformat(timespec="seconds") + "Z"
    return str(value).replace(" ", "T") + "Z"


def _share(part: int, whole: int) -> float:
    return round(part * 100 / whole, 1) if whole else 0.0


async def build_report(start_date: date, end_date: date) -> dict[str, Any]:
    """Aggregate usage for the inclusive UTC date range."""

    start = datetime.combine(start_date, time.min)
    end = datetime.combine(end_date + timedelta(days=1), time.min)
    totals = await usage_repo.summarise(start, end)
    feature_rows = await usage_repo.usage_by_feature(start, end)
    model_rows = await usage_repo.usage_by_model(start, end)
    day_rows = await usage_repo.usage_by_day(start, end)

    total_tokens = totals["input_tokens"] + totals["output_tokens"]
    totals["total_tokens"] = total_tokens

    used = {row["feature"]: row for row in feature_rows}
    features: list[dict[str, Any]] = []
    for key in [feature.key for feature in FEATURES] + sorted(
        key for key in used if key not in FEATURES_BY_KEY
    ):
        row = used.get(key) or {
            "requests": 0, "input_tokens": 0, "output_tokens": 0,
            "failed_requests": 0, "last_used_at": None,
        }
        feature = FEATURES_BY_KEY.get(key)
        tokens = row["input_tokens"] + row["output_tokens"]
        features.append({
            **row,
            "feature": key,
            "label": feature_label(key),
            "description": feature.description if feature else "Requests from an unlisted caller.",
            "last_used_at": _iso_utc(row["last_used_at"]),
            "total_tokens": tokens,
            "share": _share(tokens, total_tokens),
        })
    features.sort(key=lambda item: (-item["total_tokens"], -item["requests"], item["label"]))

    models = []
    for row in model_rows:
        tokens = row["input_tokens"] + row["output_tokens"]
        models.append({**row, "total_tokens": tokens, "share": _share(tokens, total_tokens)})
    models.sort(key=lambda item: -item["total_tokens"])

    by_day = {row["day"]: row for row in day_rows}
    days = []
    current = start_date
    while current <= end_date:
        key = current.isoformat()
        row = by_day.get(key) or {"requests": 0, "input_tokens": 0, "output_tokens": 0}
        days.append({**row, "day": key})
        current += timedelta(days=1)

    return {
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "totals": totals,
        "features": features,
        "models": models,
        "days": days,
    }
