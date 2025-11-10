"""
Tests for BCP Business Impact Analysis (BIA) repository operations.
"""
import pytest
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


class _MockDB:
    """Mock database connection manager."""
    
    def __init__(self):
        self.connection_instance = _MockConnection()
        
    def connection(self):
        return self.connection_instance


@pytest.mark.anyio
async def test_list_critical_activities_empty(monkeypatch):
    """Test listing critical activities when none exist."""
    mock_db = _MockDB()
    mock_db.connection_instance.cursor_instance._results = []
    
    monkeypatch.setattr("app.repositories.bcp.db", mock_db)
    
    activities = await bcp_repo.list_critical_activities(1)
    
    assert activities == []
    assert len(mock_db.connection_instance.cursor_instance.executed_queries) == 1
    query, params = mock_db.connection_instance.cursor_instance.executed_queries[0]
    assert "bcp_critical_activity" in query.lower()
    assert params == (1,)


@pytest.mark.anyio
async def test_list_critical_activities_with_data(monkeypatch):
    """Test listing critical activities with impact data."""
    mock_db = _MockDB()
    
    # Mock result: one activity with impact
    mock_db.connection_instance.cursor_instance._results = [
        [
            (
                1, 1, "Email Services", "Critical email system",
                "High", "Sole", 1, "Important notes",
                "2024-01-01", "2024-01-01",
                10, "Loss of revenue", "Extra costs", "Staff idle",
                "Cannot deliver", "Bad reputation", "Regulatory fines",
                "Legal issues", 24, "Additional comments"
            )
        ]
    ]
    
    monkeypatch.setattr("app.repositories.bcp.db", mock_db)
    
    activities = await bcp_repo.list_critical_activities(1, sort_by="importance")
    
    assert len(activities) == 1
    activity = activities[0]
    assert activity["id"] == 1
    assert activity["name"] == "Email Services"
    assert activity["description"] == "Critical email system"
    assert activity["priority"] == "High"
    assert activity["supplier_dependency"] == "Sole"
    assert activity["importance"] == 1
    assert activity["notes"] == "Important notes"
    
    # Check impact
    assert activity["impact"] is not None
    assert activity["impact"]["id"] == 10
    assert activity["impact"]["losses_financial"] == "Loss of revenue"
    assert activity["impact"]["rto_hours"] == 24


@pytest.mark.anyio
async def test_create_critical_activity(monkeypatch):
    """Test creating a new critical activity."""
    mock_db = _MockDB()
    mock_db.connection_instance.cursor_instance.lastrowid = 99
    
    # Mock result for get_critical_activity_by_id
    mock_db.connection_instance.cursor_instance._results = [
        (
            99, 1, "Test Activity", "Test description",
            "High", "None", 2, "Test notes",
            "2024-01-01", "2024-01-01",
            None, None, None, None, None, None, None, None, None, None
        )
    ]
    
    monkeypatch.setattr("app.repositories.bcp.db", mock_db)
    
    activity = await bcp_repo.create_critical_activity(
        plan_id=1,
        name="Test Activity",
        description="Test description",
        priority="High",
        supplier_dependency="None",
        importance=2,
        notes="Test notes"
    )
    
    assert activity["id"] == 99
    assert activity["name"] == "Test Activity"
    assert activity["importance"] == 2
    
    # Check that INSERT was called
    queries = mock_db.connection_instance.cursor_instance.executed_queries
    insert_query = queries[0][0]
    assert "INSERT INTO bcp_critical_activity" in insert_query
    
    # Check that get_critical_activity_by_id was called
    select_query = queries[1][0]
    assert "SELECT" in select_query
    assert "bcp_critical_activity" in select_query.lower()


@pytest.mark.anyio
async def test_update_critical_activity(monkeypatch):
    """Test updating a critical activity."""
    mock_db = _MockDB()
    
    # Mock result for get_critical_activity_by_id
    mock_db.connection_instance.cursor_instance._results = [
        (
            1, 1, "Updated Activity", "Updated description",
            "Medium", "Major", 3, "Updated notes",
            "2024-01-01", "2024-01-01",
            None, None, None, None, None, None, None, None, None, None
        )
    ]
    
    monkeypatch.setattr("app.repositories.bcp.db", mock_db)
    
    activity = await bcp_repo.update_critical_activity(
        activity_id=1,
        name="Updated Activity",
        description="Updated description",
        priority="Medium",
        importance=3
    )
    
    assert activity["name"] == "Updated Activity"
    assert activity["importance"] == 3
    
    # Check that UPDATE was called
    queries = mock_db.connection_instance.cursor_instance.executed_queries
    update_query = queries[0][0]
    assert "UPDATE bcp_critical_activity" in update_query


