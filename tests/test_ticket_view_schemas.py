"""Tests for ticket view schemas."""
import pytest
from pydantic import ValidationError

from app.schemas.tickets import TicketViewCreate, TicketViewFilters, TicketViewUpdate


def test_ticket_view_filters_valid():
    """Test valid ticket view filters."""
    filters = TicketViewFilters(
        status=["open", "in_progress"],
        priority=["high", "urgent"],
        search="test query"
    )
    assert filters.status == ["open", "in_progress"]
    assert filters.priority == ["high", "urgent"]
    assert filters.search == "test query"


def test_ticket_view_filters_optional():
    """Test that all filter fields are optional."""
    filters = TicketViewFilters()
    assert filters.status is None
    assert filters.priority is None
    assert filters.search is None


def test_ticket_view_create_minimal():
    """Test creating a ticket view with minimal fields."""
    view = TicketViewCreate(name="Test View")
    assert view.name == "Test View"
    assert view.description is None
    assert view.filters is None
    assert view.grouping_field is None
    assert view.is_default is False


def test_ticket_view_create_full():
    """Test creating a ticket view with all fields."""
    filters = TicketViewFilters(status=["open"])
    view = TicketViewCreate(
        name="Full View",
        description="Description here",
        filters=filters,
        grouping_field="status",
        sort_field="created_at",
        sort_direction="desc",
        is_default=True
    )
    assert view.name == "Full View"
    assert view.description == "Description here"
    assert view.filters is not None
    assert view.grouping_field == "status"
    assert view.sort_field == "created_at"
    assert view.sort_direction == "desc"
    assert view.is_default is True


def test_ticket_view_create_name_required():
    """Test that name is required for view creation."""
    with pytest.raises(ValidationError) as exc_info:
        TicketViewCreate()
    
    errors = exc_info.value.errors()
    assert any(error["loc"] == ("name",) for error in errors)


def test_ticket_view_create_invalid_sort_direction():
    """Test that invalid sort direction is rejected."""
    with pytest.raises(ValidationError) as exc_info:
        TicketViewCreate(name="Test", sort_direction="invalid")
    
    errors = exc_info.value.errors()
    assert any("sort_direction" in str(error["loc"]) for error in errors)


def test_ticket_view_update_all_optional():
    """Test that all update fields are optional."""
    update = TicketViewUpdate()
    assert update.name is None
    assert update.description is None
    assert update.filters is None
    assert update.grouping_field is None
    assert update.is_default is None


def test_ticket_view_update_partial():
    """Test updating only some fields."""
    update = TicketViewUpdate(
        name="Updated Name",
        grouping_field="priority"
    )
    assert update.name == "Updated Name"
    assert update.grouping_field == "priority"
    assert update.description is None
