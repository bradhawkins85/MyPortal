import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.services import rag_index, rag_retrieval


@pytest.mark.anyio
async def test_openai_compatible_embedding_supports_semantic_paraphrase(monkeypatch):
    settings = SimpleNamespace(
        rag_embedding_provider="openai_compatible",
        rag_embedding_model="semantic-test",
        rag_embedding_dimensions=3,
        rag_embedding_base_url="http://embedding.test",
        rag_embedding_api_key=None,
    )
    vectors = {
        "wireless disconnects": [1.0, 0.1, 0.0],
        "Wi-Fi keeps dropping": [0.99, 0.1, 0.0],
    }
    response = SimpleNamespace(
        raise_for_status=lambda: None,
        json=lambda: {"data": [{"embedding": vectors[current_text[0]]}]},
    )
    current_text = [""]

    async def post(_self, _url, *, json, headers):
        current_text[0] = json["input"]
        return response

    monkeypatch.setattr(rag_index, "get_settings", lambda: settings)
    monkeypatch.setattr(rag_index.httpx.AsyncClient, "post", post)
    left = await rag_index.embed_text("wireless disconnects")
    right = await rag_index.embed_text("Wi-Fi keeps dropping")

    assert rag_index.cosine_similarity(left, right) > 0.99


@pytest.mark.anyio
async def test_openai_compatible_embedding_uses_llama_cpp_supported_payload(monkeypatch):
    settings = SimpleNamespace(
        rag_embedding_provider="openai_compatible",
        rag_embedding_model="nomic-embed-text",
        rag_embedding_dimensions=2,
        rag_embedding_base_url="http://llama.test",
        rag_embedding_api_key=None,
    )
    response = SimpleNamespace(
        raise_for_status=lambda: None,
        json=lambda: {"data": [{"embedding": [3.0, 4.0, 12.0]}]},
    )
    payloads = []

    async def post(_self, _url, *, json, headers):
        payloads.append(json)
        return response

    monkeypatch.setattr(rag_index, "get_settings", lambda: settings)
    monkeypatch.setattr(rag_index.httpx.AsyncClient, "post", post)

    vector = await rag_index.embed_text("Section: Subject")

    assert payloads == [
        {
            "model": "nomic-embed-text",
            "input": "Section: Subject",
            "encoding_format": "float",
        }
    ]
    assert vector == pytest.approx([0.6, 0.8])


@pytest.mark.anyio
async def test_openai_compatible_embedding_rejects_too_short_vector(monkeypatch):
    settings = SimpleNamespace(
        rag_embedding_provider="openai_compatible",
        rag_embedding_model="nomic-embed-text",
        rag_embedding_dimensions=3,
        rag_embedding_base_url="http://llama.test",
        rag_embedding_api_key=None,
    )
    response = SimpleNamespace(
        raise_for_status=lambda: None,
        json=lambda: {"data": [{"embedding": [1.0, 2.0]}]},
    )
    monkeypatch.setattr(rag_index, "get_settings", lambda: settings)
    monkeypatch.setattr(
        rag_index.httpx.AsyncClient, "post", AsyncMock(return_value=response)
    )

    with pytest.raises(ValueError, match="configured 3, provider returned 2"):
        await rag_index.embed_text("test")


def test_embedding_fingerprint_changes_for_every_compatibility_component(monkeypatch):
    base = dict(
        rag_embedding_provider="ollama",
        rag_embedding_model="nomic-embed-text",
        rag_embedding_dimensions=768,
    )
    monkeypatch.setattr(rag_index, "get_settings", lambda: SimpleNamespace(**base))
    original = rag_index.embedding_model()

    for key, value in (
        ("rag_embedding_provider", "openai_compatible"),
        ("rag_embedding_model", "other-model"),
        ("rag_embedding_dimensions", 384),
    ):
        changed = {**base, key: value}
        monkeypatch.setattr(
            rag_index,
            "get_settings",
            lambda changed=changed: SimpleNamespace(**changed),
        )
        assert rag_index.embedding_model() != original


def test_unreachable_source_threshold_is_rejected(monkeypatch):
    monkeypatch.setitem(rag_retrieval._SOURCE_THRESHOLDS, "products", 0.61)

    with pytest.raises(ValueError, match="products"):
        rag_retrieval.validate_score_configuration(
            vector_weight=0.45,
            bm25_weight=0.45,
            metadata_weight=0.10,
            minimum_score=0.35,
        )


def test_global_minimum_cannot_make_a_source_unreachable():
    with pytest.raises(ValueError, match="best_practices"):
        rag_retrieval.validate_score_configuration(
            vector_weight=0.45,
            bm25_weight=0.45,
            metadata_weight=0.10,
            minimum_score=0.51,
        )


@pytest.mark.anyio
async def test_reranker_is_bounded_and_applies_returned_order(monkeypatch):
    settings = SimpleNamespace(
        rag_rerank_enabled=True,
        rag_rerank_top_n=2,
        rag_rerank_model="reranker",
        rag_embedding_api_key=None,
        rag_embedding_base_url="http://llm.test",
    )
    response = SimpleNamespace(
        raise_for_status=lambda: None,
        json=lambda: {"choices": [{"message": {"content": json.dumps([1, 0])}}]},
    )
    monkeypatch.setattr(rag_retrieval, "get_settings", lambda: settings)
    monkeypatch.setattr(
        rag_retrieval.httpx.AsyncClient, "post", AsyncMock(return_value=response)
    )
    candidates = [{"title": value, "excerpt": value} for value in "abc"]

    ranked = await rag_retrieval._rerank("query", candidates)

    assert [item["title"] for item in ranked] == ["b", "a", "c"]
