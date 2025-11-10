"""
Tests for BCP insurance and backup repository operations.
"""
import pytest
from datetime import datetime
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
async def test_list_insurance_policies(monkeypatch):
    """Test listing insurance policies."""
    mock_db = _MockDatabase()
    monkeypatch.setattr('app.repositories.bcp.db', mock_db)
    
    # Mock data - fetchall returns a list of tuples (one per row)
    mock_db.connection_instance.cursor_instance._results = [
        (1, 1, "Property", "Building coverage", "Flood excluded", 
         "ABC Insurance", "123-456-7890", datetime(2024, 1, 1), 
         "Annual", datetime(2024, 1, 1), datetime(2024, 1, 1))
    ]
    
    policies = await bcp_repo.list_insurance_policies(1)
    
    assert len(policies) == 1
    assert policies[0]["type"] == "Property"
    assert policies[0]["insurer"] == "ABC Insurance"


@pytest.mark.anyio
async def test_create_insurance_policy(monkeypatch):
    """Test creating an insurance policy."""
    mock_db = _MockDatabase()
    monkeypatch.setattr('app.repositories.bcp.db', mock_db)
    
    # Mock the get_insurance_policy_by_id call - fetchone returns a single tuple
    mock_db.connection_instance.cursor_instance._results = [
        (1, 1, "Liability", "General liability", None, "XYZ Insurance", 
         "contact@xyz.com", None, "Monthly", datetime(2024, 1, 1), datetime(2024, 1, 1))
    ]
    
    policy = await bcp_repo.create_insurance_policy(
        plan_id=1,
        policy_type="Liability",
        coverage="General liability",
        insurer="XYZ Insurance",
        contact="contact@xyz.com",
        payment_terms="Monthly"
    )
    
    assert policy is not None
    assert policy["type"] == "Liability"
    assert policy["insurer"] == "XYZ Insurance"


@pytest.mark.anyio
async def test_delete_insurance_policy(monkeypatch):
    """Test deleting an insurance policy."""
    mock_db = _MockDatabase()
    monkeypatch.setattr('app.repositories.bcp.db', mock_db)
    
    mock_db.connection_instance.cursor_instance.rowcount = 1
    
    result = await bcp_repo.delete_insurance_policy(1)
    
    assert result is True


@pytest.mark.anyio
async def test_list_backup_items(monkeypatch):
    """Test listing backup items."""
    mock_db = _MockDatabase()
    monkeypatch.setattr('app.repositories.bcp.db', mock_db)
    
    # Mock data - fetchall returns a list of tuples (one per row)
    mock_db.connection_instance.cursor_instance._results = [
        (1, 1, "Customer Database", "Daily", "AWS S3", "IT Team", 
         "1. Connect to server\n2. Run backup script", 
         datetime(2024, 1, 1), datetime(2024, 1, 1))
    ]
    
    backups = await bcp_repo.list_backup_items(1)
    
    assert len(backups) == 1
    assert backups[0]["data_scope"] == "Customer Database"
    assert backups[0]["frequency"] == "Daily"
    assert backups[0]["medium"] == "AWS S3"


@pytest.mark.anyio
async def test_create_backup_item(monkeypatch):
    """Test creating a backup item."""
    mock_db = _MockDatabase()
    monkeypatch.setattr('app.repositories.bcp.db', mock_db)
    
    # Mock the get_backup_item_by_id call - fetchone returns a single tuple
    mock_db.connection_instance.cursor_instance._results = [
        (1, 1, "Financial Records", "Weekly", "Local NAS", "Finance Team",
         "Backup procedure steps", datetime(2024, 1, 1), datetime(2024, 1, 1))
    ]
    
    backup = await bcp_repo.create_backup_item(
        plan_id=1,
        data_scope="Financial Records",
        frequency="Weekly",
        medium="Local NAS",
        owner="Finance Team",
        steps="Backup procedure steps"
    )
    
    assert backup is not None
    assert backup["data_scope"] == "Financial Records"
    assert backup["frequency"] == "Weekly"


@pytest.mark.anyio
async def test_delete_backup_item(monkeypatch):
    """Test deleting a backup item."""
    mock_db = _MockDatabase()
    monkeypatch.setattr('app.repositories.bcp.db', mock_db)
    
    mock_db.connection_instance.cursor_instance.rowcount = 1
    
    result = await bcp_repo.delete_backup_item(1)
    
    assert result is True


def test_insurance_backup_module_imports():
    """Test that insurance and backup functions can be imported."""
    from app.repositories import bcp
    
    # Verify insurance functions exist
    assert hasattr(bcp, 'list_insurance_policies')
    assert hasattr(bcp, 'create_insurance_policy')
    assert hasattr(bcp, 'update_insurance_policy')
    assert hasattr(bcp, 'delete_insurance_policy')
    assert hasattr(bcp, 'get_insurance_policy_by_id')
    
    # Verify backup functions exist
    assert hasattr(bcp, 'list_backup_items')
    assert hasattr(bcp, 'create_backup_item')
    assert hasattr(bcp, 'update_backup_item')
    assert hasattr(bcp, 'delete_backup_item')
    assert hasattr(bcp, 'get_backup_item_by_id')


@pytest.mark.anyio
async def test_update_insurance_policy(monkeypatch):
    """Test updating an insurance policy."""
    mock_db = _MockDatabase()
    monkeypatch.setattr('app.repositories.bcp.db', mock_db)
    
    # Mock the get_insurance_policy_by_id call - fetchone returns a single tuple
    mock_db.connection_instance.cursor_instance._results = [
        (1, 1, "Property Updated", "New coverage", "New exclusions", 
         "Updated Insurance", "new-contact", datetime(2024, 6, 1), 
         "Quarterly", datetime(2024, 1, 1), datetime(2024, 6, 1))
    ]
    
    policy = await bcp_repo.update_insurance_policy(
        policy_id=1,
        policy_type="Property Updated",
        coverage="New coverage",
        insurer="Updated Insurance"
    )
    
    assert policy is not None
    assert policy["type"] == "Property Updated"


@pytest.mark.anyio
async def test_update_backup_item(monkeypatch):
    """Test updating a backup item."""
    mock_db = _MockDatabase()
    monkeypatch.setattr('app.repositories.bcp.db', mock_db)
    
    # Mock the get_backup_item_by_id call - fetchone returns a single tuple
    mock_db.connection_instance.cursor_instance._results = [
        (1, 1, "Updated Data", "Hourly", "Azure Backup", "New Team",
         "Updated steps", datetime(2024, 1, 1), datetime(2024, 6, 1))
    ]
    
    backup = await bcp_repo.update_backup_item(
        backup_id=1,
        data_scope="Updated Data",
        frequency="Hourly",
        medium="Azure Backup"
    )
    
    assert backup is not None
    assert backup["data_scope"] == "Updated Data"
    assert backup["frequency"] == "Hourly"
