from __future__ import annotations

import json
import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from app.core.config import get_settings
from app.core.logging import log_info
from app.repositories import rag_index as rag_repo
from app.services import company_access
from app.services.rag_index import (
    cosine_similarity,
    embed_text,
    embedding_model,
    normalise_text,
)
from app.services.rag_permissions import can_access_candidate

_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9_.:#/@+-]{1,}", re.IGNORECASE)
_EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)
_ID_RE = re.compile(r"(?:#\d{3,}|\b\d{4,}\b|\b[A-Z]{2,}\d{2,}\b)", re.IGNORECASE)
_HOST_RE = re.compile(r"\b(?:[a-z0-9-]+\.)+[a-z]{2,}\b", re.IGNORECASE)
_NAME_RE = re.compile(r"\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+\b")
_STOP_WORDS = {
    "about",
    "above",
    "after",
    "again",
    "against",
    "all",
    "also",
    "and",
    "any",
    "are",
    "because",
    "been",
    "before",
    "being",
    "below",
    "between",
    "both",
    "but",
    "can",
    "created",
    "did",
    "does",
    "doing",
    "don",
    "down",
    "during",
    "each",
    "few",
    "for",
    "from",
    "further",
    "had",
    "has",
    "have",
    "having",
    "here",
    "how",
    "into",
    "its",
    "just",
    "more",
    "not",
    "now",
    "off",
    "once",
    "only",
    "other",
    "our",
    "out",
    "over",
    "own",
    "same",
    "she",
    "should",
    "some",
    "such",
    "than",
    "that",
    "the",
    "their",
    "them",
    "then",
    "there",
    "these",
    "they",
    "think",
    "this",
    "those",
    "through",
    "too",
    "under",
    "until",
    "usual",
    "very",
    "was",
    "were",
    "what",
    "when",
    "where",
    "which",
    "while",
    "who",
    "why",
    "will",
    "with",
    "you",
    "your",
}
_QUERY_EXPANSIONS = {
    "cmos": (
        "cmos battery",
        "rtc battery",
        "cr2032",
        "bios battery",
        "motherboard battery",
    ),
    "trello": ("task", "board", "card"),
    "ebay": ("listing", "seller", "purchase"),
    "smartcard": ("smart card", "smartcard-inline"),
}
_SOURCE_THRESHOLDS = {
    "knowledge_base": 0.42,
    "chats": 0.35,
    "tickets": 0.35,
    "products": 0.55,
    "best_practices": 0.60,
    "assets": 0.40,
}
_SOURCE_WEIGHTS = {
    "tickets": 1.00,
    "chats": 0.95,
    "knowledge_base": 0.90,
    "assets": 0.80,
    "products": 0.60,
    "best_practices": 0.50,
}
_SOURCE_LIMITS = {
    "knowledge_base": 5,
    "chats": 5,
    "tickets": 5,
    "products": 2,
    "assets": 2,
    "best_practices": 2,
}
_PRODUCT_TERMS = {
    "product",
    "sku",
    "price",
    "buy",
    "purchase",
    "compatible",
    "vendor",
    "model",
}


@dataclass(slots=True)
class QueryProfile:
    original: str
    cleaned: str
    expanded: str
    tokens: list[str]
    entities: dict[str, list[str]]
    intents: list[str]


def _loads(value: Any, fallback: Any) -> Any:
    if not value:
        return fallback
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(str(value))
    except json.JSONDecodeError:
        return fallback


def _tokens(text: str) -> list[str]:
    return [
        token.casefold().strip("#")
        for token in _TOKEN_RE.findall(normalise_text(text))
        if len(token.strip("#")) >= 2 and token.casefold() not in _STOP_WORDS
    ]


def _extract_entities(query: str) -> dict[str, list[str]]:
    entities = {
        "emails": _EMAIL_RE.findall(query),
        "ids": _ID_RE.findall(query),
        "hostnames": [h for h in _HOST_RE.findall(query) if "@" not in h],
        "names": _NAME_RE.findall(query),
        "products": [],
    }
    lowered = query.casefold()
    for phrase in (
        "cmos battery",
        "thinkcentre",
        "thinkpad",
        "trello",
        "ebay",
        "smartcard",
        "smartcard-inline",
    ):
        if phrase in lowered:
            entities["products"].append(phrase)
    return {
        key: sorted(set(values), key=str.casefold)
        for key, values in entities.items()
        if values
    }


