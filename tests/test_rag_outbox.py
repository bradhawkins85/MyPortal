import asyncio
from unittest.mock import AsyncMock

from app.services import rag_outbox


def test_enqueue_coalesces_source_changes(monkeypatch):
    execute = AsyncMock()
    monkeypatch.setattr(rag_outbox.db, "execute", execute)
    monkeypatch.setattr(rag_outbox.db, "is_sqlite", lambda: False)
    monkeypatch.setattr(rag_outbox.db, "is_connected", lambda: True)

    asyncio.run(rag_outbox.enqueue("tickets", 42, action="delete"))

    sql, params = execute.await_args.args
    assert "ON DUPLICATE KEY UPDATE" in sql
    assert params[:3] == ("tickets", "42", "delete")


def test_process_pending_indexes_source_and_completes(monkeypatch):
    row = {
        "id": 9,
        "source_type": "tickets",
        "source_id": "42",
        "action": "upsert",
        "attempt_count": 0,
        "source_updated_at": None,
    }
    monkeypatch.setattr(rag_outbox.db, "is_sqlite", lambda: False)
    monkeypatch.setattr(rag_outbox.db, "execute", AsyncMock())
    monkeypatch.setattr(rag_outbox.db, "fetch_all", AsyncMock(return_value=[row]))
    monkeypatch.setattr(rag_outbox.db, "execute_rowcount", AsyncMock(return_value=1))
    monkeypatch.setattr(rag_outbox, "_load_source", AsyncMock(return_value={"id": 42, "subject": "Help", "company_id": 3}))
    document = object()
    monkeypatch.setattr(rag_outbox.rag_index, "document_from_source", lambda *_: document)
    index = AsyncMock()
    monkeypatch.setattr(rag_outbox.rag_index, "index_document", index)

    result = asyncio.run(rag_outbox.process_pending())

    assert result == {"processed": 1, "failed": 0, "retried": 0}
    index.assert_awaited_once()


def test_process_pending_retries_observable_failure(monkeypatch):
    row = {"id": 4, "source_type": "tickets", "source_id": "7", "action": "upsert", "attempt_count": 1}
    execute = AsyncMock()
    monkeypatch.setattr(rag_outbox.db, "is_sqlite", lambda: False)
    monkeypatch.setattr(rag_outbox.db, "execute", execute)
    monkeypatch.setattr(rag_outbox.db, "fetch_all", AsyncMock(return_value=[row]))
    monkeypatch.setattr(rag_outbox.db, "execute_rowcount", AsyncMock(return_value=1))
    monkeypatch.setattr(rag_outbox, "_load_source", AsyncMock(side_effect=RuntimeError("temporary")))

    result = asyncio.run(rag_outbox.process_pending())

    assert result["retried"] == 1
    assert any("last_error" in call.args[0] for call in execute.await_args_list)
