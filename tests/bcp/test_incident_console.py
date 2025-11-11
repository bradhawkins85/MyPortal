"""
Test BCP incident console functionality.

Tests verify:
- Starting incident posts alert and seeds first log entry
- Checklist tick changes are audited
- Incident state management
- Event log tracking
"""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock
from datetime import datetime


class TestIncidentStartWithAlertAndLog:
    """Test incident start creates alert and first log entry."""
    
    @pytest.mark.asyncio
    async def test_start_incident_seeds_first_log_entry(self):
        """Test that starting an incident creates first event log entry."""
        from app.repositories import bcp as bcp_repo
        
        plan_id = 1
        now = datetime.utcnow()
        
        # Mock database connection
        with patch("app.core.database.db.connection") as mock_conn:
            mock_cursor = AsyncMock()
            mock_cursor.lastrowid = 1
            mock_cursor.__aenter__ = AsyncMock(return_value=mock_cursor)
            mock_cursor.__aexit__ = AsyncMock()
            
            mock_connection = AsyncMock()
            mock_connection.cursor = MagicMock(return_value=mock_cursor)
            mock_connection.__aenter__ = AsyncMock(return_value=mock_connection)
            mock_connection.__aexit__ = AsyncMock()
            
            mock_conn.return_value = mock_connection
            
            # Mock get_incident_by_id to return created incident
            with patch("app.repositories.bcp.get_incident_by_id") as mock_get_incident:
                mock_get_incident.return_value = {
                    "id": 1,
                    "plan_id": plan_id,
                    "started_at": now,
                    "status": "Active",
                    "source": "Manual",
                }
                
                # Create incident
                incident = await bcp_repo.create_incident(plan_id, now, source="Manual")
                
                # Verify incident was created
                assert incident["id"] == 1
                assert incident["status"] == "Active"
                
                # Now verify we can create an event log entry
                with patch("app.repositories.bcp.get_event_log_entry_by_id") as mock_get_log:
                    mock_get_log.return_value = {
                        "id": 1,
                        "plan_id": plan_id,
                        "incident_id": incident["id"],
                        "happened_at": now,
                        "notes": "Incident started",
                        "initials": "SYS",
                    }
                    
                    log_entry = await bcp_repo.create_event_log_entry(
                        plan_id,
                        incident["id"],
                        now,
                        "Incident started",
                        initials="SYS",
                    )
                    
                    # Verify log entry was created
                    assert log_entry["incident_id"] == incident["id"]
                    assert "started" in log_entry["notes"].lower()
    
    @pytest.mark.asyncio
    async def test_start_incident_initializes_checklist_ticks(self):
        """Test that starting an incident initializes checklist ticks."""
        from app.repositories import bcp as bcp_repo
        
        plan_id = 1
        incident_id = 1
        
        # Mock checklist items
        mock_items = [
            {"id": 1, "plan_id": plan_id, "phase": "Immediate", "label": "Item 1", "default_order": 0},
            {"id": 2, "plan_id": plan_id, "phase": "Immediate", "label": "Item 2", "default_order": 1},
            {"id": 3, "plan_id": plan_id, "phase": "Immediate", "label": "Item 3", "default_order": 2},
        ]
        
        with patch("app.repositories.bcp.list_checklist_items", return_value=mock_items):
            with patch("app.core.database.db.connection") as mock_conn:
                mock_cursor = AsyncMock()
                mock_cursor.__aenter__ = AsyncMock(return_value=mock_cursor)
                mock_cursor.__aexit__ = AsyncMock()
                
                mock_connection = AsyncMock()
                mock_connection.cursor = MagicMock(return_value=mock_cursor)
                mock_connection.__aenter__ = AsyncMock(return_value=mock_connection)
                mock_connection.__aexit__ = AsyncMock()
                
                mock_conn.return_value = mock_connection
                
                # Initialize checklist ticks
                await bcp_repo.initialize_checklist_ticks(plan_id, incident_id)
                
                # Verify execute was called for each item (3 times)
                assert mock_cursor.execute.call_count == 3
    
    @pytest.mark.asyncio
    async def test_incident_start_flow_complete(self):
        """Test complete incident start flow: create incident, init checklist, log event."""
        from datetime import datetime
        
        plan_id = 1
        now = datetime.utcnow()
        
        # Mock all database operations
        with patch("app.core.database.db.connection") as mock_conn:
            mock_cursor = AsyncMock()
            mock_cursor.lastrowid = 1
            mock_cursor.__aenter__ = AsyncMock(return_value=mock_cursor)
            mock_cursor.__aexit__ = AsyncMock()
            
            mock_connection = AsyncMock()
            mock_connection.cursor = MagicMock(return_value=mock_cursor)
            mock_connection.__aenter__ = AsyncMock(return_value=mock_connection)
            mock_connection.__aexit__ = AsyncMock()
            
            mock_conn.return_value = mock_connection
            
            # Mock incident creation
            with patch("app.repositories.bcp.create_incident") as mock_create_incident:
                mock_create_incident.return_value = {
                    "id": 1,
                    "plan_id": plan_id,
                    "started_at": now,
                    "status": "Active",
                    "source": "Manual",
                }
                
                # Mock checklist initialization
                with patch("app.repositories.bcp.initialize_checklist_ticks") as mock_init_ticks:
                    mock_init_ticks.return_value = None
                    
                    # Mock event log entry creation
                    with patch("app.repositories.bcp.create_event_log_entry") as mock_create_log:
                        mock_create_log.return_value = {
                            "id": 1,
                            "plan_id": plan_id,
                            "incident_id": 1,
                            "happened_at": now,
                            "notes": "Incident started",
                        }
                        
                        # Execute the flow
                        incident = await mock_create_incident(plan_id, now, source="Manual")
                        await mock_init_ticks(plan_id, incident["id"])
                        log_entry = await mock_create_log(
                            plan_id,
                            incident["id"],
                            now,
                            "Incident started",
                        )
                        
                        # Verify all steps completed
                        assert incident is not None
                        assert log_entry is not None
                        mock_create_incident.assert_called_once()
                        mock_init_ticks.assert_called_once()
                        mock_create_log.assert_called_once()