def _classify_intents(
    tokens: Sequence[str], entities: Mapping[str, Sequence[str]]
) -> list[str]:
    token_set = set(tokens)
    intents: list[str] = []
    if entities.get("ids") or {"ticket", "support", "issue"} & token_set:
        intents.append("Support Ticket")
    if {"chat", "message", "trello", "card", "board"} & token_set or entities.get(
        "names"
    ):
        intents.append("Chat")
    if _PRODUCT_TERMS & token_set or entities.get("products"):
        intents.append("Product Lookup")
    if {"asset", "hostname", "serial"} & token_set or entities.get("hostnames"):
        intents.append("Asset")
    if {"kb", "article", "guide", "howto", "knowledge"} & token_set:
        intents.append("Knowledge Base")
    if {"best", "practice", "secure", "policy"} & token_set:
        intents.append("Best Practice")
    return intents or ["Mixed"]


def _profile_query(query: str) -> QueryProfile:
    settings = get_settings()
    entities = _extract_entities(query) if bool(settings.rag_entity_extraction) else {}
    tokens = _tokens(query)
    expansions: list[str] = []
    if not bool(settings.rag_query_expansion):
        expansions = []
    else:
        for token in tokens:
            expansions.extend(_QUERY_EXPANSIONS.get(token, ()))
        for phrase in entities.get("products", []):
            for token in phrase.split():
                expansions.extend(_QUERY_EXPANSIONS.get(token.casefold(), ()))
    cleaned = " ".join(dict.fromkeys(tokens))
    expanded = " ".join(
        part for part in (cleaned, " ".join(dict.fromkeys(expansions))) if part
    ).strip()
    return QueryProfile(
        query,
        cleaned or query,
        expanded or query,
        tokens,
        entities,
        _classify_intents(tokens, entities),
    )


def _haystack(row: Mapping[str, Any], metadata: Mapping[str, Any]) -> str:
    metadata_text = (
        json.dumps(metadata, ensure_ascii=False, default=str) if metadata else ""
    )
    return (
        " ".join(
            str(row.get(k) or "")
            for k in ("title", "source_id", "chunk_text", "source_type")
        )
        + " "
        + metadata_text
    )


def _bm25_scores(
    rows: Sequence[Mapping[str, Any]],
    query_terms: Sequence[str],
    metadatas: Mapping[int, Mapping[str, Any]],
) -> dict[int, float]:
    if not rows or not query_terms:
        return {}
    docs: dict[int, list[str]] = {}
    for row in rows:
        key = int(row.get("chunk_id") or 0)
        docs[key] = _tokens(_haystack(row, metadatas.get(key, {})))
    avgdl = sum(len(v) for v in docs.values()) / max(1, len(docs))
    df = Counter(
        term for term in set(query_terms) for doc in docs.values() if term in doc
    )
    scores: dict[int, float] = {}
    k1, b = 1.5, 0.75
    for key, doc_terms in docs.items():
        if not doc_terms:
            continue
        counts = Counter(doc_terms)
        raw = 0.0
        for term in query_terms:
            freq = counts.get(term, 0)
            if not freq:
                continue
            idf = math.log(1 + (len(docs) - df[term] + 0.5) / (df[term] + 0.5))
            raw += (
                idf
                * (freq * (k1 + 1))
                / (freq + k1 * (1 - b + b * len(doc_terms) / max(avgdl, 1)))
            )
        scores[key] = raw
    peak = max(scores.values(), default=0.0)
    return {key: (value / peak if peak else 0.0) for key, value in scores.items()}


def _metadata_boost(
    profile: QueryProfile, row: Mapping[str, Any], metadata: Mapping[str, Any]
) -> float:
    haystack = _haystack(row, metadata).casefold()
    boost = 0.0
    for values in profile.entities.values():
        for value in values:
            if str(value).casefold() in haystack:
                boost += 0.08
    exact_hits = sum(1 for token in set(profile.tokens) if token in haystack)
    if exact_hits:
        boost += min(0.15, exact_hits * 0.03)
    return min(1.0, boost)


