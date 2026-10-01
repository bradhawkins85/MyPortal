import pytest

from app.features.tickets import admin_routes
from app import main


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


def test_related_sources_replace_external_urls_with_canonical_destination():
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
    ) == [
        {
            "type": "knowledge_base",
            "label": "Replace CMOS battery",
            "url": "/knowledge-base/articles/replace-cmos-battery",
        }
    ]


@pytest.mark.anyio
async def test_stored_relationships_recheck_permission_and_include_evidence(monkeypatch):
    async def get_document(*_args):
        return {"id": 91}

    async def evidence(*_args, **_kwargs):
        return [
            {
                "target_available": 1,
                "source_type": "products",
                "source_id": "12",
                "title": "Replacement battery",
                "url": None,
                "permission_scope_json": '{"version":1,"visibility":"company","company_ids":[3]}',
                "metadata_json": "{}",
                "relationship_type": "KNOWN_ISSUE",
                "confidence": 0.91,
                "relevance_score": 0.88,
                "reason": "The same battery fault is documented.",
            }
        ]

    checked = {}

    def permitted(candidate, *, user, memberships):
        checked.update(user=user, memberships=memberships, candidate=candidate)
        return True

    monkeypatch.setattr(main.rag_index_repo, "get_document_by_source", get_document)
    monkeypatch.setattr(main.rag_relationship_repo, "list_relationship_evidence", evidence)
    monkeypatch.setattr(main, "can_access_candidate", permitted)

    items = await main._load_ticket_stored_related_items(
        4, user={"id": 8}, memberships=[{"company_id": 3}]
    )

    assert checked["user"] == {"id": 8}
    assert items == [
        {
            "available": True,
            "type": "products",
            "label": "Replacement battery",
            "url": "/shop/admin/product/12",
            "relationship_label": "Known issue",
            "confidence_band": "High confidence",
            "score": 88,
            "reason": "The same battery fault is documented.",
        }
    ]


@pytest.mark.anyio
async def test_stored_relationships_distinguish_deleted_target(monkeypatch):
    async def get_document(*_args):
        return {"id": 91}

    async def evidence(*_args, **_kwargs):
        return [
            {
                "target_available": 0,
                "relationship_type": "DUPLICATE",
                "confidence": 0.7,
            }
        ]

    monkeypatch.setattr(main.rag_index_repo, "get_document_by_source", get_document)
    monkeypatch.setattr(main.rag_relationship_repo, "list_relationship_evidence", evidence)

    items = await main._load_ticket_stored_related_items(
        4, user={"id": 8}, memberships=[]
    )

    assert items == [
        {
            "available": False,
            "relationship_label": "Duplicate",
            "confidence_band": "Medium confidence",
            "label": "Related target is unavailable or has been deleted",
        }
    ]
