from unittest.mock import AsyncMock

import pytest

from app.services import rag_index


def test_source_keys_from_agent_sources_normalises_feature_pack_sources():
    sources = {
        "tickets": [{"id": 1}, {"id": "2"}],
        "chats": [{"uid": "chat-1"}],
        "feature_packs": {
            "demo": [{"key": "alpha"}, {"id": "beta"}],
            "empty": [],
        },
    }
    assert rag_index.source_keys_from_agent_sources(sources) == {
        "tickets": {"1", "2"},
        "chats": {"chat-1"},
        "feature:demo": {"alpha", "beta"},
    }


@pytest.mark.parametrize(
    ("field", "value", "expected"),
    [
        ("id", 0, "0"),
        ("slug", " article ", "article"),
        ("order_number", "ORD-42", "ORD-42"),
        ("key", "practice-key", "practice-key"),
        ("check_id", "BP-7", "BP-7"),
        ("user_principal_name", "person@example.test", "person@example.test"),
        ("uid", "mail-uid", "mail-uid"),
    ],
)
def test_every_supported_identifier_has_one_canonical_identity(field, value, expected):
    item = {field: value, "title": "Source", "company_id": 1}

    assert rag_index.source_identity("orders", item) == ("orders", expected)
    assert rag_index.source_keys_from_agent_sources({"orders": [item]}) == {
        "orders": {expected}
    }
    assert rag_index.document_from_source("orders", item).source_id == expected


@pytest.mark.parametrize("value", [None, "", "  ", True, [], {}])
def test_canonical_identity_rejects_unstable_or_empty_values(value):
    assert rag_index.source_identity("feature:demo", {"id": value}) is None


@pytest.mark.anyio
async def test_index_agent_sources_honours_stop_request(monkeypatch):
    monkeypatch.setattr(
        rag_index.rag_repo, "job_stop_requested", AsyncMock(return_value=True)
    )

    with pytest.raises(rag_index.RagIndexCancelled):
        await rag_index.index_agent_sources(
            {"tickets": [{"id": 1, "subject": "Test"}]}, job_id=9
        )


def test_ticket_document_indexes_full_conversation_attachments_and_assets():
    item = {
        "id": 42,
        "subject": "Printer failure",
        "description": "Initial report",
        "company_id": 7,
        "ai_tags": ["printing"],
        "replies": [{"body": "Resolution token REPLY-ONLY-991", "is_internal": False}],
        "attachments": [{"original_filename": "diagnostic-reply-only.log"}],
        "linked_assets": [{"asset_id": 88, "serial_number": "ASSET-ONLY-123"}],
    }

    document = rag_index.document_from_source("tickets", item)

    assert "[Description]\nInitial report" in document.text
    assert "[Reply 1]\nResolution token REPLY-ONLY-991" in document.text
    assert "diagnostic-reply-only.log" in document.text
    assert "serial_number=ASSET-ONLY-123" in document.text
    assert document.metadata["identifiers"] == {"id": 42}


def test_knowledge_base_document_indexes_later_sections_and_preserves_scope():
    item = {
        "slug": "vpn-recovery",
        "title": "VPN recovery",
        "summary": "Basic checks",
        "content": "Complete article introduction",
        "sections": [
            {"heading": "Initial checks", "content": "Restart the client"},
            {
                "heading": "Final recovery",
                "content": "LATER-SECTION-ONLY rotate certificate",
            },
        ],
        "article_permission_scope": "company",
        "allowed_company_ids": [3],
    }

    document = rag_index.document_from_source("knowledge_base", item)

    assert "[Final recovery]\nLATER-SECTION-ONLY rotate certificate" in document.text
    assert document.permission_scope["company_ids"] == [3]
    assert "Final recovery" in document.metadata["section_labels"]


def test_logical_sections_are_chunked_without_crossing_boundaries(monkeypatch):
    monkeypatch.setattr(rag_index, "_chunk_words", lambda: 3)
    monkeypatch.setattr(rag_index, "_chunk_overlap_words", lambda: 0)
    document = rag_index.document_from_source(
        "tickets",
        {
            "id": 1,
            "subject": "one two three four",
            "description": "five six seven eight",
            "requester_id": 9,
        },
    )

    chunks = []
    for label, text in document.sections:
        chunks.extend(
            f"[Section: {label}] {chunk}" for chunk in rag_index.chunk_text(text)
        )

    assert chunks == [
        "[Section: Subject] one two three",
        "[Section: Subject] four",
        "[Section: Description] five six seven",
        "[Section: Description] eight",
    ]


def test_internal_note_document_is_restricted_to_super_admins():
    document = rag_index.document_from_source(
        "ticket_comments",
        {
            "id": "42:7",
            "title": "Internal note",
            "internal_only": True,
            "company_id": 3,
            "replies": [{"body": "privileged remediation", "is_internal": True}],
        },
    )

    assert "[Internal note 1]\nprivileged remediation" in document.text
    assert document.permission_scope == {
        "version": 1,
        "visibility": "super_admin",
        "company_ids": [],
        "user_ids": [],
        "required_any": [],
    }
