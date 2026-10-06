import pytest

from app.repositories import ticket_tasks, tickets


class _RecordingDB:
    def __init__(self):
        self.executed: list[tuple[str, tuple]] = []

    async def execute(self, sql, params=None):
        self.executed.append((" ".join(sql.split()), params))

    async def execute_rowcount(self, sql, params=None):
        self.executed.append((" ".join(sql.split()), params))
        return 1

    async def fetch_one(self, sql, params=None):
        return None

    async def fetch_all(self, sql, params=None):
        return []

    def task_updates(self):
        return [
            (sql, params)
            for sql, params in self.executed
            if sql.startswith("UPDATE ticket_tasks")
        ]


@pytest.fixture
def recording_db(monkeypatch):
    db = _RecordingDB()
    monkeypatch.setattr(tickets, "db", db)
    monkeypatch.setattr(ticket_tasks, "db", db)

    class _Outbox:
        async def enqueue(self, *args, **kwargs):
            return None

    monkeypatch.setattr(tickets, "_rag_outbox_service", lambda: _Outbox())
    return db


@pytest.mark.anyio
@pytest.mark.parametrize("status", ["closed", "resolved", "Closed"])
async def test_closing_linked_ticket_completes_task(recording_db, status):
    await tickets.update_ticket(77, status=status)

    updates = recording_db.task_updates()
    assert len(updates) == 1
    sql, params = updates[0]
    assert "SET is_completed = 1" in sql
    assert "WHERE linked_ticket_id IN (%s) AND is_completed = 0" in sql
    assert params == (77,)


@pytest.mark.anyio
async def test_non_closing_status_leaves_tasks_alone(recording_db):
    await tickets.update_ticket(77, status="in_progress")
    await tickets.update_ticket(77, subject="Renamed")

    assert recording_db.task_updates() == []


@pytest.mark.anyio
async def test_bulk_close_completes_linked_tasks(recording_db):
    await tickets.set_tickets_status([5, "6", 5, "bad"], "closed")

    updates = recording_db.task_updates()
    assert len(updates) == 1
    assert updates[0][1] == (5, 6)


@pytest.mark.anyio
async def test_bulk_reopen_does_not_complete_tasks(recording_db):
    await tickets.set_tickets_status([5, 6], "open")

    assert recording_db.task_updates() == []


@pytest.mark.anyio
async def test_merging_linked_ticket_completes_task(recording_db, monkeypatch):
    async def _get_ticket(ticket_id):
        return {"id": ticket_id}

    async def _list_empty(*args, **kwargs):
        return []

    monkeypatch.setattr(tickets, "get_ticket", _get_ticket)
    monkeypatch.setattr(tickets, "list_watchers", _list_empty)
    monkeypatch.setattr(tickets, "list_replies", _list_empty)

    await tickets.merge_tickets([10, 11, 12], 10)

    updates = recording_db.task_updates()
    assert len(updates) == 1
    assert updates[0][1] == (11, 12)
