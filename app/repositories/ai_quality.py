from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from app.core.database import db

PIPELINE_VERSION = "agent-rag-v1"
REASONS = {"incorrect", "uncited", "irrelevant", "incomplete", "unsafe", "other"}


def redact_query(value: str) -> str:
    value = re.sub(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", "[email]", value)
    value = re.sub(r"\b(?:\d[ -]?){7,}\d\b", "[number]", value)
    return value[:500]


def evidence_ids(evidence: dict[str, list[dict[str, Any]]]) -> tuple[list[str], list[str]]:
    identifiers: list[str] = []
    source_types: list[str] = []
    for source_type, items in evidence.items():
        if items:
            source_types.append(source_type)
        for item in items or []:
            source_id = item.get("source_id")
            if source_id is not None:
                identifiers.append(f"{source_type}:{source_id}")
    return identifiers, source_types


async def record_response(*, user_id: int, company_id: int | None, feature: str,
                          query: str, evidence: dict[str, list[dict[str, Any]]],
                          model: str | None, latency_ms: int,
                          confidence_band: str | None, outcome: str) -> int:
    identifiers, source_types = evidence_ids(evidence)
    provider = model.split(":", 1)[0] if model and ":" in model else None
    return await db.execute_insert(
        """INSERT INTO ai_quality_responses
        (user_id, company_id, feature, query_hash, query_redacted,
         evidence_identifiers, source_types, primary_source_type, provider, model, pipeline_version,
         latency_ms, confidence_band, outcome)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (user_id, company_id, feature, hashlib.sha256(query.encode()).hexdigest(),
         redact_query(query), json.dumps(identifiers), json.dumps(source_types),
         source_types[0] if source_types else None, provider, model, PIPELINE_VERSION,
         max(0, latency_ms), confidence_band, outcome),
    )


async def save_feedback(*, response_id: int, user_id: int, rating: str,
                        reason: str | None, comment: str | None) -> None:
    existing = await db.fetch_one(
        "SELECT id FROM ai_quality_feedback WHERE response_id = ? AND user_id = ?",
        (response_id, user_id),
    )
    values = (rating, reason, comment or None)
    if existing:
        await db.execute(
            "UPDATE ai_quality_feedback SET rating = ?, reason = ?, comment = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            values + (int(existing["id"]),),
        )
    else:
        await db.execute(
            "INSERT INTO ai_quality_feedback (response_id, user_id, rating, reason, comment) VALUES (?, ?, ?, ?, ?)",
            (response_id, user_id) + values,
        )


async def response_owned_by(response_id: int, user_id: int) -> bool:
    return bool(await db.fetch_one(
        "SELECT id FROM ai_quality_responses WHERE id = ? AND user_id = ?", (response_id, user_id)
    ))


async def aggregate() -> list[dict[str, Any]]:
    return await db.fetch_all("""SELECT r.feature, r.primary_source_type AS source_type, r.provider, r.confidence_band,
        COUNT(*) AS response_count, SUM(CASE WHEN f.rating = 'up' THEN 1 ELSE 0 END) AS helpful,
        SUM(CASE WHEN f.rating = 'down' THEN 1 ELSE 0 END) AS unhelpful,
        AVG(r.latency_ms) AS average_latency_ms
        FROM ai_quality_responses r LEFT JOIN ai_quality_feedback f ON f.response_id = r.id
        GROUP BY r.feature, r.primary_source_type, r.provider, r.confidence_band
        ORDER BY response_count DESC""")
