from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

from app.core.config import get_settings
from app.repositories import rag_index as rag_repo
from app.services import rag_relationships
from app.services.sanitization import sanitize_rich_text

# Shared token regex and stop-word set used by both embed_text and BM25 retrieval so
# that indexed vectors and query scoring operate on an identical vocabulary.
_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9_.:#/@+-]{1,}", re.IGNORECASE)
_STOP_WORDS = frozenset({
    "about", "above", "after", "again", "against", "all", "also", "and",
    "any", "are", "because", "been", "before", "being", "below", "between",
    "both", "but", "can", "created", "did", "does", "doing", "don", "down",
    "during", "each", "few", "for", "from", "further", "had", "has", "have",
    "having", "here", "how", "into", "its", "just", "more", "not", "now",
    "off", "once", "only", "other", "our", "out", "over", "own", "same",
    "she", "should", "some", "such", "than", "that", "the", "their", "them",
    "then", "there", "these", "they", "think", "this", "those", "through",
    "too", "under", "until", "usual", "very", "was", "were", "what", "when",
    "where", "which", "while", "who", "why", "will", "with", "you", "your",
})


def embedding_model() -> str:
    return get_settings().rag_embedding_model


def embedding_dimensions() -> int:
    return int(get_settings().rag_embedding_dimensions)


def _chunk_words() -> int:
    return int(get_settings().rag_chunk_words)


def _chunk_overlap_words() -> int:
    overlap = int(get_settings().rag_chunk_overlap_words)
    return min(overlap, max(0, _chunk_words() - 1))


class RagIndexCancelled(Exception):
    """Raised when an administrator requests a running RAG index job to stop."""


@dataclass(slots=True)
class RagDocument:
    source_type: str
    source_id: str
    title: str
    text: str
    url: str | None = None
    company_id: int | None = None
    permission_scope: dict[str, Any] | None = None
    metadata: dict[str, Any] | None = None


def normalise_text(value: Any) -> str:
    sanitized = sanitize_rich_text(str(value or ""))
    return re.sub(r"\s+", " ", sanitized.text_content).strip()


def tokenise(text: str) -> list[str]:
    """Return normalised, stop-word-filtered tokens for both BM25 and embedding.

    Uses the shared ``_TOKEN_RE`` and ``_STOP_WORDS`` so that vectors and BM25
    scores are computed from the exact same vocabulary.
    """
    return [
        t.casefold().strip("#")
        for t in _TOKEN_RE.findall(normalise_text(text))
        if len(t.strip("#")) >= 2 and t.casefold() not in _STOP_WORDS
    ]


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def embed_text(text: str) -> list[float]:
    """Hash-projection embedding with stop-word filtering and bigram augmentation.

    Tokens are extracted using the same regex and stop-word list as BM25 retrieval
    so that both signals operate on an identical vocabulary.  Bigrams (adjacent
    token pairs joined by a null byte) are appended before hashing to give the
    vector phrase-level discrimination (e.g. "password\x00reset" is a distinct
    feature from "password" and "reset" appearing separately).
    """
    vector_size = embedding_dimensions()
    vector = [0.0] * vector_size
    tokens = tokenise(text)
    # range(len(tokens) - 1) is empty for 0 or 1 tokens, so no bigrams are added in
    # those edge cases and the loop below processes only the unigram tokens.
    bigrams = [f"{tokens[i]}\x00{tokens[i + 1]}" for i in range(len(tokens) - 1)]
    for token in tokens + bigrams:
        digest = hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest()
        bucket = int.from_bytes(digest[:4], "big") % vector_size
        sign = 1.0 if digest[4] & 1 else -1.0
        vector[bucket] += sign
    magnitude = math.sqrt(sum(value * value for value in vector))
    if not magnitude:
        return vector
    return [value / magnitude for value in vector]


def cosine_similarity(left: Sequence[float], right: Sequence[float]) -> float:
    return float(sum(a * b for a, b in zip(left, right)))


def chunk_text(text: str) -> list[str]:
    words = normalise_text(text).split()
    if not words:
        return []
    chunks: list[str] = []
    chunk_words = _chunk_words()
    step = max(1, chunk_words - _chunk_overlap_words())
    for start in range(0, len(words), step):
        segment = words[start : start + chunk_words]
        if not segment:
            break
        chunks.append(" ".join(segment))
        if start + chunk_words >= len(words):
            break
    return chunks


