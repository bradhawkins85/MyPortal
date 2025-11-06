"""Test the create-ticket automation module."""
import pytest
from datetime import datetime, timezone
from typing import Any

from app.services import modules as modules_service
from app.services import tickets as tickets_service


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_create_ticket_module_with_minimal_payload(monkeypatch):
    """Test creating a ticket with just a subject."""
    created_tickets = []
    
    async def fake_create_ticket(**kwargs):
        created_tickets.append(kwargs)
        return {
            "id": 123,
            "ticket_number": "TKT-123",
            "subject": kwargs["subject"],
            "description": kwargs.get("description"),
            "priority": kwargs.get("priority", "normal"),
            "status": kwargs.get("status", "open"),
            "company_id": kwargs.get("company_id"),
            "requester_id": kwargs.get("requester_id"),
            "assigned_user_id": kwargs.get("assigned_user_id"),
        }
    
    async def fake_create_manual_event(**kwargs):
        return {"id": 1}
    
    async def fake_record_attempt(*args, **kwargs):
        pass
    
    async def fake_mark_event_completed(*args, **kwargs):
        pass
    
    async def fake_get_event(event_id):
        return {
            "id": event_id,
            "status": "succeeded",
            "response_status": 200,
        }
    
    monkeypatch.setattr(tickets_service, "create_ticket", fake_create_ticket)
    monkeypatch.setattr(
        modules_service.webhook_monitor,
        "create_manual_event",
        fake_create_manual_event,
    )
    monkeypatch.setattr(
        modules_service.webhook_repo,
        "record_attempt",
        fake_record_attempt,
    )
    monkeypatch.setattr(
        modules_service.webhook_repo,
        "mark_event_completed",
        fake_mark_event_completed,
    )
    monkeypatch.setattr(
        modules_service.webhook_repo,
        "get_event",
        fake_get_event,
    )
    
    # Test with minimal payload
    result = await modules_service._invoke_create_ticket(
        settings={},
        payload={"subject": "Test ticket"},
        event_future=None,
    )
    
    assert result["status"] == "succeeded"
    assert result.get("ticket_id") == 123
    assert result.get("ticket_number") == "TKT-123"
    assert len(created_tickets) == 1
    assert created_tickets[0]["subject"] == "Test ticket"
    assert created_tickets[0]["priority"] == "normal"
    assert created_tickets[0]["status"] == "open"
    assert created_tickets[0]["trigger_automations"] is False


@pytest.mark.anyio
async def test_create_ticket_module_with_full_payload(monkeypatch):
    """Test creating a ticket with all optional fields."""
    created_tickets = []
    
    async def fake_create_ticket(**kwargs):
        created_tickets.append(kwargs)
        return {
            "id": 456,
            "ticket_number": "TKT-456",
            "subject": kwargs["subject"],
            "description": kwargs.get("description"),
            "priority": kwargs.get("priority", "normal"),
            "status": kwargs.get("status", "open"),
            "company_id": kwargs.get("company_id"),
            "requester_id": kwargs.get("requester_id"),
            "assigned_user_id": kwargs.get("assigned_user_id"),
            "category": kwargs.get("category"),
            "module_slug": kwargs.get("module_slug"),
            "external_reference": kwargs.get("external_reference"),
        }
    
    async def fake_create_manual_event(**kwargs):
        return {"id": 2}
    
    async def fake_record_attempt(*args, **kwargs):
        pass
    
    async def fake_mark_event_completed(*args, **kwargs):
        pass
    
    async def fake_get_event(event_id):
        return {
            "id": event_id,
            "status": "succeeded",
            "response_status": 200,
        }
    
    monkeypatch.setattr(tickets_service, "create_ticket", fake_create_ticket)
    monkeypatch.setattr(
        modules_service.webhook_monitor,
        "create_manual_event",
        fake_create_manual_event,
    )
    monkeypatch.setattr(
        modules_service.webhook_repo,
        "record_attempt",
        fake_record_attempt,
    )
    monkeypatch.setattr(
        modules_service.webhook_repo,
        "mark_event_completed",
        fake_mark_event_completed,
    )
    monkeypatch.setattr(
        modules_service.webhook_repo,
        "get_event",
        fake_get_event,
    )
    
    # Test with full payload
    result = await modules_service._invoke_create_ticket(
        settings={},
        payload={
            "subject": "Printer issue",
            "description": "The office printer is not working",
            "company_id": 5,
            "requester_id": 10,
            "assigned_user_id": 20,
            "priority": "high",
            "status": "in_progress",
            "category": "hardware",
            "module_slug": "syncro",
            "external_reference": "EXT-123",
        },
        event_future=None,
    )
    
    assert result["status"] == "succeeded"
    assert result.get("ticket_id") == 456
    assert result.get("ticket_number") == "TKT-456"
    assert len(created_tickets) == 1
    ticket = created_tickets[0]
    assert ticket["subject"] == "Printer issue"
    assert ticket["description"] == "The office printer is not working"
    assert ticket["company_id"] == 5
    assert ticket["requester_id"] == 10
    assert ticket["assigned_user_id"] == 20
    assert ticket["priority"] == "high"
    assert ticket["status"] == "in_progress"
    assert ticket["category"] == "hardware"
    assert ticket["module_slug"] == "syncro"
    assert ticket["external_reference"] == "EXT-123"


