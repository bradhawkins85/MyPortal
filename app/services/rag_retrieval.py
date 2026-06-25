from __future__ import annotations

import json
import re
from typing import Any, Mapping, Sequence

from app.core.config import get_settings
from app.repositories import rag_index as rag_repo
from app.services import company_access
from app.services.rag_index import cosine_similarity, embed_text, embedding_model
from app.services.rag_permissions import can_access_candidate

def _loads(value: Any, fallback: Any) -> Any:
    if not value:
        return fallback
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value))
    except json.JSONDecodeError:
        return fallback


def _lexical_bonus(query: str, row: Mapping[str, Any]) -> float:
    tokens = {token for token in re.findall(r"[a-z0-9][a-z0-9_.:-]{2,}", query.casefold()) if len(token) >= 3}
    if not tokens:
        return 0.0
    haystack = " ".join(str(row.get(key) or "") for key in ("title", "source_id", "chunk_text")).casefold()
    hits = sum(1 for token in tokens if token in haystack)
    return min(0.25, hits * 0.04)


async def retrieve_candidates(
    query: str,
    user: Mapping[str, Any],
    *,
    active_company_id: int | None = None,
    memberships: Sequence[Mapping[str, Any]] | None = None,
    source_filters: Sequence[str] | None = None,
    limit: int | None = None,
    min_score: float | None = None,
) -> list[dict[str, Any]]:
    query_text = (query or "").strip()
    if not query_text:
        return []
    resolved_memberships = list(memberships or [])
    if not resolved_memberships:
        resolved_memberships = await company_access.list_accessible_companies(user)
    settings = get_settings()
    resolved_limit = int(limit if limit is not None else settings.rag_candidate_limit)
    resolved_min_score = float(min_score if min_score is not None else settings.rag_min_score)
    query_embedding = embed_text(query_text)
    rows = await rag_repo.list_active_chunks(embedding_model=embedding_model(), source_types=source_filters)
    candidates_by_doc: dict[int, dict[str, Any]] = {}
    for row in rows:
        embedding = _loads(row.get("embedding_json"), [])
        if not isinstance(embedding, list):
            continue
        score = cosine_similarity(query_embedding, [float(value) for value in embedding]) + _lexical_bonus(query_text, row)
        if score < resolved_min_score:
            continue
        metadata = _loads(row.get("metadata_json"), {})
        candidate = {
            "document_id": row.get("document_id"),
            "chunk_id": row.get("chunk_id"),
            "source_type": row.get("source_type"),
            "source_id": row.get("source_id"),
            "company_id": row.get("company_id"),
            "title": row.get("title"),
            "url": row.get("url"),
            "excerpt": row.get("chunk_text"),
            "score": round(score, 4),
            "permission_scope": _loads(row.get("permission_scope_json"), {}),
            "metadata": metadata if isinstance(metadata, dict) else {},
        }
        if not can_access_candidate(candidate, user=user, memberships=resolved_memberships):
            continue
        doc_id = int(row.get("document_id") or 0)
        if doc_id not in candidates_by_doc or score > candidates_by_doc[doc_id]["score"]:
            candidates_by_doc[doc_id] = candidate
    candidates = sorted(candidates_by_doc.values(), key=lambda item: item["score"], reverse=True)
    if active_company_id:
        try:
            active_id = int(active_company_id)
        except (TypeError, ValueError):
            active_id = None
        if active_id is not None:
            candidates.sort(key=lambda item: (item.get("company_id") != active_id, -float(item.get("score") or 0)))
    return candidates[: max(1, resolved_limit)]
