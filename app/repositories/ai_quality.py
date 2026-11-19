from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime, timezone
from typing import Any

from app.core.database import db

PIPELINE_VERSION = "agent-rag-v1"
OUTPUT_PIPELINE_VERSION = "output-v1"
REASONS = {"incorrect", "uncited", "irrelevant", "unrelated", "incomplete", "unsafe", "other"}
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
    """Record one AI search answer; every answer is its own rateable output."""
    identifiers, source_types = evidence_ids(evidence)
    provider = model.split(":", 1)[0] if model and ":" in model else None
    return await db.execute_returning_lastrowid(
        """INSERT INTO ai_outputs
        (user_id, company_id, feature, output_hash, query_hash, query_redacted,
         evidence_identifiers, source_types, primary_source_type, provider, model, pipeline_version,
         latency_ms, confidence_band, outcome)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (user_id, company_id, feature, hashlib.sha256(uuid.uuid4().bytes).hexdigest(),
         hashlib.sha256(query.encode()).hexdigest(), redact_query(query), json.dumps(identifiers),
         json.dumps(source_types), source_types[0] if source_types else None, provider, model,
         PIPELINE_VERSION, max(0, latency_ms), confidence_band, outcome),
    )


def _identity_part(value: Any) -> str:
    return "" if value is None else str(value).strip()


_FIND_OUTPUT_SQL = """SELECT id FROM ai_outputs
    WHERE feature = ? AND subject_type = ? AND subject_id = ? AND target_type = ? AND target_id = ?
      AND output_hash = ?"""


async def record_output(*, feature: str, user_id: int | None, company_id: int | None,
                        subject_type: str | None = None, subject_id: str | int | None = None,
                        target_type: str | None = None, target_id: str | int | None = None,
                        output_text: str | None = None, model: str | None = None,
                        prompt_version: str | None = None, latency_ms: int = 0,
                        outcome: str = "answered") -> int:
    """Record one AI output so it can be rated; the same output is recorded once."""
    from app.services.llm_usage import FEATURES_BY_KEY

    if feature not in FEATURES_BY_KEY:
        raise ValueError(f"Unknown AI feature: {feature}")
    identity = (feature, _identity_part(subject_type), _identity_part(subject_id),
                _identity_part(target_type), _identity_part(target_id),
                hashlib.sha256((output_text or "").encode()).hexdigest())
    existing = await db.fetch_one(_FIND_OUTPUT_SQL, identity)
    if existing:
        return int(existing["id"])
    provider = model.split(":", 1)[0] if model and ":" in model else None
    try:
        return await db.execute_returning_lastrowid(
            """INSERT INTO ai_outputs
            (feature, subject_type, subject_id, target_type, target_id, output_hash, user_id, company_id,
             output_text, provider, model, pipeline_version, prompt_version, latency_ms, outcome)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            identity + (user_id, company_id, output_text, provider, model, OUTPUT_PIPELINE_VERSION,
                        prompt_version, max(0, latency_ms), outcome),
        )
    except Exception:
        # Another request recorded the same output between our lookup and
        # insert; the unique identity key rejected ours, so use theirs.
        existing = await db.fetch_one(_FIND_OUTPUT_SQL, identity)
        if existing:
            return int(existing["id"])
        raise


async def get_response(response_id: int) -> dict[str, Any] | None:
    return await db.fetch_one(
        """SELECT id, user_id, company_id, feature, subject_type, subject_id, target_type, target_id,
        output_text, model, prompt_version, created_at FROM ai_outputs WHERE id = ?""",
        (response_id,),
    )


async def save_feedback(*, response_id: int, user_id: int, rating: str | None,
                        reason: str | None, comment: str | None, is_technician: bool = False) -> None:
    """Save a user's vote on one output; ``rating=None`` clears it.

    Callers clear the link-lesson cache (``ai_learning.invalidate_cache``);
    repositories never import services.
    """
    if rating is None:
        await db.execute(
            "DELETE FROM ai_output_feedback WHERE response_id = ? AND user_id = ?",
            (response_id, user_id),
        )
        return
    existing = await db.fetch_one(
        "SELECT id FROM ai_output_feedback WHERE response_id = ? AND user_id = ?",
        (response_id, user_id),
    )
    values = (rating, reason, (comment or "").strip() or None, 1 if is_technician else 0)
    if existing:
        await db.execute(
            "UPDATE ai_output_feedback SET rating = ?, reason = ?, comment = ?, is_technician = ?, "
            "learning_ignored_at = NULL, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
            values + (int(existing["id"]),),
        )
    else:
        await db.execute(
            "INSERT INTO ai_output_feedback (response_id, user_id, rating, reason, comment, is_technician) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (response_id, user_id) + values,
        )


