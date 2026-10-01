import pytest
from pydantic import ValidationError

from app.schemas.agent import AgentQueryRequest
from app.services.agent_sources import SOURCE_REGISTRY, SOURCE_TYPE_CAPS
from app.services import rag_retrieval


def test_retrieval_caps_are_derived_from_canonical_registry():
    assert rag_retrieval.SOURCE_TYPE_CAPS == SOURCE_TYPE_CAPS
    assert set(SOURCE_TYPE_CAPS) == set(SOURCE_REGISTRY)


def test_api_rejects_unknown_source_filter_with_clear_error():
    with pytest.raises(
        ValidationError, match="Unknown Agent source filter.*unknown_source"
    ):
        AgentQueryRequest(query="hello", source_filters=["unknown_source"])


def test_api_canonicalises_known_source_aliases():
    request = AgentQueryRequest(query="orders", source_filters=["order", "mailbox"])
    assert request.source_filters == ["orders", "mailboxes"]
