"""Permission-first search over the shared RAG documentation index.

This is intentionally a retrieval view over ``rag_documents``/``rag_chunks``
rather than a second index.  ACLs are evaluated before matching, counting, or
building a snippet so an inaccessible chunk cannot influence any response field.
"""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from collections.abc import Mapping, Sequence
from typing import Any

from app.repositories import rag_index as rag_repo
from app.services import company_access
from app.services.rag_index import embedding_model, tokenise
from app.services.rag_permissions import can_access_candidate
from app.services.rag_urls import canonical_source_url

DOCUMENTATION_SOURCES = ("assets", "knowledge_base")


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return dict(value)
    try:
        parsed = json.loads(str(value or "{}"))
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _normalise_filter(value: str | None) -> str | None:
    cleaned = str(value or "").strip().casefold()
    return cleaned or None


def _metadata_value(metadata: Mapping[str, Any], *names: str) -> str:
    for name in names:
        value = metadata.get(name)
        if value is not None and str(value).strip():
            return str(value).strip().casefold()
    return ""


def _matches_filters(
    row: Mapping[str, Any], metadata: Mapping[str, Any], *, asset_type: str | None,
    status: str | None, owner: str | None,
) -> bool:
    if asset_type and _metadata_value(metadata, "type", "asset_type") != asset_type:
        return False
    if status and _metadata_value(metadata, "status", "publication_status") != status:
        return False
    if owner and owner not in _metadata_value(
        metadata, "owner", "owner_name", "last_user", "updated_by"
    ):
        return False
    return True


def _linked_asset_ids(metadata: Mapping[str, Any]) -> set[str]:
    values: list[Any] = []
    values.extend(metadata.get("asset_ids") or [])
    for asset in metadata.get("assets") or []:
        if isinstance(asset, Mapping):
            values.append(asset.get("id"))
    return {str(value) for value in values if value is not None}