async def response_owned_by(response_id: int, user_id: int) -> bool:
    return bool(await db.fetch_one(
        "SELECT id FROM ai_outputs WHERE id = ? AND user_id = ?", (response_id, user_id)
    ))


async def aggregate() -> list[dict[str, Any]]:
    return await db.fetch_all("""SELECT r.feature, r.primary_source_type AS source_type, r.provider, r.confidence_band,
        COUNT(*) AS response_count, SUM(CASE WHEN f.rating = 'up' THEN 1 ELSE 0 END) AS helpful,
        SUM(CASE WHEN f.rating = 'down' THEN 1 ELSE 0 END) AS unhelpful,
        AVG(r.latency_ms) AS average_latency_ms
        FROM ai_outputs r LEFT JOIN ai_output_feedback f ON f.response_id = r.id
        GROUP BY r.feature, r.primary_source_type, r.provider, r.confidence_band
        ORDER BY response_count DESC""")


async def response_counts(response_id: int) -> dict[str, int]:
    row = await db.fetch_one(
        """SELECT SUM(CASE WHEN rating = 'up' THEN 1 ELSE 0 END) AS helpful,
        SUM(CASE WHEN rating = 'down' THEN 1 ELSE 0 END) AS unhelpful
        FROM ai_output_feedback WHERE response_id = ?""",
        (response_id,),
    ) or {}
    return {"helpful": int(row.get("helpful") or 0), "unhelpful": int(row.get("unhelpful") or 0)}


def _utc_iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value)
        except ValueError:
            return value
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat()
    return str(value)


async def aggregate_by_feature() -> list[dict[str, Any]]:
    """Outputs and votes per AI feature, worst-rated first."""
    rows = await db.fetch_all("""SELECT r.feature, COUNT(DISTINCT r.id) AS outputs,
        SUM(CASE WHEN f.rating = 'up' THEN 1 ELSE 0 END) AS helpful,
        SUM(CASE WHEN f.rating = 'down' THEN 1 ELSE 0 END) AS unhelpful,
        SUM(CASE WHEN f.id IS NOT NULL AND f.is_technician = 1 THEN 1 ELSE 0 END) AS technician_votes,
        SUM(CASE WHEN f.id IS NOT NULL AND f.is_technician = 0 THEN 1 ELSE 0 END) AS customer_votes,
        MAX(f.updated_at) AS last_rated_at
        FROM ai_outputs r LEFT JOIN ai_output_feedback f ON f.response_id = r.id
        GROUP BY r.feature""")
    reasons = await db.fetch_all("""SELECT r.feature, f.reason, COUNT(*) AS votes
        FROM ai_output_feedback f JOIN ai_outputs r ON r.id = f.response_id
        WHERE f.rating = 'down' AND f.reason IS NOT NULL
        GROUP BY r.feature, f.reason""")
    top_reason: dict[str, tuple[int, str]] = {}
    for row in reasons:
        candidate = (int(row["votes"]), str(row["reason"]))
        if candidate > top_reason.get(row["feature"], (0, "")):
            top_reason[row["feature"]] = candidate
    groups = []
    for row in rows:
        feature = str(row["feature"])
        groups.append({
            "feature": feature,
            "outputs": int(row["outputs"] or 0),
            "helpful": int(row["helpful"] or 0),
            "unhelpful": int(row["unhelpful"] or 0),
            "technician_votes": int(row["technician_votes"] or 0),
            "customer_votes": int(row["customer_votes"] or 0),
            "top_reason": top_reason[feature][1] if feature in top_reason else None,
            "last_rated_at": _utc_iso(row["last_rated_at"]),
        })
    groups.sort(key=lambda group: (-group["unhelpful"], -group["outputs"], group["feature"]))
    return groups


async def rating_overview(*, since: datetime) -> dict[str, Any]:
    cutoff = since.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    row = await db.fetch_one("""SELECT COUNT(*) AS ratings,
        SUM(CASE WHEN f.rating = 'up' THEN 1 ELSE 0 END) AS helpful,
        COUNT(DISTINCT r.feature) AS features
        FROM ai_output_feedback f JOIN ai_outputs r ON r.id = f.response_id
        WHERE f.updated_at >= ?""", (cutoff,)) or {}
    ratings = int(row.get("ratings") or 0)
    return {
        "ratings_since": ratings,
        "helpful_pct": round(100 * int(row.get("helpful") or 0) / ratings) if ratings else None,
        "features_rated": int(row.get("features") or 0),
    }


