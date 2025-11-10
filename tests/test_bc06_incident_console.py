"""
Test BC06: Incident Console functionality

These tests verify that the incident console works correctly with:
- Starting and closing incidents
- Managing checklist items and ticks
- Managing contacts
- Creating event log entries
- Webhook integration for auto-starting incidents
"""
from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, patch

import pytest

from app.repositories import bcp as bcp_repo


@pytest.mark.anyio
async def test_seed_default_checklist_items():
    """Test that default checklist items are seeded correctly."""
    plan_id = 1
    
    # Mock the database operations
    with patch("app.repositories.bcp.create_checklist_item") as mock_create:
        mock_create.return_value = {"id": 1, "plan_id": plan_id, "phase": "Immediate", "label": "Test", "default_order": 0}
        
        await bcp_repo.seed_default_checklist_items(plan_id)
        
        # Verify that 18 items were created
        assert mock_create.call_count == 18
        
        # Verify that all items are for "Immediate" phase
        for call in mock_create.call_args_list:
            args, kwargs = call
            assert args[1] == "Immediate"  # phase parameter


@pytest.mark.anyio
async def test_create_incident():
    """Test creating a new incident."""
    plan_id = 1
    now = datetime.utcnow()
    
    mock_incident = {
        "id": 1,
        "plan_id": plan_id,
        "started_at": now,
        "status": "Active",
        "source": "Manual",
    }
    
    with patch("app.core.database.db.connection") as mock_conn:
        mock_cursor = AsyncMock()
        mock_cursor.lastrowid = 1
        mock_cursor.__aenter__ = AsyncMock(return_value=mock_cursor)
        mock_cursor.__aexit__ = AsyncMock()
        
        mock_connection = AsyncMock()
        mock_connection.cursor.return_value = mock_cursor
        mock_connection.__aenter__ = AsyncMock(return_value=mock_connection)
        mock_connection.__aexit__ = AsyncMock()
        
        mock_conn.return_value = mock_connection
        
        with patch("app.repositories.bcp.get_incident_by_id", return_value=mock_incident):
            result = await bcp_repo.create_incident(plan_id, now, source="Manual")
            
            assert result["id"] == 1
            assert result["plan_id"] == plan_id
            assert result["status"] == "Active"
            assert result["source"] == "Manual"


@pytest.mark.anyio
async def test_initialize_checklist_ticks():
    """Test initializing checklist ticks for a new incident."""
    plan_id = 1
    incident_id = 1
    
    mock_items = [
        {"id": 1, "plan_id": plan_id, "phase": "Immediate", "label": "Item 1", "default_order": 0},
        {"id": 2, "plan_id": plan_id, "phase": "Immediate", "label": "Item 2", "default_order": 1},
    ]
    
    with patch("app.repositories.bcp.list_checklist_items", return_value=mock_items):
        with patch("app.core.database.db.connection") as mock_conn:
            mock_cursor = AsyncMock()
            mock_cursor.__aenter__ = AsyncMock(return_value=mock_cursor)
            mock_cursor.__aexit__ = AsyncMock()
            
            mock_connection = AsyncMock()
            mock_connection.cursor.return_value = mock_cursor
            mock_connection.__aenter__ = AsyncMock(return_value=mock_connection)
            mock_connection.__aexit__ = AsyncMock()
            
            mock_conn.return_value = mock_connection
            
            await bcp_repo.initialize_checklist_ticks(plan_id, incident_id)
            
            # Verify that execute was called for each item
            assert mock_cursor.execute.call_count == len(mock_items)


