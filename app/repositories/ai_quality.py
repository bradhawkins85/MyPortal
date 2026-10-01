from __future__ import annotations

import hashlib
import json
from typing import Any

from app.core.database import db

PIPELINE_VERSION = "agent-rag-v1"
REASONS = {"incorrect", "uncited", "irrelevant", "incomplete", "unsafe", "other"}
_MAX_REDACTION_INPUT_CHARS = 5000
_LOCAL_EMAIL_CHARS = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789._+-")
_DOMAIN_EMAIL_CHARS = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-")
_EMAIL_TOKEN_CHARS = _LOCAL_EMAIL_CHARS | {"@"}


def _is_word_char(char: str) -> bool:
    return char.isalnum() or char == "_"


def _is_email_token(token: str) -> bool:
    if token.count("@") != 1:
        return False
    local, domain = token.split("@", 1)
    if not local or not domain:
        return False
    if any(char not in _LOCAL_EMAIL_CHARS for char in local):
        return False
    if any(char not in _DOMAIN_EMAIL_CHARS for char in domain):
        return False
    label, separator, tld = domain.rpartition(".")
    if not separator or not label:
        return False
    return len(tld) >= 2 and tld.isalpha()


def _redact_emails(value: str) -> str:
    parts: list[str] = []
    i = 0
    length = len(value)
    while i < length:
        char = value[i]
        if char not in _EMAIL_TOKEN_CHARS:
            parts.append(char)
            i += 1
            continue
        start = i
        while i < length and value[i] in _EMAIL_TOKEN_CHARS:
            i += 1
        token = value[start:i]
        core = token.rstrip(".")
        trailing = token[len(core):]
        if core and _is_email_token(core):
            parts.append("[email]")
            parts.append(trailing)
        else:
            parts.append(token)
    return "".join(parts)


def _redact_number_sequences(value: str) -> str:
    parts: list[str] = []
    i = 0
    length = len(value)
    while i < length:
        char = value[i]
        prev = value[i - 1] if i > 0 else ""
        if not char.isdigit() or (prev and _is_word_char(prev)):
            parts.append(char)
            i += 1
            continue
        j = i
        digit_count = 0
        last_was_separator = False
        while j < length:
            current = value[j]
            if current.isdigit():
                digit_count += 1
                last_was_separator = False
                j += 1
                continue
            if current in " -" and not last_was_separator:
                last_was_separator = True
                j += 1
                continue
            break
        if last_was_separator:
            j -= 1
        next_char = value[j] if j < length else ""
        if digit_count >= 8 and (not next_char or not _is_word_char(next_char)):
            parts.append("[number]")
            i = j
            continue
        parts.append(char)
        i += 1
    return "".join(parts)


def redact_query(value: str) -> str:
    value = value[:_MAX_REDACTION_INPUT_CHARS]
    value = _redact_emails(value)
    value = _redact_number_sequences(value)
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