LINK_FEATURE = "rag_relationships"


async def list_link_votes(*, since: datetime | None = None) -> list[dict[str, Any]]:
    """Technician votes on linked items that MyPortal may learn from (newest window only)."""
    cutoff = (since or datetime(1970, 1, 1, tzinfo=timezone.utc)).astimezone(timezone.utc)
    rows = await db.fetch_all("""SELECT o.id AS output_id, o.subject_type, o.subject_id, o.target_type,
        o.target_id, o.subject_embedding_json, o.subject_embedding_model, f.rating, f.updated_at AS voted_at
        FROM ai_outputs o JOIN ai_output_feedback f ON f.response_id = o.id
        WHERE o.feature = ? AND o.target_type <> '' AND f.is_technician = 1 AND f.learning_ignored_at IS NULL
          AND f.updated_at >= ?
        ORDER BY f.id""", (LINK_FEATURE, cutoff.strftime("%Y-%m-%d %H:%M:%S")))
    return [dict(row) for row in rows]


async def store_subject_embedding(output_id: int, embedding: list[float], model: str) -> None:
    await db.execute(
        "UPDATE ai_outputs SET subject_embedding_json = ?, subject_embedding_model = ? WHERE id = ?",
        (json.dumps(embedding), model, output_id),
    )


async def set_link_learning_ignored(target_type: str, target_id: str, *, ignored: bool) -> int:
    """Stop (or resume) learning from technician votes on one linked item.

    ``updated_at = updated_at`` keeps each vote's time: MariaDB would otherwise
    stamp it ON UPDATE, restarting the vote's decay.
    """
    if ignored:
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        return await db.execute_rowcount(
            """UPDATE ai_output_feedback SET learning_ignored_at = ?, updated_at = updated_at
            WHERE learning_ignored_at IS NULL AND is_technician = 1 AND response_id IN (
                SELECT id FROM ai_outputs WHERE feature = ? AND target_type = ? AND target_id = ?)""",
            (stamp, LINK_FEATURE, target_type, target_id),
        )
    return await db.execute_rowcount(
        """UPDATE ai_output_feedback SET learning_ignored_at = NULL, updated_at = updated_at
        WHERE learning_ignored_at IS NOT NULL AND is_technician = 1 AND response_id IN (
            SELECT id FROM ai_outputs WHERE feature = ? AND target_type = ? AND target_id = ?)""",
        (LINK_FEATURE, target_type, target_id),
    )


async def learned_links() -> list[dict[str, Any]]:
    """Each linked item technicians voted on, with what MyPortal learned from it."""
    rows = await db.fetch_all("""SELECT o.target_type, o.target_id,
        (SELECT MAX(d.title) FROM rag_documents d
         WHERE d.source_type = o.target_type AND d.source_id = o.target_id) AS title,
        SUM(CASE WHEN f.rating = 'down' THEN 1 ELSE 0 END) AS unhelpful,
        SUM(CASE WHEN f.rating = 'up' THEN 1 ELSE 0 END) AS helpful,
        COUNT(DISTINCT o.subject_id) AS tickets,
        COUNT(*) AS votes,
        SUM(CASE WHEN f.learning_ignored_at IS NOT NULL THEN 1 ELSE 0 END) AS ignored_votes,
        MAX(f.updated_at) AS last_voted_at
        FROM ai_outputs o JOIN ai_output_feedback f ON f.response_id = o.id
        WHERE o.feature = ? AND o.target_type <> '' AND f.is_technician = 1
        GROUP BY o.target_type, o.target_id""", (LINK_FEATURE,))
    links = [{
        "target_type": str(row["target_type"]),
        "target_id": str(row["target_id"]),
        "title": row["title"],
        "unhelpful": int(row["unhelpful"] or 0),
        "helpful": int(row["helpful"] or 0),
        "tickets": int(row["tickets"] or 0),
        "ignored": int(row["ignored_votes"] or 0) == int(row["votes"] or 0),
        "last_voted_at": _utc_iso(row["last_voted_at"]),
    } for row in rows]
    links.sort(key=lambda link: (-link["unhelpful"], -link["helpful"], link["target_type"], link["target_id"]))
    return links
