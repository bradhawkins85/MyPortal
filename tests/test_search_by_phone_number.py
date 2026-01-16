import pytest
from datetime import datetime, timezone

from app.repositories import tickets


class _PhoneSearchDB:
    def __init__(self, rows):
        self.fetch_sql = None
        self.fetch_params = None
        self._rows = rows

    async def fetch_all(self, sql, params):
        self.fetch_sql = sql.strip()
        self.fetch_params = params
        return self._rows


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_list_tickets_by_requester_phone_normalizes_phone_number(monkeypatch):
    """Test that phone number search normalizes the input"""
    sample_rows = [
        {
            "id": 1,
            "company_id": 10,
            "requester_id": 5,
            "assigned_user_id": None,
            "subject": "Test Ticket 1",
            "description": "First ticket",
            "status": "open",
            "priority": "normal",
            "category": None,
            "module_slug": None,
            "external_reference": None,
            "ai_summary": None,
            "ai_summary_status": None,
            "ai_summary_model": None,
            "ai_resolution_state": None,
            "ai_summary_updated_at": None,
            "ai_tags": None,
            "ai_tags_status": None,
            "ai_tags_model": None,
            "ai_tags_updated_at": None,
            "created_at": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc),
            "closed_at": None,
        }
    ]
    dummy_db = _PhoneSearchDB(sample_rows)
    monkeypatch.setattr(tickets, "db", dummy_db)

    # Search with formatted phone number
    results = await tickets.list_tickets_by_requester_phone("+61 412 345 678", limit=100)

    assert len(results) == 1
    assert results[0]["id"] == 1
    assert results[0]["subject"] == "Test Ticket 1"
    
    # Check that the SQL query is correct
    assert "SELECT t.*" in dummy_db.fetch_sql
    assert "FROM tickets AS t" in dummy_db.fetch_sql
    assert "INNER JOIN users AS u ON u.id = t.requester_id" in dummy_db.fetch_sql
    assert "WHERE u.mobile_phone LIKE %s" in dummy_db.fetch_sql
    assert "ORDER BY t.updated_at DESC" in dummy_db.fetch_sql
    assert "LIMIT %s" in dummy_db.fetch_sql
    
    # Check that phone number is normalized (spaces and + removed)
    assert dummy_db.fetch_params[0] == "%61412345678%"
    assert dummy_db.fetch_params[1] == 100


@pytest.mark.anyio
async def test_list_tickets_by_requester_phone_removes_formatting(monkeypatch):
    """Test that phone number search strips common formatting characters"""
    sample_rows = []
    dummy_db = _PhoneSearchDB(sample_rows)
    monkeypatch.setattr(tickets, "db", dummy_db)

    # Search with phone number containing brackets and dashes
    await tickets.list_tickets_by_requester_phone("(02) 1234-5678", limit=50)

    # Check that formatting characters are removed
    assert dummy_db.fetch_params[0] == "%0212345678%"
    assert dummy_db.fetch_params[1] == 50


@pytest.mark.anyio
async def test_list_tickets_by_requester_phone_returns_empty_for_empty_input(monkeypatch):
    """Test that empty phone number returns empty list"""
    dummy_db = _PhoneSearchDB([])
    monkeypatch.setattr(tickets, "db", dummy_db)

    # Test empty string
    results = await tickets.list_tickets_by_requester_phone("")
    assert results == []
    assert dummy_db.fetch_sql is None

    # Test whitespace only
    results = await tickets.list_tickets_by_requester_phone("   ")
    assert results == []
    assert dummy_db.fetch_sql is None


@pytest.mark.anyio
async def test_list_tickets_by_requester_phone_returns_multiple_tickets(monkeypatch):
    """Test that multiple tickets are returned and ordered correctly"""
    sample_rows = [
        {
            "id": 2,
            "company_id": 10,
            "requester_id": 5,
            "assigned_user_id": None,
            "subject": "Newer Ticket",
            "description": "More recent ticket",
            "status": "open",
            "priority": "normal",
            "category": None,
            "module_slug": None,
            "external_reference": None,
            "ai_summary": None,
            "ai_summary_status": None,
            "ai_summary_model": None,
            "ai_resolution_state": None,
            "ai_summary_updated_at": None,
            "ai_tags": None,
            "ai_tags_status": None,
            "ai_tags_model": None,
            "ai_tags_updated_at": None,
            "created_at": datetime(2024, 1, 2, tzinfo=timezone.utc),
            "updated_at": datetime(2024, 1, 2, tzinfo=timezone.utc),
            "closed_at": None,
        },
        {
            "id": 1,
            "company_id": 10,
            "requester_id": 5,
            "assigned_user_id": None,
            "subject": "Older Ticket",
            "description": "Earlier ticket",
            "status": "closed",
            "priority": "normal",
            "category": None,
            "module_slug": None,
            "external_reference": None,
            "ai_summary": None,
            "ai_summary_status": None,
            "ai_summary_model": None,
            "ai_resolution_state": None,
            "ai_summary_updated_at": None,
            "ai_tags": None,
            "ai_tags_status": None,
            "ai_tags_model": None,
            "ai_tags_updated_at": None,
            "created_at": datetime(2024, 1, 1, tzinfo=timezone.utc),
            "updated_at": datetime(2024, 1, 1, tzinfo=timezone.utc),
            "closed_at": None,
        },
    ]
    dummy_db = _PhoneSearchDB(sample_rows)
    monkeypatch.setattr(tickets, "db", dummy_db)

    results = await tickets.list_tickets_by_requester_phone("0412345678")

    assert len(results) == 2
    # Results should be returned in the order from the database (newest first)
    assert results[0]["id"] == 2
    assert results[0]["subject"] == "Newer Ticket"
    assert results[1]["id"] == 1
    assert results[1]["subject"] == "Older Ticket"
