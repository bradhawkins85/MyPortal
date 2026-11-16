import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.services import rag_index


def test_index_document_repairs_unchanged_document_without_active_chunks(monkeypatch):
    document = rag_index.RagDocument(
        source_type="tickets",
        source_id="42",
        title="Printer issue",
        text="The office printer is showing error code 17.",
        company_id=3,
        permission_scope={"version": 1, "visibility": "company", "company_ids": [3]},
    )
    previous = {
        "id": 71,
        "content_hash": rag_index.content_hash(document.text),
    }
    monkeypatch.setattr(
        rag_index, "get_settings", lambda: SimpleNamespace(rag_chunk_words=100, rag_chunk_overlap_words=10)
    )
    monkeypatch.setattr(rag_index, "embedding_model", lambda: "test-model")
    monkeypatch.setattr(rag_index.rag_repo, "get_document_by_source", AsyncMock(return_value=previous))
    monkeypatch.setattr(rag_index.rag_repo, "has_active_chunks", AsyncMock(return_value=False))
    monkeypatch.setattr(rag_index.rag_repo, "get_chunk_embeddings", AsyncMock(return_value={}))
    monkeypatch.setattr(rag_index.rag_repo, "upsert_document", AsyncMock(return_value=71))
    replace_chunks = AsyncMock()
    monkeypatch.setattr(rag_index.rag_repo, "replace_chunks", replace_chunks)
    monkeypatch.setattr(rag_index, "embed_text", AsyncMock(return_value=[1.0]))
    notify = AsyncMock()
    monkeypatch.setattr(rag_index.rag_relationships, "on_document_indexed", notify)

    result = asyncio.run(rag_index.index_document(document))

    assert result == 71
    replace_chunks.assert_awaited_once()
    assert replace_chunks.await_args.args[1][0]["chunk_text"].endswith(document.text)
    notify.assert_awaited_once_with(71, content_changed=True)


def test_index_document_skips_unchanged_document_with_active_chunks(monkeypatch):
    document = rag_index.RagDocument(
        source_type="tickets",
        source_id="42",
        title="Printer issue",
        text="The office printer is showing error code 17.",
        company_id=3,
        permission_scope={"version": 1, "visibility": "company", "company_ids": [3]},
    )
    previous = {
        "id": 71,
        "content_hash": rag_index.content_hash(document.text),
    }
    monkeypatch.setattr(rag_index, "embedding_model", lambda: "test-model")
    monkeypatch.setattr(rag_index.rag_repo, "get_document_by_source", AsyncMock(return_value=previous))
    monkeypatch.setattr(rag_index.rag_repo, "has_active_chunks", AsyncMock(return_value=True))
    monkeypatch.setattr(rag_index.rag_repo, "upsert_document", AsyncMock(return_value=71))
    replace_chunks = AsyncMock()
    monkeypatch.setattr(rag_index.rag_repo, "replace_chunks", replace_chunks)

    result = asyncio.run(rag_index.index_document(document))

    assert result == 71
    replace_chunks.assert_not_awaited()


def test_failed_embedding_refresh_keeps_old_hash_and_retries(monkeypatch):
    document = rag_index.RagDocument(
        source_type='tickets', source_id='42', title='Printer issue',
        text='New resolution for the printer.',
    )
    previous = {'id': 71, 'content_hash': rag_index.content_hash('Old printer issue.')}
    monkeypatch.setattr(rag_index, 'get_settings', lambda: SimpleNamespace(rag_chunk_words=100, rag_chunk_overlap_words=10))
    monkeypatch.setattr(rag_index, 'embedding_model', lambda: 'test-model')
    monkeypatch.setattr(rag_index.rag_repo, 'get_document_by_source', AsyncMock(return_value=previous))
    monkeypatch.setattr(rag_index.rag_repo, 'has_active_chunks', AsyncMock(return_value=True))
    monkeypatch.setattr(rag_index.rag_repo, 'get_chunk_embeddings', AsyncMock(return_value={}))

    async def upsert(record):
        previous['content_hash'] = record['content_hash']
        return 71

    save = AsyncMock(side_effect=upsert)
    replace = AsyncMock()
    embed = AsyncMock(side_effect=[ValueError('provider failed'), [1.0]])
    notify = AsyncMock()
    monkeypatch.setattr(rag_index.rag_repo, 'upsert_document', save)
    monkeypatch.setattr(rag_index.rag_repo, 'replace_chunks', replace)
    monkeypatch.setattr(rag_index, 'embed_text', embed)
    monkeypatch.setattr(rag_index.rag_relationships, 'on_document_indexed', notify)

    async def run():
        import pytest
        with pytest.raises(ValueError, match='provider failed'):
            await rag_index.index_document(document)
        save.assert_not_awaited()
        replace.assert_not_awaited()
        assert previous['content_hash'] == rag_index.content_hash('Old printer issue.')
        assert await rag_index.index_document(document) == 71

    asyncio.run(run())
    assert embed.await_count == 2
    replace.assert_awaited_once()
    assert previous['content_hash'] == rag_index.content_hash(document.text)
    notify.assert_awaited_once_with(71, content_changed=True)


