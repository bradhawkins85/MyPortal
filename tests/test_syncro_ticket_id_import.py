"""
Test that Syncro ticket imports use the Syncro ticket number as the database ID.
"""
import pytest

from app.services import ticket_importer


def test_extract_numeric_ticket_id_from_number():
    """Test extracting numeric ID from ticket 'number' field."""
    ticket = {"id": 999, "number": "12345"}
    assert ticket_importer._extract_numeric_ticket_id(ticket) == 12345


def test_extract_numeric_ticket_id_from_id():
    """Test extracting numeric ID from ticket 'id' field as fallback."""
    ticket = {"id": 54321}
    assert ticket_importer._extract_numeric_ticket_id(ticket) == 54321


def test_extract_numeric_ticket_id_with_non_numeric_number():
    """Test extracting digits from non-numeric ticket numbers."""
    ticket = {"id": 100, "number": "TKT-789"}
    # Should extract digits from the number
    assert ticket_importer._extract_numeric_ticket_id(ticket) == 789


def test_extract_numeric_ticket_id_returns_none_for_invalid():
    """Test that None is returned when no valid ID can be extracted."""
    ticket = {"id": "not-a-number", "number": "ABC"}
    assert ticket_importer._extract_numeric_ticket_id(ticket) is None


def test_extract_numeric_ticket_id_empty_ticket():
    """Test that None is returned for empty ticket."""
    ticket = {}
    assert ticket_importer._extract_numeric_ticket_id(ticket) is None


@pytest.mark.anyio
async def test_syncro_ticket_import_uses_syncro_id(monkeypatch):
    """Test that importing a Syncro ticket uses the Syncro ticket number as the database ID."""
    from app.services import syncro
    from app.services import tickets as tickets_service
    from app.repositories import tickets as tickets_repo
    from app.repositories import companies as company_repo
    from app.repositories import users as user_repo

    async def fake_get_ticket(ticket_id, rate_limiter=None):
        return {
            "id": 12345,
            "number": "12345",
            "subject": "Test ticket",
            "priority": "Normal",
            "status": "Open",
            "problem": "Test description",
            "customer_id": "200",
        }

    async def fake_get_company(syncro_id):
        return {"id": 1}

    async def fake_get_company_by_name(_name):
        return None

    async def fake_get_existing(external_reference):
        return None

    created_ticket_data = {}

    async def fake_create_ticket(**kwargs):
        created_ticket_data.update(kwargs)
        # Return the created ticket with the ID that was passed
        return {"id": kwargs.get("id") or 999, **kwargs}

    async def fake_update_ticket(ticket_id, **fields):
        return {"id": ticket_id, **fields}

    async def fake_get_user_by_email(_email):
        return None

    async def fake_emit_event(*args, **kwargs):
        return None

    async def fake_refresh_summary(ticket_id):
        return None

    async def fake_refresh_tags(ticket_id):
        return None

    monkeypatch.setattr(syncro, "get_ticket", fake_get_ticket)
    monkeypatch.setattr(company_repo, "get_company_by_syncro_id", fake_get_company)
    monkeypatch.setattr(company_repo, "get_company_by_name", fake_get_company_by_name)
    monkeypatch.setattr(tickets_repo, "get_ticket_by_external_reference", fake_get_existing)
    monkeypatch.setattr(tickets_service, "create_ticket", fake_create_ticket)
    monkeypatch.setattr(tickets_repo, "update_ticket", fake_update_ticket)
    monkeypatch.setattr(user_repo, "get_user_by_email", fake_get_user_by_email)
    monkeypatch.setattr(tickets_service, "emit_ticket_updated_event", fake_emit_event)
    monkeypatch.setattr(tickets_service, "refresh_ticket_ai_summary", fake_refresh_summary)
    monkeypatch.setattr(tickets_service, "refresh_ticket_ai_tags", fake_refresh_tags)

    # Import the ticket
    summary = await ticket_importer.import_ticket_by_id(12345, rate_limiter=None)

    # Verify the ticket was created
    assert summary.created == 1
    assert summary.updated == 0
    assert summary.skipped == 0

    # Verify the ID was set to the Syncro ticket number
    assert created_ticket_data.get("id") == 12345
    assert created_ticket_data.get("ticket_number") == "12345"
    assert created_ticket_data.get("external_reference") == "12345"


@pytest.fixture
def anyio_backend():
    return "asyncio"
