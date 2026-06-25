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


@pytest.mark.anyio
async def test_index_agent_sources_honours_stop_request(monkeypatch):
    monkeypatch.setattr(
        rag_index.rag_repo, "job_stop_requested", AsyncMock(return_value=True)
    )

    with pytest.raises(rag_index.RagIndexCancelled):
        await rag_index.index_agent_sources(
            {"tickets": [{"id": 1, "subject": "Test"}]}, job_id=9
        )
