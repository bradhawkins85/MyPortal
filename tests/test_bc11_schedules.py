"""
Tests for BCP training and review schedule operations.
"""
import pytest
from datetime import datetime, timezone
from app.repositories import bcp as bcp_repo


@pytest.fixture
def anyio_backend():
    return "asyncio"


class _MockCursor:
    """Mock database cursor for testing."""
    
    def __init__(self):
        self.lastrowid = 1
        self.rowcount = 1
        self._results = []
        self.executed_queries = []
        
    async def execute(self, query, params=None):
        self.executed_queries.append((query, params))
        
    async def fetchone(self):
        if self._results:
            return self._results.pop(0)
        return None
        
    async def fetchall(self):
        results = self._results
        self._results = []
        return results
    
    async def __aenter__(self):
        return self
        
    async def __aexit__(self, *args):
        pass


class _MockConnection:
    """Mock database connection for testing."""
    
    def __init__(self):
        self.cursor_instance = _MockCursor()
        
    def cursor(self):
        return self.cursor_instance
    
    async def commit(self):
        pass
        
    async def __aenter__(self):
        return self
        
    async def __aexit__(self, *args):
        pass


class _MockDatabase:
    """Mock database for testing."""
    
    def __init__(self):
        self.connection_instance = _MockConnection()
        
    def connection(self):
        return self.connection_instance


@pytest.mark.anyio
async def test_create_training_item(monkeypatch):
    """Test creating a training item."""
    mock_db = _MockDatabase()
    monkeypatch.setattr("app.repositories.bcp.db", mock_db)
    
    # Mock return value for get_training_item_by_id
    mock_db.connection_instance.cursor_instance._results = [
        (1, 1, datetime(2024, 12, 1, 10, 0), "Tabletop Exercise", "Test comments", datetime.now(), datetime.now())
    ]
    
    training = await bcp_repo.create_training_item(
        plan_id=1,
        training_date=datetime(2024, 12, 1, 10, 0),
        training_type="Tabletop Exercise",
        comments="Test comments"
    )
    
    assert training is not None
    assert training["id"] == 1
    assert training["plan_id"] == 1
    assert training["training_type"] == "Tabletop Exercise"
    assert training["comments"] == "Test comments"


@pytest.mark.anyio
async def test_list_training_items(monkeypatch):
    """Test listing training items for a plan."""
    mock_db = _MockDatabase()
    monkeypatch.setattr("app.repositories.bcp.db", mock_db)
    
    # Mock return value for list query - fetchall returns a list of tuples
    mock_db.connection_instance.cursor_instance._results = [
        (1, 1, datetime(2024, 12, 1, 10, 0), "Tabletop Exercise", "First training", datetime.now(), datetime.now()),
        (2, 1, datetime(2024, 12, 15, 14, 0), "Full-scale Drill", "Second training", datetime.now(), datetime.now())
    ]
    
    trainings = await bcp_repo.list_training_items(plan_id=1)
    
    assert len(trainings) == 2
    assert trainings[0]["id"] == 1
    assert trainings[0]["training_type"] == "Tabletop Exercise"
    assert trainings[1]["id"] == 2
    assert trainings[1]["training_type"] == "Full-scale Drill"


@pytest.mark.anyio
async def test_update_training_item(monkeypatch):
    """Test updating a training item."""
    mock_db = _MockDatabase()
    monkeypatch.setattr("app.repositories.bcp.db", mock_db)
    
    # Mock return value for get_training_item_by_id
    mock_db.connection_instance.cursor_instance._results = [
        (1, 1, datetime(2024, 12, 1, 10, 0), "Updated Type", "Updated comments", datetime.now(), datetime.now())
    ]
    
    updated = await bcp_repo.update_training_item(
        training_id=1,
        training_type="Updated Type",
        comments="Updated comments"
    )
    
    assert updated is not None
    assert updated["training_type"] == "Updated Type"
    assert updated["comments"] == "Updated comments"


@pytest.mark.anyio
async def test_delete_training_item(monkeypatch):
    """Test deleting a training item."""
    mock_db = _MockDatabase()
    monkeypatch.setattr("app.repositories.bcp.db", mock_db)
    
    mock_db.connection_instance.cursor_instance.rowcount = 1
    
    result = await bcp_repo.delete_training_item(training_id=1)
    
    assert result is True