@pytest.mark.anyio
async def test_toggle_checklist_tick():
    """Test toggling a checklist tick."""
    tick_id = 1
    user_id = 1
    now = datetime.utcnow()
    
    mock_tick = {
        "id": tick_id,
        "is_done": True,
        "done_at": now,
        "done_by": user_id,
    }
    
    with patch("app.core.database.db.connection") as mock_conn:
        mock_cursor = AsyncMock()
        mock_cursor.__aenter__ = AsyncMock(return_value=mock_cursor)
        mock_cursor.__aexit__ = AsyncMock()
        
        mock_connection = AsyncMock()
        mock_connection.cursor.return_value = mock_cursor
        mock_connection.__aenter__ = AsyncMock(return_value=mock_connection)
        mock_connection.__aexit__ = AsyncMock()
        
        mock_conn.return_value = mock_connection
        
        with patch("app.repositories.bcp.get_checklist_tick_by_id", return_value=mock_tick):
            result = await bcp_repo.toggle_checklist_tick(tick_id, True, user_id, now)
            
            assert result["is_done"] is True
            assert result["done_by"] == user_id


@pytest.mark.anyio
async def test_create_contact():
    """Test creating a new contact."""
    plan_id = 1
    
    mock_contact = {
        "id": 1,
        "plan_id": plan_id,
        "kind": "Internal",
        "person_or_org": "John Doe",
        "phones": "+1-555-0100",
        "email": "john@example.com",
        "responsibility_or_agency": "Emergency Coordinator",
    }
    
    with patch("app.core.database.db.connection") as mock_conn:
        mock_cursor = AsyncMock()
        mock_cursor.lastrowid = 1
        mock_cursor.__aenter__ = AsyncMock(return_value=mock_cursor)
        mock_cursor.__aexit__ = AsyncMock()
        
        mock_connection = AsyncMock()
        mock_connection.cursor.return_value = mock_cursor
        mock_connection.__aenter__ = AsyncMock(return_value=mock_connection)
        mock_connection.__aexit__ = AsyncMock()
        
        mock_conn.return_value = mock_connection
        
        with patch("app.repositories.bcp.get_contact_by_id", return_value=mock_contact):
            result = await bcp_repo.create_contact(
                plan_id,
                "Internal",
                "John Doe",
                phones="+1-555-0100",
                email="john@example.com",
                responsibility_or_agency="Emergency Coordinator"
            )
            
            assert result["id"] == 1
            assert result["kind"] == "Internal"
            assert result["person_or_org"] == "John Doe"


@pytest.mark.anyio
async def test_create_event_log_entry():
    """Test creating an event log entry."""
    plan_id = 1
    incident_id = 1
    now = datetime.utcnow()
    
    mock_entry = {
        "id": 1,
        "plan_id": plan_id,
        "incident_id": incident_id,
        "happened_at": now,
        "author_id": 1,
        "notes": "Test event",
        "initials": "JD",
    }
    
    with patch("app.core.database.db.connection") as mock_conn:
        mock_cursor = AsyncMock()
        mock_cursor.lastrowid = 1
        mock_cursor.__aenter__ = AsyncMock(return_value=mock_cursor)
        mock_cursor.__aexit__ = AsyncMock()
        
        mock_connection = AsyncMock()
        mock_connection.cursor.return_value = mock_cursor
        mock_connection.__aenter__ = AsyncMock(return_value=mock_connection)
        mock_connection.__aexit__ = AsyncMock()
        
        mock_conn.return_value = mock_connection
        
        with patch("app.repositories.bcp.get_event_log_entry_by_id", return_value=mock_entry):
            result = await bcp_repo.create_event_log_entry(
                plan_id,
                incident_id,
                now,
                "Test event",
                author_id=1,
                initials="JD"
            )
            
            assert result["id"] == 1
            assert result["notes"] == "Test event"
            assert result["initials"] == "JD"


@pytest.mark.anyio
async def test_close_incident():
    """Test closing an incident."""
    incident_id = 1
    
    mock_incident = {
        "id": incident_id,
        "status": "Closed",
    }
    
    with patch("app.core.database.db.connection") as mock_conn:
        mock_cursor = AsyncMock()
        mock_cursor.__aenter__ = AsyncMock(return_value=mock_cursor)
        mock_cursor.__aexit__ = AsyncMock()
        
        mock_connection = AsyncMock()
        mock_connection.cursor.return_value = mock_cursor
        mock_connection.__aenter__ = AsyncMock(return_value=mock_connection)
        mock_connection.__aexit__ = AsyncMock()
        
        mock_conn.return_value = mock_connection
        
        with patch("app.repositories.bcp.get_incident_by_id", return_value=mock_incident):
            result = await bcp_repo.close_incident(incident_id)
            
            assert result["status"] == "Closed"


