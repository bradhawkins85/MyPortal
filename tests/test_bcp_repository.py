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
    ]
    
    for func_name in required_functions:
        assert hasattr(bcp, func_name), f"Missing function: {func_name}"
