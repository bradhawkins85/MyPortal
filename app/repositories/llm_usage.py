"""Storage and aggregation for LLM usage events.

Each row is one request MyPortal sent to an LLM provider. Only metadata is
kept (the MyPortal function, provider, model and token counts); prompts and
responses never reach this table. Timestamps are stored as UTC
``YYYY-MM-DD HH:MM:SS`` strings so range filters compare the same way on
MySQL and the SQLite fallback.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.core.database import db

_TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"


def _format_timestamp(value: datetime) -> str:
    return value.strftime(_TIMESTAMP_FORMAT)


async def record_event(
    *,
    occurred_at: datetime,
    feature: str,
    provider: str | None,
    model: str | None,
    status: str,
    input_tokens: int,
    output_tokens: int,
    tokens_estimated: bool,
    duration_ms: int | None,
    webhook_event_id: int | None,
) -> None:
    await db.execute(
        """INSERT INTO llm_usage_events
        (occurred_at, feature, provider, model, status, input_tokens, output_tokens,
         tokens_estimated, duration_ms, webhook_event_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            _format_timestamp(occurred_at),
            feature[:100],
            (provider or None) and provider[:32],
            (model or None) and model[:255],
            status[:16],
            max(0, int(input_tokens)),
            max(0, int(output_tokens)),
            1 if tokens_estimated else 0,
            duration_ms,
            webhook_event_id,
        ),
    )


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


async def summarise(start: datetime, end: datetime) -> dict[str, int]:
    """Return totals for events with ``start <= occurred_at < end``."""

    row = await db.fetch_one(
        """SELECT COUNT(*) AS requests,
            SUM(input_tokens) AS input_tokens,
            SUM(output_tokens) AS output_tokens,
            SUM(CASE WHEN status <> 'succeeded' THEN 1 ELSE 0 END) AS failed_requests,
            SUM(CASE WHEN tokens_estimated = 1 THEN 1 ELSE 0 END) AS estimated_requests
        FROM llm_usage_events
        WHERE occurred_at >= ? AND occurred_at < ?""",
        (_format_timestamp(start), _format_timestamp(end)),
    ) or {}
    return {
        "requests": _int(row.get("requests")),
        "input_tokens": _int(row.get("input_tokens")),
        "output_tokens": _int(row.get("output_tokens")),
        "failed_requests": _int(row.get("failed_requests")),
        "estimated_requests": _int(row.get("estimated_requests")),
    }


async def usage_by_feature(start: datetime, end: datetime) -> list[dict[str, Any]]:
    rows = await db.fetch_all(
        """SELECT feature, COUNT(*) AS requests,
            SUM(input_tokens) AS input_tokens,
            SUM(output_tokens) AS output_tokens,
            SUM(CASE WHEN status <> 'succeeded' THEN 1 ELSE 0 END) AS failed_requests,
            MAX(occurred_at) AS last_used_at
        FROM llm_usage_events
        WHERE occurred_at >= ? AND occurred_at < ?
        GROUP BY feature""",
        (_format_timestamp(start), _format_timestamp(end)),
    )
    model_rows = await db.fetch_all(
        """SELECT DISTINCT feature, model
        FROM llm_usage_events
        WHERE occurred_at >= ? AND occurred_at < ?""",
        (_format_timestamp(start), _format_timestamp(end)),
    )
    models_by_feature: dict[str, set[str]] = {}
    for row in model_rows or []:
        key = str(row.get("feature") or "")
        model = str(row.get("model") or "").strip() or "Not reported"
        models_by_feature.setdefault(key, set()).add(model)
    return [
        {
            "feature": str(row.get("feature") or ""),
            "models": sorted(models_by_feature.get(str(row.get("feature") or ""), set())),
            "requests": _int(row.get("requests")),
            "input_tokens": _int(row.get("input_tokens")),
            "output_tokens": _int(row.get("output_tokens")),
            "failed_requests": _int(row.get("failed_requests")),
            "last_used_at": row.get("last_used_at"),
        }
        for row in rows or []
    ]


async def usage_by_model(start: datetime, end: datetime) -> list[dict[str, Any]]:
    rows = await db.fetch_all(
        """SELECT provider, model, COUNT(*) AS requests,
            SUM(input_tokens) AS input_tokens,
            SUM(output_tokens) AS output_tokens
        FROM llm_usage_events
        WHERE occurred_at >= ? AND occurred_at < ?
        GROUP BY provider, model""",
        (_format_timestamp(start), _format_timestamp(end)),
    )
    return [
        {
            "provider": row.get("provider"),
            "model": row.get("model"),
            "requests": _int(row.get("requests")),
            "input_tokens": _int(row.get("input_tokens")),
            "output_tokens": _int(row.get("output_tokens")),
        }
        for row in rows or []
    ]


async def usage_by_day(start: datetime, end: datetime) -> list[dict[str, Any]]:
    rows = await db.fetch_all(
        """SELECT DATE(occurred_at) AS day, COUNT(*) AS requests,
            SUM(input_tokens) AS input_tokens,
            SUM(output_tokens) AS output_tokens
        FROM llm_usage_events
        WHERE occurred_at >= ? AND occurred_at < ?
        GROUP BY DATE(occurred_at)
        ORDER BY day""",
        (_format_timestamp(start), _format_timestamp(end)),
    )
    return [
        {
            "day": str(row.get("day")),
            "requests": _int(row.get("requests")),
            "input_tokens": _int(row.get("input_tokens")),
            "output_tokens": _int(row.get("output_tokens")),
        }
        for row in rows or []
    ]