def document_from_source(
    source_type: str, item: Mapping[str, Any]
) -> RagDocument | None:
    source_id = (
        item.get("id")
        or item.get("slug")
        or item.get("order_number")
        or item.get("key")
        or item.get("check_id")
        or item.get("user_principal_name")
    )
    if source_id is None:
        return None
    title = str(
        item.get("title")
        or item.get("subject")
        or item.get("name")
        or item.get("order_number")
        or item.get("key")
        or item.get("check_name")
        or source_id
    ).strip()
    body_parts = [title]
    for key in (
        "summary",
        "excerpt",
        "content",
        "description",
        "status",
        "status_message",
        "priority",
        "serial_number",
        "os_name",
        "last_user",
        "details",
        "email",
        "job_title",
        "department",
        "mobile_phone",
        "org_company",
        "manager_name",
        "account_action",
        "po_number",
        "consignment_id",
        "sku",
        "vendor_sku",
    ):
        if item.get(key):
            body_parts.append(str(item[key]))
    for key in ("custom_fields", "assignments", "recommendations"):
        nested = item.get(key)
        if nested:
            body_parts.append(json.dumps(nested, ensure_ascii=False, default=str))
    text = normalise_text("\n".join(body_parts))
    if not text:
        return None
    company_id = item.get("company_id")
    try:
        company_id = int(company_id) if company_id is not None else None
    except (TypeError, ValueError):
        company_id = None
    metadata = {
        key: value for key, value in item.items() if key not in {"permission_scope"}
    }
    return RagDocument(
        source_type=source_type,
        source_id=str(source_id),
        title=title[:500],
        text=text,
        url=item.get("url"),
        company_id=company_id,
        permission_scope=dict(item.get("permission_scope") or {}),
        metadata=metadata,
    )


async def index_document(document: RagDocument) -> int:
    previous = await rag_repo.get_document_by_source(
        document.source_type, document.source_id, embedding_model()
    )
    new_hash = content_hash(document.text)
    content_changed = (
        not previous or str(previous.get("content_hash") or "") != new_hash
    )
    chunks = chunk_text(document.text)
    if not chunks:
        chunks = [normalise_text(document.title)]
    doc_id = await rag_repo.upsert_document(
        {
            "source_type": document.source_type,
            "source_id": document.source_id,
            "company_id": document.company_id,
            "title": document.title,
            "url": document.url,
            "permission_scope_json": json.dumps(
                document.permission_scope or {}, ensure_ascii=False
            ),
            "metadata_json": json.dumps(
                document.metadata or {}, ensure_ascii=False, default=str
            ),
            "content_hash": new_hash,
            "embedding_model": embedding_model(),
        }
    )
    if not content_changed:
        return doc_id
    await rag_repo.replace_chunks(
        doc_id,
        [
            {
                "chunk_index": index,
                "chunk_text": chunk,
                "chunk_hash": content_hash(chunk),
                "embedding_json": json.dumps(embed_text(chunk)),
                "embedding_model": embedding_model(),
                "token_count": len(chunk.split()),
            }
            for index, chunk in enumerate(chunks)
        ],
    )
    await rag_relationships.on_document_indexed(doc_id, content_changed=content_changed)
    return doc_id


def source_keys_from_agent_sources(sources: Mapping[str, Any]) -> dict[str, set[str]]:
    active: dict[str, set[str]] = {}
    for source_type, values in sources.items():
        if source_type == "feature_packs" and isinstance(values, Mapping):
            iterable = (
                (f"feature:{slug}", item)
                for slug, rows in values.items()
                for item in (rows or [])
            )
        else:
            iterable = ((source_type, item) for item in (values or []))
        for normalised_type, item in iterable:
            if not isinstance(item, Mapping):
                continue
            source_id = (
                item.get("id") or item.get("slug") or item.get("key") or item.get("uid")
            )
            if source_id is None:
                continue
            active.setdefault(str(normalised_type), set()).add(str(source_id))
    return active


async def index_agent_sources(
    sources: Mapping[str, Any],
    *,
    job_id: int | None = None,
    cleanup_missing: bool = False,
) -> int:
    indexed = 0
    for source_type, values in sources.items():
        if source_type == "feature_packs" and isinstance(values, Mapping):
            iterable = (
                (f"feature:{slug}", item)
                for slug, rows in values.items()
                for item in (rows or [])
            )
        else:
            iterable = ((source_type, item) for item in (values or []))
        for normalised_type, item in iterable:
            if job_id is not None and await rag_repo.job_stop_requested(job_id):
                raise RagIndexCancelled(f"Index job {job_id} was stopped.")
            if not isinstance(item, Mapping):
                continue
            document = document_from_source(normalised_type, item)
            if document is None:
                continue
            await index_document(document)
            indexed += 1
    if cleanup_missing:
        if job_id is not None and await rag_repo.job_stop_requested(job_id):
            raise RagIndexCancelled(f"Index job {job_id} was stopped.")
        await cleanup_missing_agent_sources(sources)
    return indexed


def candidate_to_source(candidate: Mapping[str, Any]) -> dict[str, Any]:
    metadata = (
        candidate.get("metadata")
        if isinstance(candidate.get("metadata"), Mapping)
        else {}
    )
    item = dict(metadata)
    item.setdefault("id", candidate.get("source_id"))
    item.setdefault("title", candidate.get("title"))
    item.setdefault("summary", candidate.get("excerpt"))
    item.setdefault("url", candidate.get("url"))
    item.setdefault("rag_score", candidate.get("score"))
    return item


async def cleanup_missing_agent_sources(sources: Mapping[str, Any]) -> int:
    """Remove indexed RAG documents whose source records no longer exist.

    The cleanup is scoped to source types present in the current indexing pass so
    a partial future index cannot accidentally purge unrelated RAG assets.
    Related relationship matchings and queue entries are deleted before document
    removal to keep retrieval evidence from pointing at deleted tickets, chats,
    assets, or other source records.
    """

    return await rag_repo.cleanup_missing_documents(
        source_keys_from_agent_sources(sources), embedding_model=embedding_model()
    )
