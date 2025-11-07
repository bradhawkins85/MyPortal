"""Tests for ticket views repository layer."""
import pytest

from app.repositories import ticket_views


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.mark.anyio
async def test_normalise_ticket_view_handles_json_filters():
    """Test that ticket view normalisation handles JSON filters correctly."""
    import json
    from datetime import datetime, timezone
    
    # Mock row data
    row = {
        "id": 1,
        "user_id": 10,
        "name": "Test View",
        "description": "Test description",
        "filters": json.dumps({"status": ["open", "in_progress"]}),
        "grouping_field": "status",
        "sort_field": "created_at",
        "sort_direction": "desc",
        "is_default": 1,
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
    }
    
    # Call the private normalisation function
    result = ticket_views._normalise_ticket_view(row)
    
    # Verify
    assert result["id"] == 1
    assert result["user_id"] == 10
    assert result["name"] == "Test View"
    assert isinstance(result["filters"], dict)
    assert result["filters"]["status"] == ["open", "in_progress"]
    assert result["is_default"] is True


@pytest.mark.anyio
async def test_normalise_ticket_view_handles_none_filters():
    """Test that ticket view normalisation handles None filters."""
    from datetime import datetime, timezone
    
    row = {
        "id": 1,
        "user_id": 10,
        "name": "Test View",
        "description": None,
        "filters": None,
        "grouping_field": None,
        "sort_field": None,
        "sort_direction": None,
        "is_default": 0,
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
    }
    
    result = ticket_views._normalise_ticket_view(row)
    
    assert result["filters"] is None
    assert result["is_default"] is False

