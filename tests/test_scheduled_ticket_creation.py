"""Test scheduled ticket creation command."""
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch


@pytest.mark.asyncio
async def test_create_scheduled_ticket_handler():
    """Test that create_scheduled_ticket command processes JSON payload correctly."""
    
    # Mock the task data
    task = {
        "id": 1,
        "command": "create_scheduled_ticket",
        "company_id": 10,
        "description": json.dumps({
            "subject": "Test Ticket",
            "description": "Test description",
            "priority": "high",
            "status": "open"
        })
    }
    
    # Mock the tickets_service.create_ticket function
    mock_ticket = {
        "id": 100,
        "number": "T-1234",
        "subject": "Test Ticket"
    }
    
    with patch('app.services.scheduler.tickets_service.create_ticket', new_callable=AsyncMock) as mock_create:
        mock_create.return_value = mock_ticket
        
        # Import the scheduler service (after patching)
        from app.services.scheduler import SchedulerService
        
        scheduler = SchedulerService()
        
        # Mock the database operations
        with patch('app.services.scheduler.scheduled_tasks_repo.record_task_run', new_callable=AsyncMock):
            with patch('app.services.scheduler.db.acquire_lock') as mock_lock:
                # Configure the lock to return True (acquired)
                mock_lock.return_value.__aenter__.return_value = True
                
                # Run the task
                await scheduler._run_task(task)
                
                # Verify create_ticket was called with correct arguments
                mock_create.assert_called_once()
                call_kwargs = mock_create.call_args.kwargs
                
                assert call_kwargs['subject'] == "Test Ticket"
                assert call_kwargs['description'] == "Test description"
                assert call_kwargs['priority'] == "high"
                assert call_kwargs['status'] == "open"
                assert call_kwargs['company_id'] == 10
                assert call_kwargs['trigger_automations'] is False


@pytest.mark.asyncio
async def test_create_scheduled_ticket_invalid_json():
    """Test that invalid JSON in description is handled gracefully."""
    
    task = {
        "id": 2,
        "command": "create_scheduled_ticket",
        "description": "not valid json"
    }
    
    from app.services.scheduler import SchedulerService
    
    scheduler = SchedulerService()
    
    # Mock the database operations
    with patch('app.services.scheduler.scheduled_tasks_repo.record_task_run', new_callable=AsyncMock) as mock_record:
        with patch('app.services.scheduler.db.acquire_lock') as mock_lock:
            mock_lock.return_value.__aenter__.return_value = True
            
            # Run the task
            await scheduler._run_task(task)
            
            # Verify that the run was recorded with failed status
            mock_record.assert_called_once()
            call_args = mock_record.call_args
            assert call_args.kwargs['status'] == 'failed'
            assert 'Invalid JSON' in call_args.kwargs['details']


@pytest.mark.asyncio
async def test_create_scheduled_ticket_missing_subject():
    """Test that missing subject field is handled gracefully."""
    
    task = {
        "id": 3,
        "command": "create_scheduled_ticket",
        "description": json.dumps({
            "description": "Test description without subject"
        })
    }
    
    from app.services.scheduler import SchedulerService
    
    scheduler = SchedulerService()
    
    # Mock the database operations
    with patch('app.services.scheduler.scheduled_tasks_repo.record_task_run', new_callable=AsyncMock) as mock_record:
        with patch('app.services.scheduler.db.acquire_lock') as mock_lock:
            mock_lock.return_value.__aenter__.return_value = True
            
            # Run the task
            await scheduler._run_task(task)
            
            # Verify that the run was recorded with failed status
            mock_record.assert_called_once()
            call_args = mock_record.call_args
            assert call_args.kwargs['status'] == 'failed'
            assert 'Missing required field: subject' in call_args.kwargs['details']
