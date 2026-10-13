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
