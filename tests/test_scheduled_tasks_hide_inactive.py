"""Test that inactive scheduled tasks are hidden by default."""
import pytest
from unittest.mock import AsyncMock, patch

from app.repositories import scheduled_tasks as scheduled_tasks_repo


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.mark.anyio("asyncio")
async def test_list_tasks_hides_inactive_by_default():
    """Test that list_tasks() hides inactive tasks by default."""
    mock_fetch_all = AsyncMock(return_value=[
        {
            "id": 1,
            "name": "Active Task",
            "command": "sync_staff",
            "cron": "0 0 * * *",
            "active": 1,
            "company_id": None,
            "last_run_at": None,
            "max_retries": 12,
            "retry_backoff_seconds": 300,
        }
    ])
    
    with patch('app.repositories.scheduled_tasks.db.fetch_all', mock_fetch_all):
        tasks = await scheduled_tasks_repo.list_tasks()
    
    # Verify WHERE active = 1 clause is used
    mock_fetch_all.assert_called_once()
    call_args = mock_fetch_all.call_args
    query = call_args[0][0]
    assert "WHERE active = 1" in query
    assert len(tasks) == 1


@pytest.mark.anyio("asyncio")
async def test_list_tasks_shows_all_when_requested():
    """Test that list_tasks(include_inactive=True) shows all tasks."""
    mock_fetch_all = AsyncMock(return_value=[
        {
            "id": 1,
            "name": "Active Task",
            "command": "sync_staff",
            "cron": "0 0 * * *",
            "active": 1,
            "company_id": None,
            "last_run_at": None,
            "max_retries": 12,
            "retry_backoff_seconds": 300,
        },
        {
            "id": 2,
            "name": "Inactive Task",
            "command": "sync_o365",
            "cron": "0 1 * * *",
            "active": 0,
            "company_id": None,
            "last_run_at": None,
            "max_retries": 12,
            "retry_backoff_seconds": 300,
        }
    ])
    
    with patch('app.repositories.scheduled_tasks.db.fetch_all', mock_fetch_all):
        tasks = await scheduled_tasks_repo.list_tasks(include_inactive=True)
    
    # Verify no WHERE clause is used
    mock_fetch_all.assert_called_once()
    call_args = mock_fetch_all.call_args
    query = call_args[0][0]
    assert "WHERE" not in query
    assert len(tasks) == 2


@pytest.mark.anyio("asyncio")
async def test_list_active_tasks_still_works():
    """Test that list_active_tasks() continues to work as before."""
    mock_fetch_all = AsyncMock(return_value=[
        {
            "id": 1,
            "name": "Active Task",
            "command": "sync_staff",
            "cron": "0 0 * * *",
            "active": 1,
            "company_id": None,
            "last_run_at": None,
            "max_retries": 12,
            "retry_backoff_seconds": 300,
        }
    ])
    
    with patch('app.repositories.scheduled_tasks.db.fetch_all', mock_fetch_all):
        tasks = await scheduled_tasks_repo.list_active_tasks()
    
    # Verify WHERE active = 1 clause is used
    mock_fetch_all.assert_called_once()
    call_args = mock_fetch_all.call_args
    query = call_args[0][0]
    assert "WHERE active = 1" in query
    assert len(tasks) == 1