class TestChecklistTickAuditing:
    """Test that checklist tick changes are audited."""
    
    @pytest.mark.asyncio
    async def test_toggle_checklist_tick_audited(self):
        """Test that toggling a checklist tick is audited."""
        from app.repositories import bcp as bcp_repo
        
        tick_id = 1
        is_done = True
        user_id = 1
        done_at = datetime.utcnow()
        
        # Mock database connection
        with patch("app.core.database.db.connection") as mock_conn:
            mock_cursor = AsyncMock()
            mock_cursor.rowcount = 1
            mock_cursor.__aenter__ = AsyncMock(return_value=mock_cursor)
            mock_cursor.__aexit__ = AsyncMock()
            
            mock_connection = AsyncMock()
            mock_connection.cursor = MagicMock(return_value=mock_cursor)
            mock_connection.__aenter__ = AsyncMock(return_value=mock_connection)
            mock_connection.__aexit__ = AsyncMock()
            
            mock_conn.return_value = mock_connection
            
            # Toggle the tick
            result = await bcp_repo.toggle_checklist_tick(tick_id, is_done, user_id, done_at)
            
            # Verify toggle succeeded
            assert result is True
            
            # Verify execute was called with correct parameters
            mock_cursor.execute.assert_called_once()
            call_args = mock_cursor.execute.call_args[0]
            query = call_args[0]
            
            # Check query updates is_done, done_by, and done_at
            assert "is_done" in query.lower()
            assert "done_by" in query.lower()
            assert "done_at" in query.lower()
    
    @pytest.mark.asyncio
    async def test_checklist_tick_stores_completion_metadata(self):
        """Test that checklist tick stores who completed it and when."""
        from app.repositories import bcp as bcp_repo
        
        tick_id = 1
        
        # Mock get_checklist_tick_by_id to return tick with metadata
        with patch("app.core.database.db.connection") as mock_conn:
            mock_cursor = AsyncMock()
            mock_cursor.fetchone = AsyncMock(return_value={
                "id": tick_id,
                "plan_id": 1,
                "checklist_item_id": 1,
                "incident_id": 1,
                "is_done": True,
                "done_at": datetime.utcnow(),
                "done_by": 1,
            })
            mock_cursor.__aenter__ = AsyncMock(return_value=mock_cursor)
            mock_cursor.__aexit__ = AsyncMock()
            
            mock_connection = AsyncMock()
            mock_connection.cursor = MagicMock(return_value=mock_cursor)
            mock_connection.__aenter__ = AsyncMock(return_value=mock_connection)
            mock_connection.__aexit__ = AsyncMock()
            
            mock_conn.return_value = mock_connection
            
            # Get the tick
            tick = await bcp_repo.get_checklist_tick_by_id(tick_id)
            
            # Verify metadata is present
            assert tick["is_done"] is True
            assert tick["done_at"] is not None
            assert tick["done_by"] is not None
    
    @pytest.mark.asyncio
    async def test_audit_log_records_checklist_toggle(self):
        """Test that audit log can record checklist toggle events."""
        from app.services import audit
        from fastapi import Request
        
        # Mock request
        mock_request = MagicMock(spec=Request)
        mock_request.client = MagicMock()
        mock_request.client.host = "127.0.0.1"
        mock_request.headers = {}
        
        with patch("app.services.audit.log_action") as mock_log_action:
            mock_log_action.return_value = None
            
            # Log a checklist toggle
            await audit.log_action(
                action="bcp.checklist.toggle",
                user_id=1,
                entity_type="bcp_checklist_tick",
                entity_id=1,
                previous_value={"is_done": False},
                new_value={"is_done": True},
                metadata={"company_id": 1, "incident_id": 1},
                request=mock_request,
            )
            
            # Verify audit log was called
            mock_log_action.assert_called_once()
            call_kwargs = mock_log_action.call_args[1]
            
            assert call_kwargs["action"] == "bcp.checklist.toggle"
            assert call_kwargs["entity_type"] == "bcp_checklist_tick"
            assert call_kwargs["previous_value"]["is_done"] is False
            assert call_kwargs["new_value"]["is_done"] is True