@pytest.mark.anyio
async def test_delete_critical_activity(monkeypatch):
    """Test deleting a critical activity."""
    mock_db = _MockDB()
    mock_db.connection_instance.cursor_instance.rowcount = 1
    
    monkeypatch.setattr("app.repositories.bcp.db", mock_db)
    
    result = await bcp_repo.delete_critical_activity(1)
    
    assert result is True
    
    queries = mock_db.connection_instance.cursor_instance.executed_queries
    delete_query = queries[0][0]
    assert "DELETE FROM bcp_critical_activity" in delete_query


@pytest.mark.anyio
async def test_create_or_update_impact_create(monkeypatch):
    """Test creating impact data for a critical activity."""
    mock_db = _MockDB()
    mock_db.connection_instance.cursor_instance.lastrowid = 50
    
    # Mock results: no existing impact, then get activity with new impact
    mock_db.connection_instance.cursor_instance._results = [
        None,  # No existing impact
        (
            1, 1, "Test Activity", "Description",
            "High", "None", 1, "Notes",
            "2024-01-01", "2024-01-01",
            50, "Financial loss", "Increased costs", "Staff impact",
            "Service impact", "Reputation loss", "Fines", "Legal liability",
            48, "Comments"
        )
    ]
    
    monkeypatch.setattr("app.repositories.bcp.db", mock_db)
    
    result = await bcp_repo.create_or_update_impact(
        critical_activity_id=1,
        losses_financial="Financial loss",
        losses_increased_costs="Increased costs",
        losses_staffing="Staff impact",
        losses_product_service="Service impact",
        losses_reputation="Reputation loss",
        fines="Fines",
        legal_liability="Legal liability",
        rto_hours=48,
        losses_comments="Comments"
    )
    
    assert result["id"] == 1
    assert result["impact"]["id"] == 50
    assert result["impact"]["rto_hours"] == 48
    
    # Check that INSERT was called
    queries = mock_db.connection_instance.cursor_instance.executed_queries
    # First query checks for existing impact
    check_query = queries[0][0]
    assert "SELECT id FROM bcp_impact" in check_query
    # Second query inserts new impact
    insert_query = queries[1][0]
    assert "INSERT INTO bcp_impact" in insert_query


@pytest.mark.anyio
async def test_create_or_update_impact_update(monkeypatch):
    """Test updating existing impact data."""
    mock_db = _MockDB()
    
    # Mock results: existing impact found, then get activity with updated impact
    mock_db.connection_instance.cursor_instance._results = [
        (50,),  # Existing impact with id 50
        (
            1, 1, "Test Activity", "Description",
            "High", "None", 1, "Notes",
            "2024-01-01", "2024-01-01",
            50, "Updated financial", "Updated costs", "Updated staff",
            "Updated service", "Updated reputation", "Updated fines",
            "Updated legal", 72, "Updated comments"
        )
    ]
    
    monkeypatch.setattr("app.repositories.bcp.db", mock_db)
    
    result = await bcp_repo.create_or_update_impact(
        critical_activity_id=1,
        losses_financial="Updated financial",
        rto_hours=72
    )
    
    assert result["impact"]["rto_hours"] == 72
    
    # Check that UPDATE was called
    queries = mock_db.connection_instance.cursor_instance.executed_queries
    # First query checks for existing impact
    check_query = queries[0][0]
    assert "SELECT id FROM bcp_impact" in check_query
    # Second query updates existing impact
    update_query = queries[1][0]
    assert "UPDATE bcp_impact" in update_query


@pytest.mark.anyio
async def test_list_critical_activities_sort_by_priority(monkeypatch):
    """Test listing with priority sort order."""
    mock_db = _MockDB()
    mock_db.connection_instance.cursor_instance._results = [[]]
    
    monkeypatch.setattr("app.repositories.bcp.db", mock_db)
    
    await bcp_repo.list_critical_activities(1, sort_by="priority")
    
    queries = mock_db.connection_instance.cursor_instance.executed_queries
    query = queries[0][0]
    assert "FIELD(ca.priority, 'High', 'Medium', 'Low')" in query


@pytest.mark.anyio
async def test_list_critical_activities_sort_by_name(monkeypatch):
    """Test listing with name sort order."""
    mock_db = _MockDB()
    mock_db.connection_instance.cursor_instance._results = [[]]
    
    monkeypatch.setattr("app.repositories.bcp.db", mock_db)
    
    await bcp_repo.list_critical_activities(1, sort_by="name")
    
    queries = mock_db.connection_instance.cursor_instance.executed_queries
    query = queries[0][0]
    assert "ORDER BY ca.name" in query