@pytest.mark.anyio
async def test_create_ticket_module_missing_subject(monkeypatch):
    """Test that missing subject raises an error."""
    async def fake_create_manual_event(**kwargs):
        return {"id": 3}
    
    monkeypatch.setattr(
        modules_service.webhook_monitor,
        "create_manual_event",
        fake_create_manual_event,
    )
    
    with pytest.raises(ValueError, match="Ticket subject is required"):
        await modules_service._invoke_create_ticket(
            settings={},
            payload={},
            event_future=None,
        )
    
    with pytest.raises(ValueError, match="Ticket subject is required"):
        await modules_service._invoke_create_ticket(
            settings={},
            payload={"subject": ""},
            event_future=None,
        )


@pytest.mark.anyio
async def test_create_ticket_module_handles_type_conversion(monkeypatch):
    """Test that the module handles type conversions properly."""
    created_tickets = []
    
    async def fake_create_ticket(**kwargs):
        created_tickets.append(kwargs)
        return {
            "id": 789,
            "ticket_number": "TKT-789",
            "subject": kwargs["subject"],
            "company_id": kwargs.get("company_id"),
            "requester_id": kwargs.get("requester_id"),
        }
    
    async def fake_create_manual_event(**kwargs):
        return {"id": 4}
    
    async def fake_record_attempt(*args, **kwargs):
        pass
    
    async def fake_mark_event_completed(*args, **kwargs):
        pass
    
    async def fake_get_event(event_id):
        return {
            "id": event_id,
            "status": "succeeded",
            "response_status": 200,
        }
    
    monkeypatch.setattr(tickets_service, "create_ticket", fake_create_ticket)
    monkeypatch.setattr(
        modules_service.webhook_monitor,
        "create_manual_event",
        fake_create_manual_event,
    )
    monkeypatch.setattr(
        modules_service.webhook_repo,
        "record_attempt",
        fake_record_attempt,
    )
    monkeypatch.setattr(
        modules_service.webhook_repo,
        "mark_event_completed",
        fake_mark_event_completed,
    )
    monkeypatch.setattr(
        modules_service.webhook_repo,
        "get_event",
        fake_get_event,
    )
    
    # Test with string IDs that should be converted to integers
    result = await modules_service._invoke_create_ticket(
        settings={},
        payload={
            "subject": "Test",
            "company_id": "5",
            "requester_id": "10",
        },
        event_future=None,
    )
    
    assert result["status"] == "succeeded"
    assert len(created_tickets) == 1
    ticket = created_tickets[0]
    assert ticket["company_id"] == 5
    assert ticket["requester_id"] == 10