def _snippet(text: str, terms: Sequence[str], *, width: int = 260) -> str:
    """Return a compact, plain-text window around the first query hit."""
    clean = re.sub(r"\s+", " ", str(text or "")).strip()
    if not clean:
        return ""
    lowered = clean.casefold()
    positions = [lowered.find(term.casefold()) for term in terms if term]
    hit = min((position for position in positions if position >= 0), default=0)
    start = max(0, hit - width // 3)
    end = min(len(clean), start + width)
    if start:
        boundary = clean.find(" ", start)
        start = boundary + 1 if boundary >= 0 else start
    excerpt = clean[start:end].strip()
    return ("…" if start else "") + excerpt + ("…" if end < len(clean) else "")


async def search_documentation(
    query: str,
    user: Mapping[str, Any],
    *,
    active_company_id: int | None,
    memberships: Sequence[Mapping[str, Any]] | None = None,
    company_id: int | None = None,
    sources: Sequence[str] | None = None,
    asset_type: str | None = None,
    status: str | None = None,
    owner: str | None = None,
    page: int = 1,
    page_size: int = 20,
) -> dict[str, Any]:
    """Search documentation without leaking unauthorised snippets or totals."""
    query_text = str(query or "").strip()
    aliases = {"asset": "assets", "kb": "knowledge_base", "runbook": "knowledge_base"}
    requested = tuple(dict.fromkeys(
        aliases.get(str(value).strip().casefold(), str(value).strip().casefold())
        for value in (sources or DOCUMENTATION_SOURCES)
    ))
    requested = tuple(value for value in requested if value in DOCUMENTATION_SOURCES)
    if not query_text or not requested:
        return {"results": [], "total": 0, "page": page, "page_size": page_size,
                "pages": 0, "counts": {}}

    # Documentation searches are always anchored to the effective company.  A
    # submitted company is a narrowing filter, never a tenant switch.
    if company_id is not None and (
        active_company_id is None or int(company_id) != int(active_company_id)
    ):
        return {"results": [], "total": 0, "page": page, "page_size": page_size,
                "pages": 0, "counts": {}}
    effective_company = company_id if company_id is not None else active_company_id
    resolved_memberships = list(memberships or []) or await company_access.list_accessible_companies(user)
    terms = tokenise(query_text)
    phrase = query_text.casefold()
    visible_rows: list[tuple[Mapping[str, Any], dict[str, Any]]] = []
    for source in requested:
        rows = await rag_repo.list_active_chunks(
            embedding_model=embedding_model(), source_types=[source], limit=10000
        )
        for row in rows:
            metadata = _json_object(row.get("metadata_json"))
            candidate = {
                "permission_scope": _json_object(row.get("permission_scope_json")),
                "company_id": row.get("company_id"),
            }
            # The ordering here is a security boundary: do not inspect chunk text,
            # calculate relevance, or count until its persisted ACL has passed.
            if not can_access_candidate(candidate, user=user, memberships=resolved_memberships):
                continue
            scope_companies = candidate["permission_scope"].get("company_ids") or []
            if effective_company is not None and scope_companies:
                try:
                    if int(effective_company) not in {int(value) for value in scope_companies}:
                        continue
                except (TypeError, ValueError):
                    # Malformed tenant constraints fail closed even if the generic
                    # permission helper ignored the unusable individual value.
                    continue
            row_company = row.get("company_id")
            if effective_company is not None and row_company is not None:
                try:
                    if int(row_company) != int(effective_company):
                        continue
                except (TypeError, ValueError):
                    continue
            if not _matches_filters(
                row, metadata, asset_type=_normalise_filter(asset_type),
                status=_normalise_filter(status), owner=_normalise_filter(owner),
            ):
                continue
            visible_rows.append((row, metadata))

    matched_assets: set[str] = set()
    matches: dict[int, dict[str, Any]] = {}
    for row, metadata in visible_rows:
        haystack = " ".join((str(row.get("title") or ""), str(row.get("source_id") or ""),
                             str(row.get("chunk_text") or ""), json.dumps(metadata, default=str))).casefold()
        hits = sum(haystack.count(term) for term in set(terms))
        if phrase in haystack:
            hits += max(2, len(terms))
        if not hits:
            continue
        if row.get("source_type") == "assets":
            matched_assets.add(str(row.get("source_id")))
        doc_id = int(row.get("document_id") or 0)
        score = float(hits)
        current = matches.get(doc_id)
        if current is None or score > current["score"]:
            matches[doc_id] = {"row": row, "metadata": metadata, "score": score, "linked": False}

    # A hostname hit should also navigate to a linked runbook even where the
    # hostname itself is not repeated in the article body.
    if matched_assets:
        for row, metadata in visible_rows:
            if row.get("source_type") != "knowledge_base" or not (_linked_asset_ids(metadata) & matched_assets):
                continue
            doc_id = int(row.get("document_id") or 0)
            if doc_id not in matches:
                matches[doc_id] = {"row": row, "metadata": metadata, "score": 0.5, "linked": True}

    results: list[dict[str, Any]] = []
    for match in matches.values():
        row, metadata = match["row"], match["metadata"]
        source = str(row.get("source_type"))
        source_id = str(row.get("source_id"))
        url = canonical_source_url(source, source_id, metadata=metadata)
        if not url:
            continue
        results.append({
            "source": source, "source_id": source_id, "title": str(row.get("title") or source_id),
            "snippet": _snippet(str(row.get("chunk_text") or ""), terms), "url": url,
            "company_id": row.get("company_id"), "score": match["score"],
            "metadata": {"type": metadata.get("type"), "status": metadata.get("status"),
                         "owner": metadata.get("owner") or metadata.get("last_user"),
                         "linked_runbook": bool(match["linked"])},
        })
    results.sort(key=lambda item: (-item["score"], item["title"].casefold(), item["source_id"]))
    counts = dict(Counter(item["source"] for item in results))
    total = len(results)
    start = (page - 1) * page_size
    return {"results": results[start:start + page_size], "total": total, "page": page,
            "page_size": page_size, "pages": math.ceil(total / page_size) if total else 0,
            "counts": counts}
