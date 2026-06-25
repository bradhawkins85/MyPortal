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


def embedding_model() -> str:
    return get_settings().rag_embedding_model


def embedding_dimensions() -> int:
    return int(get_settings().rag_embedding_dimensions)


def _chunk_words() -> int:
    return int(get_settings().rag_chunk_words)


def _chunk_overlap_words() -> int:
    overlap = int(get_settings().rag_chunk_overlap_words)
    return min(overlap, max(0, _chunk_words() - 1))


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


def content_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def embed_text(text: str) -> list[float]:
    vector_size = embedding_dimensions()
    vector = [0.0] * vector_size
    tokens = re.findall(r"[a-z0-9][a-z0-9_.:-]{1,}", normalise_text(text).casefold())
    for token in tokens:
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
    content_changed = not previous or str(previous.get("content_hash") or "") != new_hash
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


async def index_agent_sources(sources: Mapping[str, Any]) -> int:
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
            if not isinstance(item, Mapping):
                continue
            document = document_from_source(normalised_type, item)
            if document is None:
                continue
            await index_document(document)
            indexed += 1
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
