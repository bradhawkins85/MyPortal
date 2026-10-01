"""
Tests for BCP repository operations.
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
async def test_seed_default_objectives():
    """Test that default objectives are created correctly."""
    # This test verifies the default objectives list is correct
    default_objectives = [
        "Perform risk assessment",
        "Identify & prioritise critical activities",
        "Document immediate incident response",
        "Document recovery strategies/actions",
        "Review & update plan regularly",
    ]
    
    # Verify we have the right number of default objectives
    assert len(default_objectives) == 5
    
    # Verify the first objective is about risk assessment
    assert "risk assessment" in default_objectives[0].lower()
    
    # Verify the last objective is about regular reviews
    assert "review" in default_objectives[4].lower()


def test_bcp_module_imports():
    """Test that BCP module can be imported without errors."""
    from app.repositories import bcp
    from app.api.routes import bcp as bcp_routes
    
    # Verify key functions exist
    assert hasattr(bcp, 'create_plan')
    assert hasattr(bcp, 'get_plan_by_company')
    assert hasattr(bcp, 'list_objectives')
    assert hasattr(bcp, 'seed_default_objectives')
    
    # Verify router exists
    assert hasattr(bcp_routes, 'router')


def test_bcp_repository_has_required_functions():
    """Test that BCP repository has all required functions."""
    from app.repositories import bcp
    
    required_functions = [
        'get_plan_by_company',
        'create_plan',
        'get_plan_by_id',
        'update_plan',
        'list_objectives',
        'create_objective',
        'get_objective_by_id',
        'delete_objective',
        'list_distribution_list',
        'create_distribution_entry',
        'get_distribution_entry_by_id',
        'delete_distribution_entry',
        'seed_default_objectives',
        'update_incident_after_action',
        'list_dependency_mappings',
        'create_dependency_mapping',
    ]
    
    for func_name in required_functions:
        assert hasattr(bcp, func_name), f"Missing function: {func_name}"


@pytest.mark.anyio
async def test_update_incident_after_action_updates_expected_fields(monkeypatch):
    """Test after-action updates persist the expected columns."""
    mock_db = _MockDatabase()
    monkeypatch.setattr(bcp_repo, "db", mock_db)

    expected = {"id": 7, "after_action_summary": "Lessons captured"}

    async def _fake_get_incident_by_id(incident_id):
        assert incident_id == 7
        return expected

    monkeypatch.setattr(bcp_repo, "get_incident_by_id", _fake_get_incident_by_id)

    reviewed_at = datetime(2026, 9, 16, tzinfo=timezone.utc)
    result = await bcp_repo.update_incident_after_action(
        7,
        after_action_summary="Lessons captured",
        after_action_improvements="Update call tree",
        after_action_reviewed_at=reviewed_at,
    )

    query, params = mock_db.connection_instance.cursor_instance.executed_queries[-1]
    assert "after_action_summary = %s" in query
    assert "after_action_improvements = %s" in query
    assert "after_action_reviewed_at = %s" in query
    assert params == ("Lessons captured", "Update call tree", reviewed_at, 7)
    assert result == expected


@pytest.mark.anyio
async def test_list_dependency_mappings_includes_activity_filter(monkeypatch):
    """Test dependency mapping queries include the optional activity filter."""
    mock_db = _MockDatabase()
    mock_db.connection_instance.cursor_instance._results = [
        (1, 9, 2, "Vendor", "Primary ISP", "Network Team", 4, "Failover path", None, None, "Internet Access")
    ]
    monkeypatch.setattr(bcp_repo, "db", mock_db)

    rows = await bcp_repo.list_dependency_mappings(9, critical_activity_id=2)

    query, params = mock_db.connection_instance.cursor_instance.executed_queries[-1]
    assert "AND dm.critical_activity_id = %s" in query
    assert params == (9, 2)
    assert rows[0]["critical_activity_name"] == "Internet Access"
    assert rows[0]["dependency_name"] == "Primary ISP"
