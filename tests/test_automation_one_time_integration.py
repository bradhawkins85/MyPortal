"""Integration test for one-time scheduling end-to-end flow."""
import pytest
from datetime import datetime, timedelta, timezone

from app.repositories import automations as automation_repo
from app.services import automations as automation_service


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio
async def test_one_time_automation_end_to_end(monkeypatch):
    """Test creating and executing a one-time automation."""
    
    # Mock database operations
    created_automation = None
    next_run_updates = []
    
    async def mock_create_automation(**kwargs):
        nonlocal created_automation
        automation_id = 999
        created_automation = {
            "id": automation_id,
            "name": kwargs["name"],
            "description": kwargs.get("description"),
            "kind": kwargs["kind"],
            "cadence": kwargs.get("cadence"),
            "cron_expression": kwargs.get("cron_expression"),
            "scheduled_time": kwargs.get("scheduled_time"),
            "run_once": kwargs.get("run_once", False),
            "trigger_event": kwargs.get("trigger_event"),
            "trigger_filters": kwargs.get("trigger_filters"),
            "action_module": kwargs.get("action_module"),
            "action_payload": kwargs.get("action_payload"),
            "status": kwargs.get("status", "inactive"),
            "next_run_at": kwargs.get("next_run_at"),
            "last_run_at": None,
            "last_error": None,
            "created_at": datetime.now(timezone.utc),
            "updated_at": datetime.now(timezone.utc),
        }
        return created_automation
    
    async def mock_get_automation(automation_id: int):
        if created_automation and created_automation["id"] == automation_id:
            return dict(created_automation)
        return None
    
    async def mock_set_next_run(automation_id: int, next_run_at):
        nonlocal next_run_updates
        next_run_updates.append((automation_id, next_run_at))
        if created_automation and created_automation["id"] == automation_id:
            created_automation["next_run_at"] = next_run_at
    
    monkeypatch.setattr(automation_repo, "create_automation", mock_create_automation)
    monkeypatch.setattr(automation_repo, "get_automation", mock_get_automation)
    monkeypatch.setattr(automation_repo, "set_next_run", mock_set_next_run)
    
    # Create a one-time automation scheduled for tomorrow
    tomorrow = datetime.now(timezone.utc) + timedelta(days=1)
    
    data = {
        "name": "Test One-time Automation",
        "description": "This should run once tomorrow",
        "kind": "scheduled",
        "run_once": True,
        "scheduled_time": tomorrow,
        "action_module": "test_module",
        "action_payload": {"test": "data"},
        "status": "active",
        "cadence": None,
        "cron_expression": None,
        "trigger_event": None,
        "trigger_filters": None,
    }
    
    # Calculate next run (should return the scheduled time)
    next_run = automation_service.calculate_next_run(data)
    assert next_run == tomorrow
    
    # Create the automation
    automation = await automation_repo.create_automation(
        name=data["name"],
        description=data["description"],
        kind=data["kind"],
        cadence=data.get("cadence"),
        cron_expression=data.get("cron_expression"),
        scheduled_time=data.get("scheduled_time"),
        run_once=data.get("run_once", False),
        trigger_event=data.get("trigger_event"),
        trigger_filters=data.get("trigger_filters"),
        action_module=data.get("action_module"),
        action_payload=data.get("action_payload"),
        status=data.get("status", "inactive"),
        next_run_at=next_run,
    )
    
    assert automation is not None
    assert automation["run_once"] is True
    assert automation["scheduled_time"] == tomorrow
    assert automation["next_run_at"] == tomorrow
    assert automation["last_run_at"] is None
    
    # Simulate the automation running
    automation["last_run_at"] = datetime.now(timezone.utc)
    
    # Calculate next run after execution (should return None)
    next_run_after = automation_service.calculate_next_run(automation)
    assert next_run_after is None, "One-time automation should not reschedule after execution"


@pytest.mark.anyio
async def test_one_time_automation_already_scheduled_in_past(monkeypatch):
    """Test one-time automation with a scheduled time in the past."""
    
    # Mock database operations
    async def mock_create_automation(**kwargs):
        return {
            "id": 888,
            "scheduled_time": kwargs.get("scheduled_time"),
            "run_once": kwargs.get("run_once", False),
            "last_run_at": None,
            "kind": "scheduled",
        }
    
    monkeypatch.setattr(automation_repo, "create_automation", mock_create_automation)
    
    # Schedule for yesterday
    yesterday = datetime.now(timezone.utc) - timedelta(days=1)
    
    data = {
        "kind": "scheduled",
        "run_once": True,
        "scheduled_time": yesterday,
        "last_run_at": None,
    }
    
    # Calculate next run (should still return the scheduled time to run ASAP)
    next_run = automation_service.calculate_next_run(data)
    assert next_run == yesterday, "Should return scheduled time even if in the past for immediate execution"
