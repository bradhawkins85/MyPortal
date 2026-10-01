import json
import time

import pytest

from app.repositories import rag_relationships


@pytest.mark.anyio
async def test_production_sized_candidate_corpus_has_bounded_output(monkeypatch):
    """A changed ticket scans once but queues a fixed-size result, not N pairs."""
    source = {
        "id": 1,
        "source_type": "tickets",
        "company_id": 77,
        "title": "INC-12345 laptop cannot boot",
        "metadata_json": json.dumps({"asset_id": "ASSET-9"}),
        "embedding_model": "test-model",
        "is_active": 1,
    }
    rows = []
    source_types = ("knowledge_base", "assets", "tickets")
    for document_id in range(2, 20_002):
        source_type = source_types[document_id % len(source_types)]
        text = f"unrelated background document {document_id}"
        metadata = {"tag": f"tag-{document_id}"}
        if document_id in {100, 101, 102}:
            source_type = source_types[document_id - 100]
            text = "INC-12345 ASSET-9 laptop boot remediation"
            metadata = {"asset_id": "ASSET-9"}
        rows.append(
            {
                **source,
                "id": document_id,
                "source_type": source_type,
                "title": text,
                "metadata_json": json.dumps(metadata),
                "candidate_text": text,
                "candidate_embedding": "[1.0, 0.0, 0.0, 0.0]",
            }
        )

    async def fetch_one(query, params=()):
        return source

    async def fetch_all(query, params=()):
        if "FROM rag_documents d" in query:
            return rows
        return [
            {
                "chunk_text": "INC-12345 ASSET-9 laptop cannot boot",
                "embedding_json": "[1.0, 0.0, 0.0, 0.0]",
            }
        ]

    monkeypatch.setattr(rag_relationships.db, "fetch_one", fetch_one)
    monkeypatch.setattr(rag_relationships.db, "fetch_all", fetch_all)

    started = time.perf_counter()
    candidates = await rag_relationships.list_compatible_targets(
        1, include_tickets=True, limit=24, ticket_limit=5
    )
    duration = time.perf_counter() - started

    assert len(candidates) <= 24
    assert {100, 101, 102}.issubset({candidate["id"] for candidate in candidates})
    assert all(candidate["company_id"] == 77 for candidate in candidates)
    assert candidates[0]["eligible_documents"] == 20_000
    # Generous enough for shared CI, while detecting accidental quadratic work.
    assert duration < 5.0
