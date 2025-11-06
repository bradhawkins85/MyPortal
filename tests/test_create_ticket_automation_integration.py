"""Integration test for create-ticket automation with variable interpolation."""
import pytest
from datetime import datetime, timezone

from app.services import automations as automations_service
from app.services import modules as modules_service
from app.services import tickets as tickets_service
from app.services import value_templates


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_create_ticket_automation_with_interpolated_variables(monkeypatch):
    """Test end-to-end automation creating a ticket with variable interpolation."""
    created_tickets = []
    
    async def fake_create_ticket(**kwargs):
        created_tickets.append(kwargs)
        return {
            "id": 555,
            "ticket_number": "TKT-555",
            "subject": kwargs["subject"],
            "description": kwargs.get("description"),
            "priority": kwargs.get("priority", "normal"),
            "status": kwargs.get("status", "open"),
            "company_id": kwargs.get("company_id"),
            "requester_id": kwargs.get("requester_id"),
            "assigned_user_id": kwargs.get("assigned_user_id"),
        }
    
    async def fake_create_manual_event(**kwargs):
        return {"id": 100}
    
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
    
    # Simulate an automation context with company information
    context = {
        "company": {
            "id": 42,
            "name": "Acme Corporation",
        },
        "timestamp": datetime(2025, 1, 15, 14, 30, 0, tzinfo=timezone.utc).isoformat(),
    }
    
    # Template payload that would be in the automation's action_payload
    template_payload = {
        "subject": "Scheduled maintenance for {{company.name}}",
        "description": "Performing scheduled maintenance at {{timestamp}}",
        "company_id": "{{company.id}}",
        "priority": "high",
        "status": "pending",
    }
    
    # Render the template with variables (this is what the automation system does)
    rendered_payload = await value_templates.render_value_async(template_payload, context)
    
    # Now invoke the create-ticket module with the rendered payload
    result = await modules_service._invoke_create_ticket(
        settings={},
        payload=rendered_payload,
        event_future=None,
    )
    
    # Verify the automation succeeded
    assert result["status"] == "succeeded"
    assert result.get("ticket_id") == 555
    assert result.get("ticket_number") == "TKT-555"
    
    # Verify the ticket was created with interpolated values
    assert len(created_tickets) == 1
    ticket = created_tickets[0]
    assert ticket["subject"] == "Scheduled maintenance for Acme Corporation"
    assert ticket["description"] == "Performing scheduled maintenance at 2025-01-15T14:30:00+00:00"
    assert ticket["company_id"] == 42
    assert ticket["priority"] == "high"
    assert ticket["status"] == "pending"


@pytest.mark.anyio
async def test_create_ticket_automation_with_dotted_path_variables(monkeypatch):
    """Test that dotted path variables work in ticket creation payload."""
    created_tickets = []
    
    async def fake_create_ticket(**kwargs):
        created_tickets.append(kwargs)
        return {
            "id": 666,
            "ticket_number": "TKT-666",
            "subject": kwargs["subject"],
            "description": kwargs.get("description"),
        }
    
    async def fake_create_manual_event(**kwargs):
        return {"id": 101}
    
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
    
    # Context with nested data
    context = {
        "system": {
            "name": "MyPortal",
            "version": "1.0.0",
        },
        "user": {
            "id": 15,
            "email": "admin@example.com",
            "display_name": "System Admin",
        },
    }
    
    # Template with dotted paths
    template_payload = {
        "subject": "System notification from {{system.name}} v{{system.version}}",
        "description": "Automated notification sent by {{user.display_name}} ({{user.email}})",
    }
    
    # Render and invoke
    rendered_payload = await value_templates.render_value_async(template_payload, context)
    result = await modules_service._invoke_create_ticket(
        settings={},
        payload=rendered_payload,
        event_future=None,
    )
    
    # Verify
    assert result["status"] == "succeeded"
    assert len(created_tickets) == 1
    ticket = created_tickets[0]
    assert ticket["subject"] == "System notification from MyPortal v1.0.0"
    assert ticket["description"] == "Automated notification sent by System Admin (admin@example.com)"
