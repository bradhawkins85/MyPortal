from app.features.tickets import admin_routes


def test_related_ticket_query_uses_specific_terms_not_instructions():
    query = admin_routes._build_related_ticket_query(
        {
            "subject": "CMOS battery replacement required",
            "description": "Workstation reports CMOS checksum failure after power loss.",
            "category": "hardware",
        },
        [],
        [],
    )

    assert "cmos" in query
    assert "battery" in query
    assert "knowledge" not in query
    assert "vpn" not in query


def test_related_sources_filter_out_unmatched_agent_results():
    terms = ["cmos", "battery", "checksum", "hardware"]
    sources = {
        "knowledge_base": [
            {
                "slug": "install-vpn-client",
                "title": "Install VPN client",
                "summary": "How to configure remote access.",
                "url": "/knowledge-base/articles/install-vpn-client",
            },
            {
                "slug": "replace-cmos-battery",
                "title": "Replace CMOS battery",
                "summary": "Fix CMOS checksum failures after power loss.",
                "url": "/knowledge-base/articles/replace-cmos-battery",
            },
        ]
    }

    items = admin_routes._related_items_from_agent_sources(
        sources,
        current_ticket_id=123,
        search_terms=terms,
    )

    assert [item["label"] for item in items] == ["Replace CMOS battery"]


def test_related_sources_reject_external_urls():
    sources = {
        "knowledge_base": [
            {
                "slug": "replace-cmos-battery",
                "title": "Replace CMOS battery",
                "summary": "CMOS battery replacement notes.",
                "url": "https://evil.example/phish",
            }
        ]
    }

    assert admin_routes._related_items_from_agent_sources(
        sources,
        current_ticket_id=123,
        search_terms=["cmos", "battery"],
    ) == []
