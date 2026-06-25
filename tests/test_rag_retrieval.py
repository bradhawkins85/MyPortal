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
