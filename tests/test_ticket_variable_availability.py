"""Test that all ticket variables are consistently available in templates."""
import pytest

from app.services import value_templates
from app.services.tickets import _enrich_ticket_context
from app.repositories import tickets as tickets_repo
from app.repositories import companies as company_repo
from app.repositories import users as user_repo


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_ticket_number_is_always_available(monkeypatch):
    """Test that ticket.number is always available, even when ticket_number is None."""
    
    async def fake_get_company(company_id):
        return None
    
    async def fake_get_user(user_id):
        return None
    
    async def fake_list_watchers(ticket_id):
        return []
    
    async def fake_list_replies(ticket_id, include_internal=True):
        return []
    
    # Monkeypatch the repository calls
    monkeypatch.setattr(company_repo, "get_company_by_id", fake_get_company)
    monkeypatch.setattr(user_repo, "get_user_by_id", fake_get_user)
    monkeypatch.setattr(tickets_repo, "list_watchers", fake_list_watchers)
    monkeypatch.setattr(tickets_repo, "list_replies", fake_list_replies)
    
    # Create a ticket without a ticket_number
    ticket = {
        "id": 123,
        "ticket_number": None,  # This is None, simulating a ticket without a number
        "subject": "Test ticket",
        "description": "Test description",
        "priority": "high",
        "status": "open",
    }
    
    enriched = await _enrich_ticket_context(ticket)
    
    # Verify that 'number' field is present (even if None)
    assert "number" in enriched, "The 'number' field should always be present"
    assert enriched["number"] is None, "The 'number' field should be None when ticket_number is None"
    
    # Verify that 'ticket_number' is still accessible
    assert "ticket_number" in enriched
    assert enriched["ticket_number"] is None


@pytest.mark.anyio
async def test_ticket_number_with_value(monkeypatch):
    """Test that ticket.number correctly maps to ticket_number when it has a value."""
    
    async def fake_get_company(company_id):
        return None
    
    async def fake_get_user(user_id):
        return None
    
    async def fake_list_watchers(ticket_id):
        return []
    
    async def fake_list_replies(ticket_id, include_internal=True):
        return []
    
    # Monkeypatch the repository calls
    monkeypatch.setattr(company_repo, "get_company_by_id", fake_get_company)
    monkeypatch.setattr(user_repo, "get_user_by_id", fake_get_user)
    monkeypatch.setattr(tickets_repo, "list_watchers", fake_list_watchers)
    monkeypatch.setattr(tickets_repo, "list_replies", fake_list_replies)
    
    # Create a ticket with a ticket_number
    ticket = {
        "id": 456,
        "ticket_number": "TKT-456",
        "subject": "Test ticket with number",
        "description": "Test description",
        "priority": "low",
        "status": "open",
    }
    
    enriched = await _enrich_ticket_context(ticket)
    
    # Verify that 'number' field is present and has the correct value
    assert "number" in enriched
    assert enriched["number"] == "TKT-456"
    
    # Verify that 'ticket_number' is still accessible
    assert "ticket_number" in enriched
    assert enriched["ticket_number"] == "TKT-456"


@pytest.mark.anyio
async def test_labels_alias_for_ai_tags(monkeypatch):
    """Test that ticket.labels is an alias for ticket.ai_tags."""
    
    async def fake_get_company(company_id):
        return None
    
    async def fake_get_user(user_id):
        return None
    
    async def fake_list_watchers(ticket_id):
        return []
    
    async def fake_list_replies(ticket_id, include_internal=True):
        return []
    
    # Monkeypatch the repository calls
    monkeypatch.setattr(company_repo, "get_company_by_id", fake_get_company)
    monkeypatch.setattr(user_repo, "get_user_by_id", fake_get_user)
    monkeypatch.setattr(tickets_repo, "list_watchers", fake_list_watchers)
    monkeypatch.setattr(tickets_repo, "list_replies", fake_list_replies)
    
    # Create a ticket with ai_tags
    ticket = {
        "id": 789,
        "ticket_number": "TKT-789",
        "subject": "Test ticket with tags",
        "description": "Test description",
        "priority": "medium",
        "status": "open",
        "ai_tags": ["urgent", "network-issue", "hardware"],
    }
    
    enriched = await _enrich_ticket_context(ticket)
    
    # Verify that 'labels' field is present and maps to ai_tags
    assert "labels" in enriched
    assert enriched["labels"] == ["urgent", "network-issue", "hardware"]
    
    # Verify that 'ai_tags' is still accessible
    assert "ai_tags" in enriched
    assert enriched["ai_tags"] == ["urgent", "network-issue", "hardware"]


@pytest.mark.anyio
async def test_labels_empty_when_ai_tags_is_none(monkeypatch):
    """Test that ticket.labels is an empty list when ai_tags is None."""
    
    async def fake_get_company(company_id):
        return None
    
    async def fake_get_user(user_id):
        return None
    
    async def fake_list_watchers(ticket_id):
        return []
    
    async def fake_list_replies(ticket_id, include_internal=True):
        return []
    
    # Monkeypatch the repository calls
    monkeypatch.setattr(company_repo, "get_company_by_id", fake_get_company)
    monkeypatch.setattr(user_repo, "get_user_by_id", fake_get_user)
    monkeypatch.setattr(tickets_repo, "list_watchers", fake_list_watchers)
    monkeypatch.setattr(tickets_repo, "list_replies", fake_list_replies)
    
    # Create a ticket without ai_tags
    ticket = {
        "id": 321,
        "ticket_number": "TKT-321",
        "subject": "Test ticket without tags",
        "description": "Test description",
        "priority": "low",
        "status": "open",
        "ai_tags": None,
    }
    
    enriched = await _enrich_ticket_context(ticket)
    
    # Verify that 'labels' field is present and is an empty list
    assert "labels" in enriched
    assert enriched["labels"] == []
    
    # Verify that 'ai_tags' is still accessible
    assert "ai_tags" in enriched
    assert enriched["ai_tags"] is None


@pytest.mark.anyio
async def test_template_rendering_with_none_ticket_number():
    """Test that templates handle None ticket_number gracefully."""
    
    context = {
        "ticket": {
            "id": 999,
            "number": None,
            "ticket_number": None,
            "subject": "Ticket without number",
            "priority": "normal",
        }
    }
    
    # Test rendering with None values
    test_cases = [
        ("Ticket #{{ticket.number}}", "Ticket #"),
        ("Ticket #{{ticket.ticket_number}}", "Ticket #"),
        ("Subject: {{ticket.subject}}", "Subject: Ticket without number"),
    ]
    
    for template, expected in test_cases:
        result = await value_templates.render_string_async(template, context)
        assert result == expected, f"Failed for template: {template}. Got: {result}"