@pytest.mark.anyio
async def test_webhook_start_incident_success():
    """Test webhook auto-starting an incident."""
    from app.api.routes.bcp import webhook_start_incident
    from fastapi import Request
    import json
    
    payload = {
        "company_id": 1,
        "source": "UptimeKuma",
        "message": "Service down alert",
        "api_key": "test-key"
    }
    
    mock_plan = {"id": 1, "company_id": 1}
    mock_incident = {
        "id": 1,
        "plan_id": 1,
        "started_at": datetime.utcnow(),
        "status": "Active"
    }
    
    # Create mock request
    mock_request = AsyncMock(spec=Request)
    mock_request.body = AsyncMock(return_value=json.dumps(payload).encode("utf-8"))
    
    with patch("app.repositories.bcp.get_plan_by_company", return_value=mock_plan):
        with patch("app.repositories.bcp.get_active_incident", return_value=None):
            with patch("app.repositories.bcp.create_incident", return_value=mock_incident):
                with patch("app.repositories.bcp.initialize_checklist_ticks"):
                    with patch("app.repositories.bcp.create_event_log_entry"):
                        result = await webhook_start_incident(mock_request)
                        
                        assert result["status"] == "started"
                        assert result["incident_id"] == 1
                        assert "message" in result


@pytest.mark.anyio
async def test_webhook_start_incident_already_active():
    """Test webhook when incident is already active."""
    from app.api.routes.bcp import webhook_start_incident
    from fastapi import Request
    import json
    
    payload = {
        "company_id": 1,
        "source": "UptimeKuma",
        "message": "Service down alert",
        "api_key": "test-key"
    }
    
    mock_plan = {"id": 1, "company_id": 1}
    mock_active_incident = {
        "id": 1,
        "plan_id": 1,
        "status": "Active"
    }
    
    # Create mock request
    mock_request = AsyncMock(spec=Request)
    mock_request.body = AsyncMock(return_value=json.dumps(payload).encode("utf-8"))
    
    with patch("app.repositories.bcp.get_plan_by_company", return_value=mock_plan):
        with patch("app.repositories.bcp.get_active_incident", return_value=mock_active_incident):
            result = await webhook_start_incident(mock_request)
            
            assert result["status"] == "already_active"
            assert result["incident_id"] == 1


@pytest.mark.anyio
async def test_list_contacts_by_kind():
    """Test listing contacts filtered by kind."""
    plan_id = 1
    
    mock_contacts = [
        {"id": 1, "kind": "Internal", "person_or_org": "John Doe"},
        {"id": 2, "kind": "Internal", "person_or_org": "Jane Smith"},
    ]
    
    with patch("app.core.database.db.connection") as mock_conn:
        mock_cursor = AsyncMock()
        mock_cursor.fetchall = AsyncMock(return_value=[
            (1, plan_id, "Internal", "John Doe", None, None, None, None, None),
            (2, plan_id, "Internal", "Jane Smith", None, None, None, None, None),
        ])
        mock_cursor.__aenter__ = AsyncMock(return_value=mock_cursor)
        mock_cursor.__aexit__ = AsyncMock()
        
        mock_connection = AsyncMock()
        mock_connection.cursor.return_value = mock_cursor
        mock_connection.__aenter__ = AsyncMock(return_value=mock_connection)
        mock_connection.__aexit__ = AsyncMock()
        
        mock_conn.return_value = mock_connection
        
        result = await bcp_repo.list_contacts(plan_id, kind="Internal")
        
        assert len(result) == 2
        assert all(contact["kind"] == "Internal" for contact in result)
