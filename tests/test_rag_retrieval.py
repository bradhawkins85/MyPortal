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