@pytest.mark.anyio
async def test_create_ticket_module_with_variables(monkeypatch):
    """Test creating a ticket with variables already interpolated."""
    created_tickets = []
    
    async def fake_create_ticket(**kwargs):
        created_tickets.append(kwargs)
        return {
            "id": 999,
            "ticket_number": "TKT-999",
            "subject": kwargs["subject"],
            "description": kwargs.get("description"),
        }
    
    async def fake_create_manual_event(**kwargs):
        return {"id": 5}
    
    async def fake_record_attempt(*args, **kwargs):
        pass
    
    async def fake_mark_event_completed(*args, **kwargs):
        pass
    
    async def fake_get_event(event_id):
        return {
            "id": event_id,
            "status": "succeeded",
            "response_status": 200,
        }
    
    monkeypatch.setattr(tickets_service, "create_ticket", fake_create_ticket)
    monkeypatch.setattr(
        modules_service.webhook_monitor,
        "create_manual_event",
        fake_create_manual_event,
    )
    monkeypatch.setattr(
        modules_service.webhook_repo,
        "record_attempt",
        fake_record_attempt,
    )
    monkeypatch.setattr(
        modules_service.webhook_repo,
        "mark_event_completed",
        fake_mark_event_completed,
    )
    monkeypatch.setattr(
        modules_service.webhook_repo,
        "get_event",
        fake_get_event,
    )
    
    # Test with variables that have already been interpolated by the automation system
    result = await modules_service._invoke_create_ticket(
        settings={},
        payload={
            "subject": "Scheduled maintenance for Acme Corp",
            "description": "Performing scheduled maintenance at 2025-01-15 10:00 UTC",
            "company_id": 5,
        },
        event_future=None,
    )
    
    assert result["status"] == "succeeded"
    assert len(created_tickets) == 1
    ticket = created_tickets[0]
    assert ticket["subject"] == "Scheduled maintenance for Acme Corp"
    assert ticket["description"] == "Performing scheduled maintenance at 2025-01-15 10:00 UTC"


@pytest.mark.anyio
async def test_create_ticket_module_error_handling(monkeypatch):
    """Test error handling when ticket creation fails."""
    async def fake_create_ticket(**kwargs):
        raise RuntimeError("Database connection failed")
    
    async def fake_create_manual_event(**kwargs):
        return {"id": 6}
    
    async def fake_record_attempt(*args, **kwargs):
        pass
    
    async def fake_mark_event_failed(*args, **kwargs):
        pass
    
    async def fake_get_event(event_id):
        return {
            "id": event_id,
            "status": "failed",
            "last_error": "Database connection failed",
        }
    
    monkeypatch.setattr(tickets_service, "create_ticket", fake_create_ticket)
    monkeypatch.setattr(
        modules_service.webhook_monitor,
        "create_manual_event",
        fake_create_manual_event,
    )
    monkeypatch.setattr(
        modules_service.webhook_repo,
        "record_attempt",
        fake_record_attempt,
    )
    monkeypatch.setattr(
        modules_service.webhook_repo,
        "mark_event_failed",
        fake_mark_event_failed,
    )
    monkeypatch.setattr(
        modules_service.webhook_repo,
        "get_event",
        fake_get_event,
    )
    
    result = await modules_service._invoke_create_ticket(
        settings={},
        payload={"subject": "Test ticket"},
        event_future=None,
    )
    
    assert result["status"] == "failed" or result.get("last_error") == "Database connection failed"
    assert "subject" in result


@pytest.mark.anyio
async def test_create_ticket_module_creates_initial_reply_when_requester_and_description_provided(monkeypatch):
    """Test that initial conversation history is created when requester and description are provided."""
    created_tickets = []
    
    async def fake_create_ticket(**kwargs):
        created_tickets.append(kwargs)
        return {
            "id": 1001,
            "ticket_number": "TKT-1001",
            "subject": kwargs["subject"],
            "description": kwargs.get("description"),
            "requester_id": kwargs.get("requester_id"),
        }
    
    async def fake_create_manual_event(**kwargs):
        return {"id": 7}
    
    async def fake_record_attempt(*args, **kwargs):
        pass
    
    async def fake_mark_event_completed(*args, **kwargs):
        pass
    
    async def fake_get_event(event_id):
        return {
            "id": event_id,
            "status": "succeeded",
            "response_status": 200,
        }
    
    monkeypatch.setattr(tickets_service, "create_ticket", fake_create_ticket)
    monkeypatch.setattr(
        modules_service.webhook_monitor,
        "create_manual_event",
        fake_create_manual_event,
    )
    monkeypatch.setattr(
        modules_service.webhook_repo,
        "record_attempt",
        fake_record_attempt,
    )
    monkeypatch.setattr(
        modules_service.webhook_repo,
        "mark_event_completed",
        fake_mark_event_completed,
    )
    monkeypatch.setattr(
        modules_service.webhook_repo,
        "get_event",
        fake_get_event,
    )
    
    # Test with requester and description - should pass initial_reply_author_id
    result = await modules_service._invoke_create_ticket(
        settings={},
        payload={
            "subject": "Scheduled maintenance",
            "description": "Performing scheduled server maintenance",
            "requester_id": 15,
        },
        event_future=None,
    )
    
    assert result["status"] == "succeeded"
    assert result.get("ticket_id") == 1001
    assert len(created_tickets) == 1
    ticket = created_tickets[0]
    assert ticket["subject"] == "Scheduled maintenance"
    assert ticket["description"] == "Performing scheduled server maintenance"
    assert ticket["requester_id"] == 15
    assert ticket["initial_reply_author_id"] == 15


