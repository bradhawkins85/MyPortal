import pytest
from datetime import datetime, timezone

from app.repositories import ticket_tasks


class _DummyTaskDB:
    def __init__(self, fetched_row):
        self.insert_sql: str | None = None
        self.insert_params: tuple | None = None
        self.fetch_sql: str | None = None
        self.fetch_params: tuple | None = None
        self._fetched_row = fetched_row

    async def execute_returning_lastrowid(self, sql, params):
        self.insert_sql = sql.strip()
        self.insert_params = params
        return 42

    async def fetch_one(self, sql, params):
        self.fetch_sql = sql.strip()
        self.fetch_params = params
        return self._fetched_row


class _UpdateTaskDB:
    def __init__(self, row):
        self.execute_sql = None
        self.execute_params = None
        self.fetch_sql = None
        self.fetch_params = None
        self._row = row

    async def execute(self, sql, params):
        self.execute_sql = sql.strip()
        self.execute_params = params

    async def fetch_one(self, sql, params):
        self.fetch_sql = sql.strip()
        self.fetch_params = params
        return self._row


class _ListTasksDB:
    def __init__(self, rows):
        self.fetch_sql = None
        self.fetch_params = None
        self._rows = rows

    async def fetch_all(self, sql, params):
        self.fetch_sql = sql.strip()
        self.fetch_params = params
        return self._rows


class _DeleteTaskDB:
    def __init__(self):
        self.execute_sql = None
        self.execute_params = None

    async def execute(self, sql, params):
        self.execute_sql = sql.strip()
        self.execute_params = params


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_create_task_returns_inserted_record(monkeypatch):
    fetched = {
        "id": 42,
        "ticket_id": 3,
        "task_name": "Test task",
        "is_completed": 0,
        "completed_at": None,
        "completed_by": None,
        "sort_order": 0,
        "created_at": None,
        "updated_at": None,
    }
    dummy_db = _DummyTaskDB(fetched)
    monkeypatch.setattr(ticket_tasks, "db", dummy_db)

    record = await ticket_tasks.create_task(
        ticket_id=3,
        task_name="Test task",
        sort_order=0,
    )

    assert record["id"] == 42
    assert record["ticket_id"] == 3
    assert record["task_name"] == "Test task"
    assert record["is_completed"] is False
    assert record["sort_order"] == 0
    assert dummy_db.fetch_sql == "SELECT * FROM ticket_tasks WHERE id = %s"
    assert dummy_db.fetch_params == (42,)


@pytest.mark.anyio
async def test_create_task_falls_back_when_fetch_missing(monkeypatch):
    dummy_db = _DummyTaskDB(fetched_row=None)
    monkeypatch.setattr(ticket_tasks, "db", dummy_db)

    record = await ticket_tasks.create_task(
        ticket_id=5,
        task_name="Fallback task",
        sort_order=1,
    )

    assert record["id"] == 42
    assert record["ticket_id"] == 5
    assert record["task_name"] == "Fallback task"
    assert record["is_completed"] is False
    assert record["sort_order"] == 1


@pytest.mark.anyio
async def test_list_tasks_returns_sorted_list(monkeypatch):
    rows = [
        {
            "id": 1,
            "ticket_id": 3,
            "task_name": "First task",
            "is_completed": 0,
            "completed_at": None,
            "completed_by": None,
            "sort_order": 0,
            "created_at": None,
            "updated_at": None,
        },
        {
            "id": 2,
            "ticket_id": 3,
            "task_name": "Second task",
            "is_completed": 1,
            "completed_at": datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc),
            "completed_by": 7,
            "sort_order": 1,
            "created_at": None,
            "updated_at": None,
        },
    ]
    dummy_db = _ListTasksDB(rows)
    monkeypatch.setattr(ticket_tasks, "db", dummy_db)

    tasks = await ticket_tasks.list_tasks(3)

    assert len(tasks) == 2
    assert tasks[0]["id"] == 1
    assert tasks[0]["is_completed"] is False
    assert tasks[1]["id"] == 2
    assert tasks[1]["is_completed"] is True
    assert tasks[1]["completed_at"].tzinfo is not None
    assert "ORDER BY sort_order ASC, id ASC" in dummy_db.fetch_sql


