import pytest
from datetime import datetime, timezone

from app.repositories import booking_event_types


class _DummyDB:
    """Mock database for testing."""
    
    def __init__(self, fetched_row=None):
        self.execute_sql = None
        self.execute_params = None
        self.fetch_sql = None
        self.fetch_params = None
        self._fetched_row = fetched_row

    async def execute(self, sql, params):
        self.execute_sql = sql.strip()
        self.execute_params = params

    async def fetch_one(self, sql, params):
        self.fetch_sql = sql.strip()
        self.fetch_params = params
        return self._fetched_row

    async def fetch_all(self, sql, params):
        self.fetch_sql = sql.strip()
        self.fetch_params = params
        return [self._fetched_row] if self._fetched_row else []


@pytest.mark.asyncio
async def test_create_event_type(monkeypatch):
    """Test creating an event type."""
    expected_row = {
        "id": 1,
        "user_id": 10,
        "slug": "30min",
        "title": "30 Minute Meeting",
        "duration_minutes": 30,
        "buffer_before_minutes": 5,
        "buffer_after_minutes": 5,
        "is_active": True,
        "metadata": None,
    }
    
    db = _DummyDB(expected_row)
    monkeypatch.setattr("app.repositories.booking_event_types.db", db)

    result = await booking_event_types.create_event_type(
        user_id=10,
        slug="30min",
        title="30 Minute Meeting",
        duration_minutes=30,
        buffer_before_minutes=5,
        buffer_after_minutes=5,
    )

    assert result == expected_row
    assert db.execute_sql is not None
    assert "INSERT INTO booking_event_types" in db.execute_sql
    assert 10 in db.execute_params


@pytest.mark.asyncio
async def test_get_event_type(monkeypatch):
    """Test retrieving an event type by ID."""
    expected_row = {
        "id": 1,
        "user_id": 10,
        "slug": "30min",
        "title": "30 Minute Meeting",
        "duration_minutes": 30,
        "buffer_before_minutes": 5,
        "buffer_after_minutes": 5,
        "is_active": True,
        "metadata": None,
    }
    
    db = _DummyDB(expected_row)
    monkeypatch.setattr("app.repositories.booking_event_types.db", db)

    result = await booking_event_types.get_event_type(1)

    assert result == expected_row
    assert db.fetch_sql is not None
    assert "SELECT * FROM booking_event_types WHERE id" in db.fetch_sql
    assert (1,) == db.fetch_params


@pytest.mark.asyncio
async def test_update_event_type(monkeypatch):
    """Test updating an event type."""
    original_row = {
        "id": 1,
        "user_id": 10,
        "slug": "30min",
        "title": "30 Minute Meeting",
        "duration_minutes": 30,
        "buffer_before_minutes": 5,
        "buffer_after_minutes": 5,
        "is_active": True,
        "metadata": None,
    }
    
    updated_row = {**original_row, "is_active": False}
    
    class _UpdateDB(_DummyDB):
        def __init__(self):
            super().__init__(updated_row)
            self.update_called = False
        
        async def execute(self, sql, params):
            self.update_called = True
            self.execute_sql = sql.strip()
            self.execute_params = params

    db = _UpdateDB()
    monkeypatch.setattr("app.repositories.booking_event_types.db", db)

    result = await booking_event_types.update_event_type(1, is_active=False)

    assert result == updated_row
    assert db.update_called
    assert "UPDATE booking_event_types SET" in db.execute_sql
    assert db.execute_params[-1] == 1  # event_type_id


@pytest.mark.asyncio
async def test_delete_event_type(monkeypatch):
    """Test deleting an event type."""
    db = _DummyDB()
    monkeypatch.setattr("app.repositories.booking_event_types.db", db)

    await booking_event_types.delete_event_type(1)

    assert db.execute_sql is not None
    assert "DELETE FROM booking_event_types WHERE id" in db.execute_sql
    assert (1,) == db.execute_params


@pytest.mark.asyncio
async def test_list_event_types(monkeypatch):
    """Test listing event types with filters."""
    expected_row = {
        "id": 1,
        "user_id": 10,
        "slug": "30min",
        "title": "30 Minute Meeting",
        "duration_minutes": 30,
        "is_active": True,
        "metadata": None,
    }
    
    db = _DummyDB(expected_row)
    monkeypatch.setattr("app.repositories.booking_event_types.db", db)

    result = await booking_event_types.list_event_types(user_id=10, is_active=True)

    assert len(result) == 1
    assert result[0] == expected_row
    assert db.fetch_sql is not None
    assert "SELECT * FROM booking_event_types" in db.fetch_sql
    assert "WHERE" in db.fetch_sql
