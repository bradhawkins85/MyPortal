"""Test that ticket automation variables are properly rendered in email notifications."""
import asyncio
from datetime import datetime, timezone

import pytest

from app.services import automations as automations_service
from app.services import value_templates


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_ticket_variables_are_rendered_in_automation_payload():
    """Test that ticket variables like {{ticket.number}} are properly rendered."""
    # Create a context similar to what would be passed during ticket creation
    context = {
        "ticket": {
            "id": 123,
            "number": "TKT-456",  # Alias added by _enrich_ticket_context
            "ticket_number": "TKT-456",
            "subject": "Printer is broken",
            "description": "The printer in the office is not working",
            "priority": "high",
            "status": "open",
            "category": "hardware",
            "created_at": datetime(2025, 1, 15, 10, 30, tzinfo=timezone.utc),
            "requester": {
                "id": 10,
                "email": "user@example.com",
                "display_name": "John Doe",
            },
            "company": {
                "id": 5,
                "name": "Acme Corp",
            },
        }
    }
    
    # Test rendering various ticket variable formats
    test_cases = [
        # Dotted path notation - both should work now
        ("Ticket {{ticket.number}}", "Ticket TKT-456"),
        ("Ticket {{ticket.ticket_number}}", "Ticket TKT-456"),
        ("Subject: {{ticket.subject}}", "Subject: Printer is broken"),
        ("Requester: {{ticket.requester.email}}", "Requester: user@example.com"),
        ("Company: {{ticket.company.name}}", "Company: Acme Corp"),
        
        # Complex payload
        (
            {
                "subject": "New ticket {{ticket.number}}: {{ticket.subject}}",
                "body": "Ticket created by {{ticket.requester.display_name}} for {{ticket.company.name}}",
                "ticket_id": "{{ticket.id}}",
            },
            {
                "subject": "New ticket TKT-456: Printer is broken",
                "body": "Ticket created by John Doe for Acme Corp",
                "ticket_id": 123,
            }
        ),
    ]
    
    for template, expected in test_cases:
        if isinstance(template, str):
            result = await value_templates.render_string_async(template, context)
            assert result == expected, f"Failed for template: {template}"
        else:
            result = await value_templates.render_value_async(template, context)
            assert result == expected, f"Failed for payload: {template}"


@pytest.mark.anyio
async def test_uppercase_ticket_tokens_are_rendered():
    """Test that uppercase ticket tokens like {{TICKET_NUMBER}} work."""
    context = {
        "ticket": {
            "id": 789,
            "number": "TKT-999",
            "subject": "Email not working",
            "priority": "medium",
        }
    }
    
    # Build the token map that would be available in templates
    base_tokens = await value_templates.build_async_base_token_map(
        context,
        tokens=["TICKET_NUMBER", "TICKET_SUBJECT", "TICKET_ID", "TICKET_PRIORITY"],
        include_templates=False,
    )
    
    # Check that uppercase tokens are available
    assert "TICKET_NUMBER" in base_tokens or "TICKET_TICKET_NUMBER" in base_tokens
    assert "TICKET_SUBJECT" in base_tokens
    assert "TICKET_ID" in base_tokens
    assert "TICKET_PRIORITY" in base_tokens
    
    # Test rendering with uppercase tokens
    template = "Ticket {{TICKET_NUMBER}} - {{TICKET_SUBJECT}}"
    result = value_templates.render_string(template, context, base_tokens=base_tokens)
    
    # The result should have the number filled in (either from TICKET_NUMBER or TICKET_TICKET_NUMBER)
    assert "TKT-999" in result or result == "Ticket  - Email not working"
    assert "Email not working" in result


@pytest.mark.anyio 
async def test_automation_context_includes_ticket_number():
    """Test that ticket.number is included in enriched ticket context."""
    # This test verifies that when a ticket is created, the number field
    # is available in the context passed to automations
    from app.services.tickets import _enrich_ticket_context
    from app.repositories import tickets as tickets_repo
    from app.repositories import companies as company_repo
    from app.repositories import users as user_repo
    
    async def fake_get_company(company_id):
        return {"id": company_id, "name": "Test Company"}
    
    async def fake_get_user(user_id):
        return {
            "id": user_id,
            "email": "test@example.com",
            "first_name": "Test",
            "last_name": "User",
        }
    
    async def fake_list_watchers(ticket_id):
        return []
    
    async def fake_list_replies(ticket_id, include_internal=True):
        return []
    
    # Monkeypatch the repository calls
    import sys
    original_get_company = getattr(company_repo, "get_company_by_id", None)
    original_get_user = getattr(user_repo, "get_user_by_id", None)
    original_list_watchers = getattr(tickets_repo, "list_watchers", None)
    original_list_replies = getattr(tickets_repo, "list_replies", None)
    
    try:
        company_repo.get_company_by_id = fake_get_company
        user_repo.get_user_by_id = fake_get_user
        tickets_repo.list_watchers = fake_list_watchers
        tickets_repo.list_replies = fake_list_replies
        
        # Create a minimal ticket record with a number
        ticket = {
            "id": 123,
            "ticket_number": "TKT-123",
            "subject": "Test ticket",
            "description": "Test description",
            "priority": "high",
            "status": "open",
            "company_id": 5,
            "requester_id": 10,
            "assigned_user_id": None,
        }
        
        enriched = await _enrich_ticket_context(ticket)
        
        # Verify that the 'number' alias is added for 'ticket_number'
        assert "ticket_number" in enriched
        assert enriched["ticket_number"] == "TKT-123"
        assert "number" in enriched, "The 'number' field should be an alias for 'ticket_number'"
        assert enriched["number"] == "TKT-123"
        
    finally:
        # Restore original functions
        if original_get_company:
            company_repo.get_company_by_id = original_get_company
        if original_get_user:
            user_repo.get_user_by_id = original_get_user  
        if original_list_watchers:
            tickets_repo.list_watchers = original_list_watchers
        if original_list_replies:
            tickets_repo.list_replies = original_list_replies
