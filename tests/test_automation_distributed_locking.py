"""Test automation distributed locking to prevent duplicate execution."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime, timezone


@pytest.mark.asyncio
async def test_automation_execution_uses_distributed_lock():
    """Test that _execute_automation uses distributed lock to prevent concurrent execution."""
    
    automation = {
        "id": 123,
        "name": "Test Automation",
        "kind": "scheduled",
        "action_module": "test_module",
        "action_payload": {"test": "data"},
    }
    
    with patch('app.services.automations.db.acquire_lock') as mock_lock:
        # Configure the lock to return True (acquired)
        mock_lock.return_value.__aenter__.return_value = True
        
        with patch('app.services.automations.automation_repo.mark_started', new_callable=AsyncMock):
            with patch('app.services.automations.automation_repo.record_run', new_callable=AsyncMock):
                with patch('app.services.automations.automation_repo.set_last_error', new_callable=AsyncMock):
                    with patch('app.services.automations.automation_repo.set_next_run', new_callable=AsyncMock):
                        with patch('app.services.automations.modules_service.trigger_module', new_callable=AsyncMock) as mock_trigger:
                            mock_trigger.return_value = {"status": "succeeded"}
                            
                            from app.services.automations import _execute_automation
                            
                            result = await _execute_automation(automation)
                            
                            # Verify lock was acquired with correct name
                            mock_lock.assert_called_once_with("automation_exec_123", timeout=1)
                            
                            # Verify execution completed successfully
                            assert result["status"] == "succeeded"


@pytest.mark.asyncio
async def test_automation_execution_skips_when_lock_not_acquired():
    """Test that automation execution is skipped when another worker already has the lock."""
    
    automation = {
        "id": 456,
        "name": "Test Automation",
        "kind": "scheduled",
        "action_module": "test_module",
        "action_payload": {"test": "data"},
    }
    
    with patch('app.services.automations.db.acquire_lock') as mock_lock:
        # Configure the lock to return False (not acquired - another worker has it)
        mock_lock.return_value.__aenter__.return_value = False
        
        with patch('app.services.automations.automation_repo.mark_started', new_callable=AsyncMock) as mock_mark:
            with patch('app.services.automations.modules_service.trigger_module', new_callable=AsyncMock) as mock_trigger:
                
                from app.services.automations import _execute_automation
                
                result = await _execute_automation(automation)
                
                # Verify lock was attempted with correct name
                mock_lock.assert_called_once_with("automation_exec_456", timeout=1)
                
                # Verify execution was skipped
                assert result["status"] == "skipped"
                assert result["reason"] == "Already running on another worker"
                assert result["automation_id"] == 456
                
                # Verify mark_started was NOT called (because we skipped)
                mock_mark.assert_not_called()
                
                # Verify trigger_module was NOT called (because we skipped)
                mock_trigger.assert_not_called()


@pytest.mark.asyncio
async def test_process_due_automations_each_gets_lock():
    """Test that process_due_automations processes multiple automations with individual locks."""
    
    automations = [
        {"id": 1, "name": "Auto 1", "action_module": "mod1", "action_payload": {}},
        {"id": 2, "name": "Auto 2", "action_module": "mod2", "action_payload": {}},
    ]
    
    with patch('app.services.automations.automation_repo.list_due_automations', new_callable=AsyncMock) as mock_list:
        mock_list.return_value = automations
        
        with patch('app.services.automations.db.acquire_lock') as mock_lock:
            # All locks succeed
            mock_lock.return_value.__aenter__.return_value = True
            
            with patch('app.services.automations.automation_repo.mark_started', new_callable=AsyncMock):
                with patch('app.services.automations.automation_repo.record_run', new_callable=AsyncMock):
                    with patch('app.services.automations.automation_repo.set_last_error', new_callable=AsyncMock):
                        with patch('app.services.automations.automation_repo.set_next_run', new_callable=AsyncMock):
                            with patch('app.services.automations.modules_service.trigger_module', new_callable=AsyncMock) as mock_trigger:
                                mock_trigger.return_value = {"status": "succeeded"}
                                
                                from app.services.automations import process_due_automations
                                
                                await process_due_automations()
                                
                                # Verify each automation tried to acquire its own lock
                                assert mock_lock.call_count == 2
                                lock_calls = [call[0] for call in mock_lock.call_args_list]
                                assert ("automation_exec_1",) in lock_calls
                                assert ("automation_exec_2",) in lock_calls


@pytest.mark.asyncio
async def test_scheduled_task_already_has_distributed_lock():
    """Verify that scheduled tasks already use distributed locking (baseline check)."""
    
    task = {
        "id": 789,
        "command": "test_command",
        "cron": "* * * * *"
    }
    
    with patch('app.services.scheduler.db.acquire_lock') as mock_lock:
        # Configure the lock to return False (not acquired)
        mock_lock.return_value.__aenter__.return_value = False
        
        with patch('app.services.scheduler.scheduled_tasks_repo.record_task_run', new_callable=AsyncMock) as mock_record:
            from app.services.scheduler import SchedulerService
            
            scheduler = SchedulerService()
            await scheduler._run_task(task)
            
            # Verify lock was attempted with correct name
            mock_lock.assert_called_once_with("scheduled_task_789", timeout=1)
            
            # Verify task execution was NOT recorded (because we didn't get the lock)
            mock_record.assert_not_called()