@pytest.mark.anyio
async def test_create_review_item(monkeypatch):
    """Test creating a review item."""
    mock_db = _MockDatabase()
    monkeypatch.setattr("app.repositories.bcp.db", mock_db)
    
    # Mock return value for get_review_item_by_id
    mock_db.connection_instance.cursor_instance._results = [
        (1, 1, datetime(2024, 12, 1, 10, 0), "Annual review", "Updated procedures", datetime.now(), datetime.now())
    ]
    
    review = await bcp_repo.create_review_item(
        plan_id=1,
        review_date=datetime(2024, 12, 1, 10, 0),
        reason="Annual review",
        changes_made="Updated procedures"
    )
    
    assert review is not None
    assert review["id"] == 1
    assert review["plan_id"] == 1
    assert review["reason"] == "Annual review"
    assert review["changes_made"] == "Updated procedures"


@pytest.mark.anyio
async def test_list_review_items(monkeypatch):
    """Test listing review items for a plan."""
    mock_db = _MockDatabase()
    monkeypatch.setattr("app.repositories.bcp.db", mock_db)
    
    # Mock return value for list query - fetchall returns a list of tuples
    mock_db.connection_instance.cursor_instance._results = [
        (1, 1, datetime(2024, 12, 1, 10, 0), "Annual review", "First review", datetime.now(), datetime.now()),
        (2, 1, datetime(2024, 12, 15, 14, 0), "Quarterly review", "Second review", datetime.now(), datetime.now())
    ]
    
    reviews = await bcp_repo.list_review_items(plan_id=1)
    
    assert len(reviews) == 2
    assert reviews[0]["id"] == 1
    assert reviews[0]["reason"] == "Annual review"
    assert reviews[1]["id"] == 2
    assert reviews[1]["reason"] == "Quarterly review"


@pytest.mark.anyio
async def test_update_review_item(monkeypatch):
    """Test updating a review item."""
    mock_db = _MockDatabase()
    monkeypatch.setattr("app.repositories.bcp.db", mock_db)
    
    # Mock return value for get_review_item_by_id
    mock_db.connection_instance.cursor_instance._results = [
        (1, 1, datetime(2024, 12, 1, 10, 0), "Updated reason", "Updated changes", datetime.now(), datetime.now())
    ]
    
    updated = await bcp_repo.update_review_item(
        review_id=1,
        reason="Updated reason",
        changes_made="Updated changes"
    )
    
    assert updated is not None
    assert updated["reason"] == "Updated reason"
    assert updated["changes_made"] == "Updated changes"


@pytest.mark.anyio
async def test_delete_review_item(monkeypatch):
    """Test deleting a review item."""
    mock_db = _MockDatabase()
    monkeypatch.setattr("app.repositories.bcp.db", mock_db)
    
    mock_db.connection_instance.cursor_instance.rowcount = 1
    
    result = await bcp_repo.delete_review_item(review_id=1)
    
    assert result is True


@pytest.mark.anyio
async def test_get_upcoming_training_items(monkeypatch):
    """Test getting upcoming training items."""
    mock_db = _MockDatabase()
    monkeypatch.setattr("app.repositories.bcp.db", mock_db)
    
    # Mock return value for upcoming training query - fetchall returns a list of tuples
    mock_db.connection_instance.cursor_instance._results = [
        (1, 1, datetime(2024, 12, 1, 10, 0), "Tabletop Exercise", "Upcoming training", 
         datetime.now(), datetime.now(), 1, 100, "BCP Plan")
    ]
    
    upcoming = await bcp_repo.get_upcoming_training_items(days_ahead=7)
    
    assert len(upcoming) == 1
    assert upcoming[0]["id"] == 1
    assert upcoming[0]["training_type"] == "Tabletop Exercise"
    assert "plan" in upcoming[0]
    assert upcoming[0]["plan"]["id"] == 1
    assert upcoming[0]["plan"]["company_id"] == 100


@pytest.mark.anyio
async def test_get_upcoming_review_items(monkeypatch):
    """Test getting upcoming review items."""
    mock_db = _MockDatabase()
    monkeypatch.setattr("app.repositories.bcp.db", mock_db)
    
    # Mock return value for upcoming review query - fetchall returns a list of tuples
    mock_db.connection_instance.cursor_instance._results = [
        (1, 1, datetime(2024, 12, 1, 10, 0), "Annual review", "Review changes", 
         datetime.now(), datetime.now(), 1, 100, "BCP Plan")
    ]
    
    upcoming = await bcp_repo.get_upcoming_review_items(days_ahead=7)
    
    assert len(upcoming) == 1
    assert upcoming[0]["id"] == 1
    assert upcoming[0]["reason"] == "Annual review"
    assert "plan" in upcoming[0]
    assert upcoming[0]["plan"]["id"] == 1
    assert upcoming[0]["plan"]["company_id"] == 100