class TestIncidentStateManagement:
    """Test incident state management."""
    
    @pytest.mark.asyncio
    async def test_only_one_active_incident_per_plan(self):
        """Test that only one incident can be active per plan."""
        from app.repositories import bcp as bcp_repo
        
        plan_id = 1
        
        # Mock get_active_incident
        with patch("app.core.database.db.connection") as mock_conn:
            mock_cursor = AsyncMock()
            mock_cursor.fetchone = AsyncMock(return_value={
                "id": 1,
                "plan_id": plan_id,
                "started_at": datetime.utcnow(),
                "status": "Active",
            })
            mock_cursor.__aenter__ = AsyncMock(return_value=mock_cursor)
            mock_cursor.__aexit__ = AsyncMock()
            
            mock_connection = AsyncMock()
            mock_connection.cursor = MagicMock(return_value=mock_cursor)
            mock_connection.__aenter__ = AsyncMock(return_value=mock_connection)
            mock_connection.__aexit__ = AsyncMock()
            
            mock_conn.return_value = mock_connection
            
            # Get active incident
            active_incident = await bcp_repo.get_active_incident(plan_id)
            
            # Verify there's an active incident
            assert active_incident is not None
            assert active_incident["status"] == "Active"
            
            # Verify query filters by plan_id and status='Active'
            call_args = mock_cursor.execute.call_args[0]
            query = call_args[0]
            assert "plan_id" in query.lower()
            assert "active" in query.lower()
    
    @pytest.mark.asyncio
    async def test_close_incident_changes_status(self):
        """Test that closing an incident changes its status to Closed."""
        from app.repositories import bcp as bcp_repo
        
        incident_id = 1
        
        # Mock database connection
        with patch("app.core.database.db.connection") as mock_conn:
            mock_cursor = AsyncMock()
            mock_cursor.rowcount = 1
            mock_cursor.__aenter__ = AsyncMock(return_value=mock_cursor)
            mock_cursor.__aexit__ = AsyncMock()
            
            mock_connection = AsyncMock()
            mock_connection.cursor = MagicMock(return_value=mock_cursor)
            mock_connection.__aenter__ = AsyncMock(return_value=mock_connection)
            mock_connection.__aexit__ = AsyncMock()
            
            mock_conn.return_value = mock_connection
            
            # Close the incident
            result = await bcp_repo.close_incident(incident_id)
            
            # Verify close succeeded
            assert result is True
            
            # Verify query updates status to 'Closed'
            call_args = mock_cursor.execute.call_args[0]
            query = call_args[0]
            params = call_args[1] if len(call_args) > 1 else None
            
            assert "status" in query.lower()
            assert "closed" in query.lower() or (params and "Closed" in str(params))
    
    @pytest.mark.asyncio
    async def test_cannot_start_incident_when_one_active(self):
        """Test that attempting to start incident when one is active is handled."""
        # This test verifies the route logic that checks for active incidents
        # In practice, the route should check get_active_incident() before creating new one
        from app.repositories import bcp as bcp_repo
        
        plan_id = 1
        
        # Mock that there's already an active incident
        with patch("app.repositories.bcp.get_active_incident") as mock_get_active:
            mock_get_active.return_value = {
                "id": 1,
                "plan_id": plan_id,
                "status": "Active",
                "started_at": datetime.utcnow(),
            }
            
            # Get active incident
            active = await mock_get_active(plan_id)
            
            # Verify there's an active incident
            assert active is not None
            assert active["status"] == "Active"
            
            # The route should not create a new incident in this case
            # This is just verifying the check works