@pytest.mark.anyio
async def test_create_ticket_module_no_initial_reply_when_no_requester(monkeypatch):
    """Test that no initial conversation history is created when requester is missing."""
    created_tickets = []
    
    async def fake_create_ticket(**kwargs):
        created_tickets.append(kwargs)
        return {
            "id": 1002,
            "ticket_number": "TKT-1002",
            "subject": kwargs["subject"],
            "description": kwargs.get("description"),
        }
    
    async def fake_create_manual_event(**kwargs):
        return {"id": 8}
    
    async def fake_record_attempt(*args, **kwargs):
        pass
    
    async def fake_mark_event_completed(*args, **kwargs):
        pass
    
    async def fake_get_event(event_id):
        return {
            "id": event_id,
            "status": "succeeded",
            "response_status": 200,
        }
    
    monkeypatch.setattr(tickets_service, "create_ticket", fake_create_ticket)
    monkeypatch.setattr(
        modules_service.webhook_monitor,
        "create_manual_event",
        fake_create_manual_event,
    )
    monkeypatch.setattr(
        modules_service.webhook_repo,
        "record_attempt",
        fake_record_attempt,
    )
    monkeypatch.setattr(
        modules_service.webhook_repo,
        "mark_event_completed",
        fake_mark_event_completed,
    )
    monkeypatch.setattr(
        modules_service.webhook_repo,
        "get_event",
        fake_get_event,
    )
    
    # Test without requester - should not pass initial_reply_author_id
    result = await modules_service._invoke_create_ticket(
        settings={},
        payload={
            "subject": "System alert",
            "description": "Automated system notification",
        },
        event_future=None,
    )
    
    assert result["status"] == "succeeded"
    assert len(created_tickets) == 1
    ticket = created_tickets[0]
    assert ticket["subject"] == "System alert"
    assert ticket["description"] == "Automated system notification"
    assert ticket["requester_id"] is None
    assert ticket["initial_reply_author_id"] is None


@pytest.mark.anyio
async def test_create_ticket_module_no_initial_reply_when_no_description(monkeypatch):
    """Test that no initial conversation history is created when description is missing."""
    created_tickets = []
    
    async def fake_create_ticket(**kwargs):
        created_tickets.append(kwargs)
        return {
            "id": 1003,
            "ticket_number": "TKT-1003",
            "subject": kwargs["subject"],
            "requester_id": kwargs.get("requester_id"),
        }
    
    async def fake_create_manual_event(**kwargs):
        return {"id": 9}
    
    async def fake_record_attempt(*args, **kwargs):
        pass
    
    async def fake_mark_event_completed(*args, **kwargs):
        pass
    
    async def fake_get_event(event_id):
        return {
            "id": event_id,
            "status": "succeeded",
            "response_status": 200,
        }
    
    monkeypatch.setattr(tickets_service, "create_ticket", fake_create_ticket)
    monkeypatch.setattr(
        modules_service.webhook_monitor,
        "create_manual_event",
        fake_create_manual_event,
    )
    monkeypatch.setattr(
        modules_service.webhook_repo,
        "record_attempt",
        fake_record_attempt,
    )
    monkeypatch.setattr(
        modules_service.webhook_repo,
        "mark_event_completed",
        fake_mark_event_completed,
    )
    monkeypatch.setattr(
        modules_service.webhook_repo,
        "get_event",
        fake_get_event,
    )
    
    # Test without description - should not pass initial_reply_author_id
    result = await modules_service._invoke_create_ticket(
        settings={},
        payload={
            "subject": "Empty ticket",
            "requester_id": 20,
        },
        event_future=None,
    )
    
    assert result["status"] == "succeeded"
    assert len(created_tickets) == 1
    ticket = created_tickets[0]
    assert ticket["subject"] == "Empty ticket"
    assert ticket["description"] is None
    assert ticket["requester_id"] == 20
    assert ticket["initial_reply_author_id"] is None
