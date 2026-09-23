from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import dataclass
from typing import Any, Mapping, Sequence

import httpx

from app.core.config import get_settings
from app.core.logging import log_info, log_warning
from app.repositories import rag_index as rag_repo
from app.services import rag_relationships
from app.services.rag_urls import canonical_source_url
from app.services.sanitization import sanitize_rich_text

# Shared token regex and stop-word set used by both embed_text and BM25 retrieval so
# that indexed vectors and query scoring operate on an identical vocabulary.
_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9_.:#/@+-]{1,}", re.IGNORECASE)
_STOP_WORDS = frozenset(
    {
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
)


_EMBEDDING_ALGORITHM = "myportal-embedding-v3"


def embedding_model() -> str:
    """Return the persisted compatibility fingerprint for the active vectors."""
    settings = get_settings()
    components = {
        "algorithm": _EMBEDDING_ALGORITHM,
        "provider": settings.rag_embedding_provider.strip().lower(),
        "model": settings.rag_embedding_model.strip(),
        "dimensions": int(settings.rag_embedding_dimensions),
    }
    digest = hashlib.sha256(
        json.dumps(components, sort_keys=True).encode("utf-8")
    ).hexdigest()[:16]
    return f"{components['provider']}:{components['model']}:{components['dimensions']}:{digest}"


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
    sections: list[tuple[str, str]] | None = None


# Ordered from the most explicit/common identifier to source-specific fallbacks.
# Every ingestion and cleanup path must use this list through ``source_identity``.
_SOURCE_ID_FIELDS = (
    "id",
    "slug",
    "order_number",
    "key",
    "check_id",
    "user_principal_name",
    "uid",
)


def source_identity(
    source_type: str, item: Mapping[str, Any]
) -> tuple[str, str] | None:
    """Return the canonical, stable identity for a source record.

    Empty values and structured values are not identifiers.  In particular, do
    not stringify dictionaries or lists: their representation is neither a
    provider contract nor a stable key suitable for stale-row cleanup.
    """

    normalised_type = str(source_type).strip()
    if not normalised_type:
        return None
    for field in _SOURCE_ID_FIELDS:
        value = item.get(field)
        if value is None or isinstance(value, (bool, Mapping, list, tuple, set)):
            continue
        source_id = str(value).strip()
        if source_id:
            return normalised_type, source_id
    return None


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


def _lexical_embedding(text: str) -> list[float]:
    """Hash-projection vector used only by the explicit lexical provider.

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


async def embed_text(text: str) -> list[float]:
    """Embed text using Ollama/OpenAI-compatible APIs or the local lexical fallback."""
    settings = get_settings()
    provider = settings.rag_embedding_provider.strip().lower()
    if provider == "lexical":
        return _lexical_embedding(text)
    base_url = settings.rag_embedding_base_url.rstrip("/")
    headers: dict[str, str] = {}
    if settings.rag_embedding_api_key:
        headers["Authorization"] = f"Bearer {settings.rag_embedding_api_key}"
    if provider == "ollama":
        url = f"{base_url}/api/embed"
        payload: dict[str, Any] = {
            "model": settings.rag_embedding_model,
            "input": text,
            "dimensions": int(settings.rag_embedding_dimensions),
        }
    else:
        url = f"{base_url}/v1/embeddings"
        payload = {
            "model": settings.rag_embedding_model,
            "input": text,
            "dimensions": int(settings.rag_embedding_dimensions),
        }
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(url, json=payload, headers=headers)
        response.raise_for_status()
        body = response.json()
    try:
        vector = (
            body["embeddings"][0]
            if provider == "ollama"
            else body["data"][0]["embedding"]
        )
        result = [float(value) for value in vector]
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise ValueError("Embedding provider returned an invalid response") from exc
    expected = int(settings.rag_embedding_dimensions)
    if len(result) != expected:
        raise ValueError(
            f"Embedding dimension mismatch: configured {expected}, provider returned {len(result)}"
        )
    magnitude = math.sqrt(sum(value * value for value in result))
    return [value / magnitude for value in result] if magnitude else result


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


def _labelled_section(label: str, value: Any) -> tuple[str, str] | None:
    text = normalise_text(value)
    return (label, text) if text else None


def _ticket_document(
    source_type: str, source_id: str, item: Mapping[str, Any]
) -> RagDocument | None:
    title = str(
        item.get("subject") or item.get("title") or f"Ticket #{source_id}"
    ).strip()
    sections: list[tuple[str, str]] = []
    for label, value in (
        ("Subject", title),
        ("Description", item.get("description")),
        ("Category", item.get("category") or item.get("category_name")),
        ("Module", item.get("module") or item.get("module_slug")),
        ("Status", item.get("status")),
        ("Priority", item.get("priority")),
        ("Resolution steps", item.get("resolution_steps")),
    ):
        section = _labelled_section(label, value)
        if section:
            sections.append(section)
    tags = [
        *(item.get("ai_tags") or []),
        *(item.get("manual_tags") or item.get("tags") or []),
    ]
    if tags:
        sections.append(("Tags", ", ".join(str(tag) for tag in tags)))
    error_codes = item.get("error_codes") or []
    if isinstance(error_codes, str):
        error_codes = [error_codes]
    if error_codes:
        sections.append(("Error codes", ", ".join(str(code) for code in error_codes)))
    for index, reply in enumerate(item.get("replies") or [], start=1):
        if not isinstance(reply, Mapping):
            continue
        kind = "Internal note" if reply.get("is_internal") else "Reply"
        author = reply.get("author_display_name") or reply.get("author_email")
        label = f"{kind} {index}" + (f" by {author}" if author else "")
        section = _labelled_section(label, reply.get("body") or reply.get("content"))
        if section:
            sections.append(section)
    names = []
    for attachment in item.get("attachments") or []:
        if isinstance(attachment, Mapping):
            names.append(
                attachment.get("original_filename") or attachment.get("filename")
            )
        else:
            names.append(attachment)
    if names:
        sections.append(("Attachments", ", ".join(str(name) for name in names if name)))
    identifiers: list[str] = []
    for asset in item.get("linked_assets") or item.get("assets") or []:
        if not isinstance(asset, Mapping):
            identifiers.append(str(asset))
            continue
        for key in (
            "asset_id",
            "name",
            "serial_number",
            "tactical_asset_id",
            "tray_device_uid",
        ):
            if asset.get(key):
                identifiers.append(f"{key}={asset[key]}")
    if identifiers:
        sections.append(("Linked assets", ", ".join(identifiers)))
    return _finish_source_document(source_type, source_id, title, item, sections)


def _knowledge_base_document(
    source_type: str, source_id: str, item: Mapping[str, Any]
) -> RagDocument | None:
    title = str(item.get("title") or source_id).strip()
    sections: list[tuple[str, str]] = []
    for label, value in (
        ("Title", title),
        ("Summary", item.get("summary")),
        ("Article", item.get("content")),
    ):
        section = _labelled_section(label, value)
        if section:
            sections.append(section)
    for index, article_section in enumerate(item.get("sections") or [], start=1):
        if not isinstance(article_section, Mapping):
            continue
        heading = article_section.get("heading") or f"Section {index}"
        section = _labelled_section(str(heading), article_section.get("content"))
        if section:
            sections.append(section)
    tags = [*(item.get("ai_tags") or []), *(item.get("manual_ai_tags") or [])]
    if tags:
        sections.append(("Tags", ", ".join(str(tag) for tag in tags)))
    return _finish_source_document(source_type, source_id, title, item, sections)


def _finish_source_document(
    source_type: str,
    source_id: str,
    title: str,
    item: Mapping[str, Any],
    sections: list[tuple[str, str]],
) -> RagDocument | None:
    text = "\n\n".join(f"[{label}]\n{value}" for label, value in sections)
    if not text:
        return None
    company_id = item.get("company_id")
    try:
        company_id = int(company_id) if company_id is not None else None
    except (TypeError, ValueError):
        company_id = None
    permission_scope = _permission_scope_for_source(source_type, item)
    if permission_scope is None:
        return None
    identifiers = {
        key: item.get(key)
        for key in ("id", "slug", "ticket_number", "external_reference")
        if item.get(key) is not None
    }
    metadata = {
        key: value
        for key, value in item.items()
        if key
        not in {"permission_scope", "description", "content", "sections", "replies"}
    }
    metadata["identifiers"] = identifiers
    metadata["section_labels"] = [label for label, _ in sections]
    return RagDocument(
        source_type,
        source_id,
        title[:500],
        text,
        item.get("url"),
        company_id,
        permission_scope,
        metadata,
        sections,
    )


def document_from_source(
    source_type: str, item: Mapping[str, Any]
) -> RagDocument | None:
    identity = source_identity(source_type, item)
    if identity is None:
        return None
    normalised_type, source_id = identity
    if normalised_type in {"tickets", "ticket_comments"}:
        return _ticket_document(normalised_type, source_id, item)
    if normalised_type == "knowledge_base":
        return _knowledge_base_document(normalised_type, source_id, item)
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
    permission_scope = _permission_scope_for_source(normalised_type, item)
    if permission_scope is None:
        return None
    metadata = {
        key: value for key, value in item.items() if key not in {"permission_scope"}
    }
    return RagDocument(
        source_type=normalised_type,
        source_id=source_id,
        title=title[:500],
        text=text,
        url=item.get("url"),
        company_id=company_id,
        permission_scope=permission_scope,
        metadata=metadata,
    )


def _ids(item: Mapping[str, Any], *keys: str) -> list[int]:
    values: list[Any] = []
    for key in keys:
        value = item.get(key)
        values.extend(value if isinstance(value, (list, tuple, set)) else [value])
    result: set[int] = set()
    for value in values:
        try:
            if value is not None:
                result.add(int(value))
        except (TypeError, ValueError):
            continue
    return sorted(result)


def _scope(
    visibility: str,
    *,
    companies: list[int] | None = None,
    users: list[int] | None = None,
    required_any: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "version": 1,
        "visibility": visibility,
        "company_ids": companies or [],
        "user_ids": users or [],
        "required_any": required_any or [],
    }


def _permission_scope_for_source(
    source_type: str, item: Mapping[str, Any]
) -> dict[str, Any] | None:
    """Build the complete access policy stored beside each shared document."""
    if source_type == "ticket_comments" and item.get("internal_only") is True:
        return _scope("super_admin")
    if source_type == "knowledge_base":
        visibility = str(item.get("article_permission_scope") or "")
        if visibility == "anonymous":
            return _scope("anonymous")
        if visibility == "super_admin":
            return _scope("super_admin")
        if visibility == "user":
            users = _ids(item, "allowed_user_ids")
            return _scope("user", users=users) if users else None
        if visibility == "company":
            return _scope("company", companies=_ids(item, "allowed_company_ids"))
        if visibility == "company_admin":
            return _scope("company_admin", companies=_ids(item, "company_admin_ids"))
        return None
    companies = _ids(item, "company_id", "allowed_company_ids")
    permission_flags = {
        "products": ["can_access_shop", "can_access_orders", "can_access_cart"],
        "packages": ["can_access_shop", "can_access_orders", "can_access_cart"],
        "chats": ["can_access_chat"],
        "orders": ["can_access_orders"],
        "assets": ["can_manage_assets"],
        "staff": ["can_manage_staff", "staff_permission"],
        "issues": ["can_manage_issues"],
        "mailboxes": ["can_view_m365_user_mailboxes", "can_view_m365_shared_mailboxes"],
        "best_practices": ["can_view_m365_best_practices"],
    }
    if source_type in permission_flags:
        if not companies and source_type in {"tickets", "ticket_comments", "chats"}:
            users = _ids(item, "requester_id", "user_id", "allowed_user_ids")
            return _scope("user", users=users) if users else None
        return _scope(
            "company", companies=companies, required_any=permission_flags[source_type]
        )
    if source_type in {"tickets", "ticket_comments", "companies", "backup_jobs"}:
        if companies:
            return _scope("company", companies=companies)
        users = _ids(item, "requester_id", "user_id", "allowed_user_ids")
        return _scope("user", users=users) if users else None
    if source_type == "service_status":
        return (
            _scope("company", companies=companies)
            if companies
            else _scope("authenticated")
        )
    if source_type == "reports":
        users = _ids(item, "allowed_user_ids", "user_id")
        return _scope("user", users=users) if users else _scope("super_admin")
    if source_type.startswith("feature:"):
        supplied = item.get("permission_scope")
        if isinstance(supplied, Mapping) and int(supplied.get("version") or 0) == 1:
            return dict(supplied)
        return None
    return None


async def index_document(
    document: RagDocument, *, source_updated_at: Any | None = None
) -> int:
    previous = await rag_repo.get_document_by_source(
        document.source_type, document.source_id, embedding_model()
    )
    new_hash = content_hash(document.text)
    content_changed = (
        not previous or str(previous.get("content_hash") or "") != new_hash
    )
    chunks = []
    for label, section_text in document.sections or [("Document", document.text)]:
        chunks.extend(
            f"[Section: {label}] {chunk}" for chunk in chunk_text(section_text)
        )
    if not chunks:
        chunks = [normalise_text(document.title)]
    canonical_url = canonical_source_url(
        document.source_type,
        document.source_id,
        metadata=document.metadata,
        supplied_url=document.url,
    )
    doc_id = await rag_repo.upsert_document(
        {
            "source_type": document.source_type,
            "source_id": document.source_id,
            "company_id": document.company_id,
            "title": document.title,
            "url": canonical_url,
            "permission_scope_json": json.dumps(
                document.permission_scope or {}, ensure_ascii=False
            ),
            "metadata_json": json.dumps(
                document.metadata or {}, ensure_ascii=False, default=str
            ),
            "content_hash": new_hash,
            "embedding_model": embedding_model(),
            "source_updated_at": source_updated_at,
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
                "embedding_json": json.dumps(await embed_text(chunk)),
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
            identity = source_identity(str(normalised_type), item)
            if identity is None:
                continue
            canonical_type, source_id = identity
            active.setdefault(canonical_type, set()).add(source_id)
    return active


async def index_agent_sources(
    sources: Mapping[str, Any],
    *,
    job_id: int | None = None,
    cleanup_missing: bool = False,
) -> int:
    indexed = 0
    diagnostics: dict[str, dict[str, int]] = {}
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
            counts = diagnostics.setdefault(
                str(normalised_type), {"received": 0, "indexed": 0, "skipped": 0}
            )
            counts["received"] += 1
            if job_id is not None and await rag_repo.job_stop_requested(job_id):
                raise RagIndexCancelled(f"Index job {job_id} was stopped.")
            if not isinstance(item, Mapping):
                counts["skipped"] += 1
                continue
            document = document_from_source(normalised_type, item)
            if document is None:
                counts["skipped"] += 1
                continue
            await index_document(document)
            indexed += 1
            counts["indexed"] += 1
    for source_type, counts in diagnostics.items():
        logger = log_warning if counts["skipped"] else log_info
        logger("RAG source indexing diagnostics", source_type=source_type, **counts)
    if cleanup_missing:
        if job_id is not None and await rag_repo.job_stop_requested(job_id):
            raise RagIndexCancelled(f"Index job {job_id} was stopped.")
        deleted = await cleanup_missing_agent_sources(sources)
        log_info("RAG stale source cleanup diagnostics", deleted=deleted)
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

    active_sources = source_keys_from_agent_sources(sources)
    for source_type, source_ids in active_sources.items():
        log_info(
            "RAG source cleanup diagnostics",
            source_type=source_type,
            active_records=len(source_ids),
        )
    return await rag_repo.cleanup_missing_documents(
        active_sources, embedding_model=embedding_model()
    )