def _threshold(source_type: str, fallback: float) -> float:
    return max(fallback, _SOURCE_THRESHOLDS.get(source_type, fallback))


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
    settings = get_settings()
    profile = _profile_query(query_text)
    resolved_limit = int(limit if limit is not None else settings.rag_candidate_limit)
    resolved_min_score = float(
        min_score if min_score is not None else settings.rag_min_score
    )
    resolved_memberships = list(
        memberships or []
    ) or await company_access.list_accessible_companies(user)
    query_embedding = embed_text(profile.expanded)
    rows = await rag_repo.list_active_chunks(
        embedding_model=embedding_model(), source_types=source_filters
    )
    metadata_by_chunk = {
        int(r.get("chunk_id") or 0): (_loads(r.get("metadata_json"), {}) or {})
        for r in rows
    }
    bm25 = _bm25_scores(
        rows,
        profile.tokens
        + [
            t
            for values in profile.entities.values()
            for v in values
            for t in _tokens(v)
        ],
        metadata_by_chunk,
    )
    candidates_by_doc: dict[int, dict[str, Any]] = {}
    discarded = 0
    per_source_counts: defaultdict[str, int] = defaultdict(int)
    for row in rows:
        chunk_id = int(row.get("chunk_id") or 0)
        metadata = metadata_by_chunk.get(chunk_id, {})
        embedding = _loads(row.get("embedding_json"), [])
        if not isinstance(embedding, list):
            continue
        semantic = max(
            0.0, cosine_similarity(query_embedding, [float(v) for v in embedding])
        )
        lexical = bm25.get(chunk_id, 0.0)
        meta = _metadata_boost(profile, row, metadata)
        source_type = str(row.get("source_type") or "")
        final = (
            (float(settings.rag_vector_weight) * semantic)
            + (float(settings.rag_bm25_weight) * lexical)
            + (float(settings.rag_metadata_weight) * meta)
        ) * float(_SOURCE_WEIGHTS.get(source_type, 0.75))
        if final < _threshold(source_type, resolved_min_score):
            discarded += 1
            continue
        candidate = {
            "document_id": row.get("document_id"),
            "chunk_id": chunk_id,
            "source_type": source_type,
            "source_id": row.get("source_id"),
            "company_id": row.get("company_id"),
            "title": row.get("title"),
            "url": row.get("url"),
            "excerpt": row.get("chunk_text"),
            "score": round(final, 4),
            "semantic_score": round(semantic, 4),
            "bm25_score": round(lexical, 4),
            "metadata_score": round(meta, 4),
            "permission_scope": _loads(row.get("permission_scope_json"), {}),
            "metadata": metadata if isinstance(metadata, dict) else {},
            "entities": profile.entities,
            "intents": profile.intents,
        }
        if not can_access_candidate(
            candidate, user=user, memberships=resolved_memberships
        ):
            discarded += 1
            continue
        if active_company_id and candidate.get("company_id") not in (
            None,
            active_company_id,
        ):
            final *= 0.95
        doc_id = int(row.get("document_id") or 0)
        if (
            doc_id not in candidates_by_doc
            or final > candidates_by_doc[doc_id]["score"]
        ):
            candidates_by_doc[doc_id] = candidate
    candidates = sorted(
        candidates_by_doc.values(), key=lambda item: item["score"], reverse=True
    )
    diverse: list[dict[str, Any]] = []
    for candidate in candidates:
        st = candidate.get("source_type") or ""
        if per_source_counts[st] >= _SOURCE_LIMITS.get(st, 3):
            continue
        per_source_counts[st] += 1
        diverse.append(candidate)
        if len(diverse) >= max(1, resolved_limit):
            break
    log_info(
        "RAG hybrid retrieval completed",
        query=query_text,
        entities=profile.entities,
        expanded_query=profile.expanded,
        intents=profile.intents,
        candidates_sent=len(diverse),
        discarded=discarded,
    )
    return diverse
