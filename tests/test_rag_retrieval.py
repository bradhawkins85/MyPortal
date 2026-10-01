from app.services import rag_retrieval


def test_profile_extracts_entities_and_expands_domain_terms():
    profile = rag_retrieval._profile_query(
        "CMOS batteries purchase Trello card Brad Hawkins created 24425 Jimmi Nolan eBay"
    )

    assert "24425" in profile.entities["ids"]
    assert "Brad Hawkins" in profile.entities["names"]
    assert "Jimmi Nolan" in profile.entities["names"]
    assert "rtc battery" in profile.expanded
    assert "board" in profile.expanded
    assert "created" not in profile.tokens
    assert "Support Ticket" in profile.intents
    assert "Chat" in profile.intents
    assert "Product Lookup" in profile.intents


def test_hybrid_score_prefers_exact_entities_over_semantic_neighbour():
    profile = rag_retrieval._profile_query("CMOS battery Trello 24425 Brad Hawkins")
    relevant = {
        "chunk_id": 1,
        "title": "Ticket 24425 CMOS battery purchase",
        "source_id": "24425",
        "source_type": "tickets",
        "chunk_text": "Brad Hawkins asked Jimmi Nolan about CMOS battery purchase from eBay Trello card.",
    }
    irrelevant = {
        "chunk_id": 2,
        "title": "ThinkPad touchpad troubleshooting",
        "source_id": "kb-touchpad",
        "source_type": "knowledge_base",
        "chunk_text": "Lenovo ThinkPad touchpad mouse dock sleeve troubleshooting guide.",
    }
    metadata = {
        1: {
            "ticket": 24425,
            "author": "Brad Hawkins",
            "keywords": ["CMOS", "battery", "ebay"],
        },
        2: {},
    }

    scores = rag_retrieval._bm25_scores(
        [relevant, irrelevant], profile.tokens, metadata
    )

    assert scores[1] > scores.get(2, 0)
    assert rag_retrieval._metadata_boost(
        profile, relevant, metadata[1]
    ) > rag_retrieval._metadata_boost(profile, irrelevant, metadata[2])


def test_group_duplicate_candidates_exposes_duplicates_without_repeating_snippets():
    candidates = [
        {
            "document_id": 30,
            "chunk_id": 1,
            "source_type": "chats",
            "source_id": 30,
            "title": "Lenovo touchpad",
            "excerpt": "User was shown a Lenovo touchpad article.",
            "score": 0.9,
            "metadata": {"linked_ticket_id": 24425},
            "_embedding": [1.0, 0.0, 0.0],
        },
        {
            "document_id": 31,
            "chunk_id": 2,
            "source_type": "chats",
            "source_id": 31,
            "title": "Lenovo touchpad follow-up",
            "excerpt": "User was shown a Lenovo touchpad article.",
            "score": 0.87,
            "metadata": {"linked_ticket_id": 24425},
            "_embedding": [1.0, 0.0, 0.0],
        },
    ]

    grouped = rag_retrieval._group_duplicate_candidates(candidates)

    assert len(grouped) == 1
    assert grouped[0]["source_id"] == 30
    assert grouped[0]["duplicate_count"] == 1
    assert grouped[0]["duplicates"][0]["source_id"] == 31


def test_retrieve_candidates_demotes_other_company_evidence_in_stored_score(monkeypatch):
    import asyncio
    import json
    from unittest.mock import AsyncMock

    def _row(chunk_id, company_id, floor, vector):
        return {
            "chunk_id": chunk_id,
            "document_id": chunk_id,
            "source_type": "tickets",
            "source_id": str(chunk_id),
            "company_id": company_id,
            "title": "Printer jam",
            "chunk_text": f"Printer jam on level {floor}",
            "embedding_json": json.dumps(vector),
            "metadata_json": "{}",
            "permission_scope_json": "{}",
        }

    # Equally relevant, but distinct enough not to be grouped as duplicates.
    rows = [_row(1, 7, "two", [0.8, 0.6]), _row(2, 8, "six", [0.8, -0.6])]

    async def fake_chunks(*, embedding_model, source_types, limit):
        return rows if source_types == ["tickets"] else []

    monkeypatch.setattr(rag_retrieval.rag_repo, "list_active_chunks", fake_chunks)
    monkeypatch.setattr(rag_retrieval, "embed_text", AsyncMock(return_value=[1.0, 0.0]))
    monkeypatch.setattr(
        rag_retrieval, "can_access_current_candidate", AsyncMock(return_value=True)
    )

    candidates = asyncio.run(
        rag_retrieval.retrieve_candidates(
            "printer jam",
            {"id": 1, "is_super_admin": True},
            active_company_id=8,
            memberships=[{"company_id": 7}, {"company_id": 8}],
            source_filters=["tickets"],
            min_score=0.0,
        )
    )

    assert [item["company_id"] for item in candidates] == [8, 7]
    assert candidates[0]["score"] > candidates[1]["score"]