@pytest.mark.anyio
async def test_get_task_returns_normalised_record(monkeypatch):
    fetched = {
        "id": 15,
        "ticket_id": 2,
        "task_name": "Test task",
        "is_completed": 1,
        "completed_at": datetime(2024, 1, 1, 12, 0, tzinfo=timezone.utc),
        "completed_by": 7,
        "sort_order": 0,
        "created_at": None,
        "updated_at": None,
    }
    dummy_db = _DummyTaskDB(fetched)
    monkeypatch.setattr(ticket_tasks, "db", dummy_db)

    record = await ticket_tasks.get_task(15)

    assert record["id"] == 15
    assert record["ticket_id"] == 2
    assert record["is_completed"] is True
    assert record["completed_by"] == 7
    assert record["completed_at"].tzinfo is not None


@pytest.mark.anyio
async def test_update_task_marks_completed(monkeypatch):
    fetched = {
        "id": 7,
        "ticket_id": 3,
        "task_name": "Test task",
        "is_completed": 1,
        "completed_at": datetime.now(timezone.utc),
        "completed_by": 5,
        "sort_order": 0,
        "created_at": None,
        "updated_at": None,
    }
    dummy_db = _UpdateTaskDB(fetched)
    monkeypatch.setattr(ticket_tasks, "db", dummy_db)

    record = await ticket_tasks.update_task(7, is_completed=True, completed_by=5)

    assert "UPDATE ticket_tasks" in dummy_db.execute_sql
    assert "is_completed = %s" in dummy_db.execute_sql
    assert "completed_at = UTC_TIMESTAMP(6)" in dummy_db.execute_sql
    assert "completed_by = %s" in dummy_db.execute_sql
    assert record["is_completed"] is True
    assert record["completed_by"] == 5


@pytest.mark.anyio
async def test_update_task_marks_incomplete(monkeypatch):
    fetched = {
        "id": 9,
        "ticket_id": 5,
        "task_name": "Test task",
        "is_completed": 0,
        "completed_at": None,
        "completed_by": None,
        "sort_order": 0,
        "created_at": None,
        "updated_at": None,
    }
    dummy_db = _UpdateTaskDB(fetched)
    monkeypatch.setattr(ticket_tasks, "db", dummy_db)

    record = await ticket_tasks.update_task(9, is_completed=False)

    assert "is_completed = %s" in dummy_db.execute_sql
    assert "completed_at = NULL" in dummy_db.execute_sql
    assert "completed_by = NULL" in dummy_db.execute_sql
    assert record["is_completed"] is False
    assert record["completed_at"] is None
    assert record["completed_by"] is None


@pytest.mark.anyio
async def test_update_task_updates_name_and_order(monkeypatch):
    fetched = {
        "id": 11,
        "ticket_id": 6,
        "task_name": "Updated task name",
        "is_completed": 0,
        "completed_at": None,
        "completed_by": None,
        "sort_order": 5,
        "created_at": None,
        "updated_at": None,
    }
    dummy_db = _UpdateTaskDB(fetched)
    monkeypatch.setattr(ticket_tasks, "db", dummy_db)

    record = await ticket_tasks.update_task(11, task_name="Updated task name", sort_order=5)

    assert "task_name = %s" in dummy_db.execute_sql
    assert "sort_order = %s" in dummy_db.execute_sql
    assert record["task_name"] == "Updated task name"
    assert record["sort_order"] == 5


@pytest.mark.anyio
async def test_delete_task_executes_delete(monkeypatch):
    dummy_db = _DeleteTaskDB()
    monkeypatch.setattr(ticket_tasks, "db", dummy_db)

    await ticket_tasks.delete_task(42)

    assert dummy_db.execute_sql == "DELETE FROM ticket_tasks WHERE id = %s"
    assert dummy_db.execute_params == (42,)
