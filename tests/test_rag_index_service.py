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
def test_every_supported_identifier_has_one_canonical_identity(
    field, value, expected
):
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