def _prefilter_row(chunk_id, company_id):
    import json

    return {
        "chunk_id": chunk_id,
        "document_id": chunk_id,
        "source_type": "tickets",
        "source_id": str(chunk_id),
        "company_id": company_id,
        "title": f"Printer jam {chunk_id}",
        "chunk_text": f"Printer jam reported on floor {chunk_id}",
        "embedding_json": json.dumps([1.0, 0.0]),
        "metadata_json": "{}",
        "permission_scope_json": json.dumps(
            {"version": 1, "visibility": "company", "company_ids": [company_id]}
        ),
    }


def _patch_prefilter(monkeypatch, rows_by_id, vector_pages, lexical_ids=()):
    from unittest.mock import AsyncMock

    calls = {"nearest": [], "full_scan": 0}

    async def nearest(source_type, query_embedding, *, limit, offset=0):
        calls["nearest"].append((limit, offset))
        return list(vector_pages.get(offset, []))

    async def lexical(source_type, text, *, limit):
        return list(lexical_ids)

    async def by_ids(*, embedding_model, chunk_ids):
        return [rows_by_id[i] for i in chunk_ids if i in rows_by_id]

    async def full_scan(**kwargs):
        calls["full_scan"] += 1
        return list(rows_by_id.values())

    vector_index = rag_retrieval.rag_vector_index
    monkeypatch.setattr(vector_index, "prefilter_ready", AsyncMock(return_value=True))
    monkeypatch.setattr(vector_index, "nearest_chunk_ids", nearest)
    monkeypatch.setattr(vector_index, "lexical_chunk_ids", lexical)
    monkeypatch.setattr(rag_retrieval.rag_repo, "list_active_chunks_by_ids", by_ids)
    monkeypatch.setattr(rag_retrieval.rag_repo, "list_active_chunks", full_scan)
    monkeypatch.setattr(rag_retrieval, "embed_text", AsyncMock(return_value=[1.0, 0.0]))
    return calls


def _retrieve_as_company_member(monkeypatch, company_id):
    import asyncio

    async def static_policy(candidate, *, user, memberships, cache=None):
        from app.services.rag_permissions import can_access_candidate

        return can_access_candidate(candidate, user=user, memberships=memberships)

    monkeypatch.setattr(rag_retrieval, "can_access_current_candidate", static_policy)
    return asyncio.run(
        rag_retrieval.retrieve_candidates(
            "printer jam",
            {"id": 5},
            memberships=[{"company_id": company_id}],
            source_filters=["tickets"],
            min_score=0.0,
        )
    )


def test_prefilter_scores_only_nearest_and_lexical_rows_and_keeps_permissions(
    monkeypatch,
):
    rows = {1: _prefilter_row(1, 7), 2: _prefilter_row(2, 8), 3: _prefilter_row(3, 7)}
    rows[3]["chunk_text"] = "Completely different wording about toner"
    calls = _patch_prefilter(monkeypatch, rows, {0: [1, 2]}, lexical_ids=[3])

    candidates = _retrieve_as_company_member(monkeypatch, 7)

    assert calls["full_scan"] == 0
    assert {item["company_id"] for item in candidates} == {7}
    assert 2 not in {item["chunk_id"] for item in candidates}


def test_prefilter_widens_vector_page_when_nearest_rows_are_unauthorised(monkeypatch):
    from app.core.config import get_settings

    settings = get_settings()
    monkeypatch.setattr(settings, "rag_prefilter_top_k", 50)
    monkeypatch.setattr(settings, "rag_active_chunk_limit", 1000)
    rows = {i: _prefilter_row(i, 8) for i in range(1, 51)}
    rows.update({i: _prefilter_row(i, 7) for i in range(51, 151)})
    calls = _patch_prefilter(
        monkeypatch,
        rows,
        {0: list(range(1, 51)), 50: list(range(51, 151))},
    )

    candidates = _retrieve_as_company_member(monkeypatch, 7)

    assert calls["nearest"] == [(50, 0), (100, 50)]
    assert calls["full_scan"] == 0
    assert candidates and {item["company_id"] for item in candidates} == {7}


def test_prefilter_failure_falls_back_to_full_scan(monkeypatch):
    from unittest.mock import AsyncMock

    rows = {1: _prefilter_row(1, 7)}
    calls = _patch_prefilter(monkeypatch, rows, {})
    monkeypatch.setattr(
        rag_retrieval.rag_vector_index,
        "nearest_chunk_ids",
        AsyncMock(side_effect=RuntimeError("vector index offline")),
    )

    candidates = _retrieve_as_company_member(monkeypatch, 7)

    assert calls["full_scan"] == 1
    assert [item["chunk_id"] for item in candidates] == [1]


def test_vector_prefilter_is_never_ready_without_vector_support(monkeypatch):
    import asyncio

    from app.repositories import rag_vector_index

    rag_vector_index.reset_state()
    monkeypatch.setattr(rag_vector_index.db, "is_sqlite", lambda: True)

    assert asyncio.run(rag_vector_index.prefilter_ready()) is False
    rag_vector_index.reset_state()
