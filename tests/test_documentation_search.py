"""Permission and navigation regression tests for ITDOC search."""

from __future__ import annotations

import json

import pytest

from app.services import documentation_search
from app.services.rag_urls import canonical_source_url


def _row(
    document_id: int,
    source: str,
    source_id: str,
    title: str,
    text: str,
    *,
    company_id: int = 7,
    scope: dict | None = None,
    metadata: dict | None = None,
) -> dict:
    return {
        "document_id": document_id,
        "chunk_id": document_id,
        "source_type": source,
        "source_id": source_id,
        "title": title,
        "chunk_text": text,
        "company_id": company_id,
        "permission_scope_json": json.dumps(
            scope or {"version": 1, "visibility": "company", "company_ids": [company_id]}
        ),
        "metadata_json": json.dumps(metadata or {}),
    }


@pytest.mark.anyio("asyncio")
async def test_hostname_returns_asset_and_authorised_linked_runbook(monkeypatch):
    rows = {
        "assets": [_row(1, "assets", "42", "Web server", "Hostname: web-01.example.test")],
        "knowledge_base": [
            _row(2, "knowledge_base", "restart-web", "Restart web services", "Safe restart procedure",
                 metadata={"slug": "restart-web", "asset_ids": [42]})
        ],
    }

    async def active_chunks(*, source_types, **_kwargs):
        return rows[source_types[0]]

    monkeypatch.setattr(documentation_search.rag_repo, "list_active_chunks", active_chunks)
    result = await documentation_search.search_documentation(
        "web-01.example.test", {"id": 9}, active_company_id=7,
        memberships=[{"company_id": 7, "can_manage_assets": True}],
    )

    assert result["total"] == 2
    assert result["counts"] == {"assets": 1, "knowledge_base": 1}
    assert {item["url"] for item in result["results"]} == {
        "/assets/42", "/knowledge-base/articles/restart-web"
    }
    runbook = next(item for item in result["results"] if item["source"] == "knowledge_base")
    assert runbook["metadata"]["linked_runbook"] is True


@pytest.mark.anyio("asyncio")
async def test_unauthorised_content_contributes_no_result_snippet_or_count(monkeypatch):
    secret = _row(1, "assets", "99", "Secret host", "forbidden-host.example.test",
                  company_id=8)

    async def active_chunks(**_kwargs):
        return [secret]

    monkeypatch.setattr(documentation_search.rag_repo, "list_active_chunks", active_chunks)
    result = await documentation_search.search_documentation(
        "forbidden-host.example.test", {"id": 9}, active_company_id=7,
        memberships=[{"company_id": 7, "can_manage_assets": True}], sources=["assets"],
    )

    assert result == {
        "results": [], "total": 0, "page": 1, "page_size": 20,
        "pages": 0, "counts": {},
    }
    assert "forbidden-host" not in json.dumps(result)


def test_documentation_links_use_existing_routes():
    assert canonical_source_url("assets", 12) == "/assets/12"
    assert canonical_source_url("knowledge_base", "runbook") == "/knowledge-base/articles/runbook"
