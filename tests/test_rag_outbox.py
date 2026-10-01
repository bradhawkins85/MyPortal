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
    assert params[4] == rag_outbox.CURRENT_PRIORITY


def test_backfill_enqueue_cannot_downgrade_pending_current_work(monkeypatch):
    execute = AsyncMock()
    monkeypatch.setattr(rag_outbox.db, "execute", execute)
    monkeypatch.setattr(rag_outbox.db, "is_sqlite", lambda: True)
    monkeypatch.setattr(rag_outbox.db, "is_connected", lambda: True)

    asyncio.run(
        rag_outbox.enqueue("tickets", 42, priority=rag_outbox.BACKFILL_PRIORITY)
    )

    sql, params = execute.await_args.args
    assert "MIN(rag_index_outbox.priority, excluded.priority)" in sql
    assert params[4] == rag_outbox.BACKFILL_PRIORITY


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
    monkeypatch.setattr(
        rag_outbox,
        "_load_source",
        AsyncMock(return_value={"id": 42, "subject": "Help", "company_id": 3}),
    )
    document = object()
    monkeypatch.setattr(
        rag_outbox.rag_index, "document_from_source", lambda *_: document
    )
    index = AsyncMock()
    monkeypatch.setattr(rag_outbox.rag_index, "index_document", index)

    result = asyncio.run(rag_outbox.process_pending())

    assert result == {"processed": 1, "failed": 0, "retried": 0}
    index.assert_awaited_once()


def test_process_pending_retries_observable_failure(monkeypatch):
    row = {
        "id": 4,
        "source_type": "tickets",
        "source_id": "7",
        "action": "upsert",
        "attempt_count": 1,
    }
    execute = AsyncMock()
    monkeypatch.setattr(rag_outbox.db, "is_sqlite", lambda: False)
    monkeypatch.setattr(rag_outbox.db, "execute", execute)
    monkeypatch.setattr(rag_outbox.db, "fetch_all", AsyncMock(return_value=[row]))
    monkeypatch.setattr(rag_outbox.db, "execute_rowcount", AsyncMock(return_value=1))
    monkeypatch.setattr(
        rag_outbox, "_load_source", AsyncMock(side_effect=RuntimeError("temporary"))
    )

    result = asyncio.run(rag_outbox.process_pending())

    assert result["retried"] == 1
    assert any("last_error" in call.args[0] for call in execute.await_args_list)


def test_process_pending_prioritises_current_work(monkeypatch):
    fetch_all = AsyncMock(return_value=[])
    monkeypatch.setattr(rag_outbox.db, "is_sqlite", lambda: False)
    monkeypatch.setattr(rag_outbox.db, "execute", AsyncMock())
    monkeypatch.setattr(rag_outbox.db, "fetch_all", fetch_all)

    asyncio.run(rag_outbox.process_pending())

    assert "ORDER BY priority, available_at, id" in fetch_all.await_args.args[0]


def test_reconcile_queues_every_existing_item_at_backfill_priority(monkeypatch):
    tickets = [
        {"id": 1, "updated_at": None},
        {"id": 2, "updated_at": None},
    ]
    monkeypatch.setattr(
        rag_outbox.tickets_repo,
        "list_tickets",
        AsyncMock(side_effect=[tickets, []]),
    )
    monkeypatch.setattr(
        rag_outbox.kb_repo,
        "list_articles",
        AsyncMock(return_value=[{"id": 3, "is_published": True, "updated_at": None}]),
    )
    enqueue = AsyncMock()
    monkeypatch.setattr(rag_outbox, "enqueue", enqueue)
    monkeypatch.setattr(
        rag_outbox.rag_repo, "cleanup_missing_documents", AsyncMock(return_value=0)
    )

    result = asyncio.run(rag_outbox.reconcile(page_size=2))

    assert result == {"queued": 3, "skipped": 0, "failed": 0, "deactivated": 0}
    assert [call.args[:2] for call in enqueue.await_args_list] == [
        ("tickets", "1"),
        ("tickets", "2"),
        ("knowledge_base", "3"),
    ]
    assert all(
        call.kwargs["priority"] == rag_outbox.BACKFILL_PRIORITY
        for call in enqueue.await_args_list
    )