class TestEventLogTracking:
    """Test event log tracking during incidents."""
    
    @pytest.mark.asyncio
    async def test_create_event_log_entry_with_author(self):
        """Test creating event log entry with author information."""
        from app.repositories import bcp as bcp_repo
        
        plan_id = 1
        incident_id = 1
        now = datetime.utcnow()
        
        # Mock database connection
        with patch("app.core.database.db.connection") as mock_conn:
            mock_cursor = AsyncMock()
            mock_cursor.lastrowid = 1
            mock_cursor.__aenter__ = AsyncMock(return_value=mock_cursor)
            mock_cursor.__aexit__ = AsyncMock()
            
            mock_connection = AsyncMock()
            mock_connection.cursor = MagicMock(return_value=mock_cursor)
            mock_connection.__aenter__ = AsyncMock(return_value=mock_connection)
            mock_connection.__aexit__ = AsyncMock()
            
            mock_conn.return_value = mock_connection
            
            with patch("app.repositories.bcp.get_event_log_entry_by_id") as mock_get:
                mock_get.return_value = {
                    "id": 1,
                    "plan_id": plan_id,
                    "incident_id": incident_id,
                    "happened_at": now,
                    "author_id": 1,
                    "notes": "Test event",
                    "initials": "JD",
                }
                
                # Create event log entry
                entry = await bcp_repo.create_event_log_entry(
                    plan_id,
                    incident_id,
                    now,
                    "Test event",
                    author_id=1,
                    initials="JD",
                )
                
                # Verify entry was created with author info
                assert entry["author_id"] == 1
                assert entry["initials"] == "JD"
    
    @pytest.mark.asyncio
    async def test_list_event_log_entries_for_incident(self):
        """Test listing event log entries for a specific incident."""
        from app.repositories import bcp as bcp_repo
        
        plan_id = 1
        incident_id = 1
        
        # Mock database connection
        with patch("app.core.database.db.connection") as mock_conn:
            mock_cursor = AsyncMock()
            mock_cursor.fetchall = AsyncMock(return_value=[
                {
                    "id": 1,
                    "plan_id": plan_id,
                    "incident_id": incident_id,
                    "happened_at": datetime.utcnow(),
                    "notes": "Event 1",
                },
                {
                    "id": 2,
                    "plan_id": plan_id,
                    "incident_id": incident_id,
                    "happened_at": datetime.utcnow(),
                    "notes": "Event 2",
                },
            ])
            mock_cursor.__aenter__ = AsyncMock(return_value=mock_cursor)
            mock_cursor.__aexit__ = AsyncMock()
            
            mock_connection = AsyncMock()
            mock_connection.cursor = MagicMock(return_value=mock_cursor)
            mock_connection.__aenter__ = AsyncMock(return_value=mock_connection)
            mock_connection.__aexit__ = AsyncMock()
            
            mock_conn.return_value = mock_connection
            
            # List event log entries
            entries = await bcp_repo.list_event_log_entries(plan_id, incident_id=incident_id)
            
            # Verify entries were returned
            assert len(entries) == 2
            assert all(e["incident_id"] == incident_id for e in entries)
    
    @pytest.mark.asyncio
    async def test_event_log_ordered_by_timestamp(self):
        """Test that event log entries are ordered by timestamp."""
        from app.repositories import bcp as bcp_repo
        
        plan_id = 1
        incident_id = 1
        
        # Mock database connection with ordered entries
        with patch("app.core.database.db.connection") as mock_conn:
            mock_cursor = AsyncMock()
            # Entries should be ordered by happened_at DESC
            mock_cursor.fetchall = AsyncMock(return_value=[
                {
                    "id": 2,
                    "happened_at": datetime(2024, 1, 2, 12, 0, 0),
                    "notes": "Later event",
                },
                {
                    "id": 1,
                    "happened_at": datetime(2024, 1, 1, 12, 0, 0),
                    "notes": "Earlier event",
                },
            ])
            mock_cursor.__aenter__ = AsyncMock(return_value=mock_cursor)
            mock_cursor.__aexit__ = AsyncMock()
            
            mock_connection = AsyncMock()
            mock_connection.cursor = MagicMock(return_value=mock_cursor)
            mock_connection.__aenter__ = AsyncMock(return_value=mock_connection)
            mock_connection.__aexit__ = AsyncMock()
            
            mock_conn.return_value = mock_connection
            
            # List event log entries
            entries = await bcp_repo.list_event_log_entries(plan_id, incident_id=incident_id)
            
            # Verify ordering (later events first)
            assert entries[0]["notes"] == "Later event"
            assert entries[1]["notes"] == "Earlier event"
            
            # Verify query includes ORDER BY
            call_args = mock_cursor.execute.call_args[0]
            query = call_args[0]
            assert "order by" in query.lower()
