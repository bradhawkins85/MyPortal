"""Test ticket creation with default status."""

import pytest

from app.repositories import ticket_statuses as ticket_status_repo
from app.services import tickets as tickets_service


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_resolve_status_or_default_uses_default_when_none(monkeypatch):
    """Test that resolve_status_or_default uses the default status when None is provided."""
    
    async def fake_get_default_status():
        return {
            "tech_status": "pending",
            "tech_label": "Pending",
            "public_status": "Pending",
            "is_default": True,
        }
    
    async def fake_status_exists(slug):
        return slug in ["open", "pending", "closed"]
    
    monkeypatch.setattr(ticket_status_repo, "get_default_status", fake_get_default_status)
    monkeypatch.setattr(ticket_status_repo, "status_exists", fake_status_exists)
    
    # When None is provided, should use default
    result = await tickets_service.resolve_status_or_default(None)
    assert result == "pending", f"Expected 'pending' but got '{result}'"


@pytest.mark.anyio
async def test_resolve_status_or_default_uses_default_when_empty_string(monkeypatch):
    """Test that resolve_status_or_default uses the default status when empty string is provided."""
    
    async def fake_get_default_status():
        return {
            "tech_status": "in_progress",
            "tech_label": "In Progress", 
            "public_status": "In Progress",
            "is_default": True,
        }
    
    async def fake_status_exists(slug):
        return slug in ["open", "in_progress", "closed"]
    
    monkeypatch.setattr(ticket_status_repo, "get_default_status", fake_get_default_status)
    monkeypatch.setattr(ticket_status_repo, "status_exists", fake_status_exists)
    
    # When empty string is provided, should use default
    result = await tickets_service.resolve_status_or_default("")
    assert result == "in_progress", f"Expected 'in_progress' but got '{result}'"


@pytest.mark.anyio
async def test_resolve_status_or_default_validates_provided_status(monkeypatch):
    """Test that resolve_status_or_default uses the provided status if valid."""
    
    async def fake_get_default_status():
        return {
            "tech_status": "pending",
            "tech_label": "Pending",
            "public_status": "Pending",
            "is_default": True,
        }
    
    async def fake_status_exists(slug):
        return slug in ["open", "pending", "closed"]
    
    monkeypatch.setattr(ticket_status_repo, "get_default_status", fake_get_default_status)
    monkeypatch.setattr(ticket_status_repo, "status_exists", fake_status_exists)
    
    # When a valid status is provided, should use it (not the default)
    result = await tickets_service.resolve_status_or_default("open")
    assert result == "open", f"Expected 'open' but got '{result}'"


@pytest.mark.anyio
async def test_resolve_status_or_default_uses_default_when_invalid_status(monkeypatch):
    """Test that resolve_status_or_default uses the default status when an invalid status is provided."""
    
    async def fake_get_default_status():
        return {
            "tech_status": "pending",
            "tech_label": "Pending",
            "public_status": "Pending",
            "is_default": True,
        }
    
    async def fake_status_exists(slug):
        return slug in ["open", "pending", "closed"]
    
    monkeypatch.setattr(ticket_status_repo, "get_default_status", fake_get_default_status)
    monkeypatch.setattr(ticket_status_repo, "status_exists", fake_status_exists)
    
    # When an invalid status is provided, should fall back to default
    result = await tickets_service.resolve_status_or_default("nonexistent")
    assert result == "pending", f"Expected 'pending' but got '{result}'"


@pytest.mark.anyio
async def test_create_ticket_uses_default_status_when_none(monkeypatch):
    """Test that create_ticket uses the default status when None is provided."""
    
    captured_status = None
    
    async def fake_create_ticket(**kwargs):
        nonlocal captured_status
        captured_status = kwargs.get("status")
        return {"id": 123, **kwargs}
    
    async def fake_get_default_status():
        return {
            "tech_status": "new",
            "tech_label": "New",
            "public_status": "New",
            "is_default": True,
        }
    
    async def fake_status_exists(slug):
        return slug in ["open", "new", "closed"]
    
    async def fake_handle_event(event_name, context):
        return []
    
    async def fake_get_company(company_id):
        return None
    
    async def fake_get_user(user_id):
        return None
    
    monkeypatch.setattr(tickets_service.tickets_repo, "create_ticket", fake_create_ticket)
    monkeypatch.setattr(ticket_status_repo, "get_default_status", fake_get_default_status)
    monkeypatch.setattr(ticket_status_repo, "status_exists", fake_status_exists)
    monkeypatch.setattr(tickets_service.automations_service, "handle_event", fake_handle_event)
    monkeypatch.setattr(tickets_service.company_repo, "get_company_by_id", fake_get_company)
    monkeypatch.setattr(tickets_service.user_repo, "get_user_by_id", fake_get_user)
    
    # Create ticket with no status (None)
    ticket = await tickets_service.create_ticket(
        subject="Test ticket",
        description="Test description",
        requester_id=None,
        company_id=None,
        assigned_user_id=None,
        priority="normal",
        status=None,  # No status provided
        category=None,
        module_slug=None,
        external_reference=None,
    )
    
    assert captured_status == "new", f"Expected status 'new' but got '{captured_status}'"