def _ticket(text):
    return rag_index.RagDocument(
        source_type="tickets", source_id="42", title="Printer issue", text=text,
        sections=[("Description", text)],
    )


def _settings():
    return SimpleNamespace(rag_chunk_words=4, rag_chunk_overlap_words=0, rag_chunk_max_chars=2000)


def test_changed_document_reuses_embeddings_for_unchanged_chunks(monkeypatch):
    old = _ticket("alpha beta gamma delta")
    new = _ticket("alpha beta gamma delta epsilon zeta eta theta")
    monkeypatch.setattr(rag_index, "get_settings", _settings)
    monkeypatch.setattr(rag_index, "embedding_model", lambda: "test-model")
    unchanged_hash = rag_index.content_hash("[Section: Description] alpha beta gamma delta")
    monkeypatch.setattr(
        rag_index.rag_repo, "get_document_by_source",
        AsyncMock(return_value={"id": 71, "content_hash": rag_index.content_hash(old.text)}),
    )
    monkeypatch.setattr(rag_index.rag_repo, "has_active_chunks", AsyncMock(return_value=True))
    existing = AsyncMock(return_value={unchanged_hash: "[0.5, 0.5]"})
    monkeypatch.setattr(rag_index.rag_repo, "get_chunk_embeddings", existing)
    monkeypatch.setattr(rag_index.rag_repo, "upsert_document", AsyncMock(return_value=71))
    replace = AsyncMock()
    monkeypatch.setattr(rag_index.rag_repo, "replace_chunks", replace)
    embed = AsyncMock(return_value=[1.0])
    monkeypatch.setattr(rag_index, "embed_text", embed)
    monkeypatch.setattr(rag_index.rag_relationships, "on_document_indexed", AsyncMock())

    asyncio.run(rag_index.index_document(new))

    existing.assert_awaited_once_with(71, "test-model")
    embed.assert_awaited_once_with("[Section: Description] epsilon zeta eta theta")
    chunks = replace.await_args.args[1]
    assert [c["embedding_json"] for c in chunks] == ["[0.5, 0.5]", "[1.0]"]


def test_identical_chunks_in_one_document_are_embedded_once(monkeypatch):
    document = _ticket("alpha beta gamma delta alpha beta gamma delta")
    monkeypatch.setattr(rag_index, "get_settings", _settings)
    monkeypatch.setattr(rag_index, "embedding_model", lambda: "test-model")
    monkeypatch.setattr(rag_index.rag_repo, "get_document_by_source", AsyncMock(return_value=None))
    monkeypatch.setattr(rag_index.rag_repo, "upsert_document", AsyncMock(return_value=71))
    replace = AsyncMock()
    monkeypatch.setattr(rag_index.rag_repo, "replace_chunks", replace)
    embed = AsyncMock(return_value=[1.0])
    monkeypatch.setattr(rag_index, "embed_text", embed)
    monkeypatch.setattr(rag_index.rag_relationships, "on_document_indexed", AsyncMock())

    asyncio.run(rag_index.index_document(document))

    assert embed.await_count == 1
    assert len(replace.await_args.args[1]) == 2


def test_one_failing_source_does_not_stop_the_full_index(monkeypatch):
    monkeypatch.setattr(rag_index.ai_consent, "prepare_rag_source", AsyncMock(side_effect=lambda _t, item: item))
    calls = []

    async def index(document, **_kwargs):
        calls.append(document.source_id)
        if document.source_id == "1":
            raise ValueError("provider failed")
        return 5

    monkeypatch.setattr(rag_index, "index_document", index)

    indexed = asyncio.run(
        rag_index.index_agent_sources(
            {"orders": [{"id": 1, "title": "One", "company_id": 1}, {"id": 2, "title": "Two", "company_id": 1}]}
        )
    )

    assert calls == ["1", "2"]
    assert indexed == 1


def test_chunk_embeddings_prefer_active_rows_and_skip_empty(monkeypatch):
    from app.repositories import rag_index as rag_repo

    rows = [
        {"chunk_hash": "a", "embedding_json": "[1]"},
        {"chunk_hash": "a", "embedding_json": "[2]"},
        {"chunk_hash": "b", "embedding_json": None},
    ]
    fetch = AsyncMock(return_value=rows)
    monkeypatch.setattr(rag_repo.db, "fetch_all", fetch)

    assert asyncio.run(rag_repo.get_chunk_embeddings(7, "model")) == {"a": "[1]"}
    assert fetch.await_args.args[1] == (7, "model")
