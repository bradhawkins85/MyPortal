import pytest
from datetime import datetime, timedelta, timezone

from app.services import bookings


class _MockEventTypesRepo:
    """Mock event types repository."""
    
    def __init__(self, event_type=None):
        self._event_type = event_type or {
            "id": 1,
            "user_id": 10,
            "title": "30 Minute Meeting",
            "duration_minutes": 30,
            "buffer_before_minutes": 5,
            "buffer_after_minutes": 5,
            "minimum_notice_hours": 2,
            "is_active": True,
            "requires_confirmation": False,
            "location_type": "google_meet",
            "location_value": "https://meet.google.com/abc-defg",
        }
    
    async def get_event_type(self, event_type_id):
        return self._event_type if event_type_id == self._event_type["id"] else None


class _MockSchedulesRepo:
    """Mock schedules repository."""
    
    def __init__(self):
        self.schedule = {
            "id": 1,
            "user_id": 10,
            "name": "Work Hours",
            "timezone": "UTC",
        }
    
    async def list_schedules(self, user_id):
        return [self.schedule] if user_id == 10 else []
    
    async def list_availability(self, schedule_id):
        # Monday to Friday, 9 AM to 5 PM
        return [
            {"id": i+1, "schedule_id": 1, "day_of_week": i, "start_time": "09:00:00", "end_time": "17:00:00"}
            for i in range(5)  # 0=Monday, 4=Friday
        ]
    
    async def list_date_overrides(self, schedule_id):
        return []


class _MockBookingsRepo:
    """Mock bookings repository."""
    
    def __init__(self):
        self.bookings = []
        self.attendees = []
    
    async def list_bookings(self, **kwargs):
        return self.bookings
    
    async def create_booking(self, **kwargs):
        booking = {
            "id": len(self.bookings) + 1,
            "uid": f"bk_{len(self.bookings) + 1}",
            **kwargs
        }
        self.bookings.append(booking)
        return booking
    
    async def create_attendee(self, **kwargs):
        attendee = {
            "id": len(self.attendees) + 1,
            **kwargs
        }
        self.attendees.append(attendee)
        return attendee
    
    async def get_booking(self, booking_id):
        for booking in self.bookings:
            if booking["id"] == booking_id:
                return booking
        return None
    
    async def list_attendees(self, booking_id):
        return [a for a in self.attendees if a["booking_id"] == booking_id]
    
    async def update_booking(self, booking_id, **updates):
        for booking in self.bookings:
            if booking["id"] == booking_id:
                booking.update(updates)
                return booking
        return None


@pytest.mark.asyncio
async def test_calculate_available_slots(monkeypatch):
    """Test calculating available time slots."""
    event_types_repo = _MockEventTypesRepo()
    schedules_repo = _MockSchedulesRepo()
    bookings_repo = _MockBookingsRepo()
    
    monkeypatch.setattr("app.services.bookings.event_types_repo", event_types_repo)
    monkeypatch.setattr("app.services.bookings.schedules_repo", schedules_repo)
    monkeypatch.setattr("app.services.bookings.bookings_repo", bookings_repo)
    
    # Get slots for next Monday (assuming today is before that)
    today = datetime.now(timezone.utc)
    # Find next Monday
    days_ahead = (0 - today.weekday()) % 7
    if days_ahead == 0:
        days_ahead = 7
    next_monday = (today + timedelta(days=days_ahead)).replace(hour=0, minute=0, second=0, microsecond=0)
    next_friday = next_monday + timedelta(days=4)
    
    slots = await bookings.calculate_available_slots(
        event_type_id=1,
        start_date=next_monday,
        end_date=next_friday,
        timezone="UTC"
    )
    
    # Should have slots for Monday through Friday
    assert isinstance(slots, dict)
    # We should have at least one day with slots
    assert len(slots) > 0


@pytest.mark.asyncio
async def test_create_booking_with_attendee(monkeypatch):
    """Test creating a booking with an attendee."""
    event_types_repo = _MockEventTypesRepo()
    bookings_repo = _MockBookingsRepo()
    
    monkeypatch.setattr("app.services.bookings.event_types_repo", event_types_repo)
    monkeypatch.setattr("app.services.bookings.bookings_repo", bookings_repo)
    
    start_time = datetime.now(timezone.utc) + timedelta(hours=24)
    
    booking = await bookings.create_booking_with_attendee(
        event_type_id=1,
        start_time=start_time,
        attendee_name="Jane Doe",
        attendee_email="jane@example.com",
        attendee_timezone="America/New_York",
        attendee_notes="Looking forward to the meeting",
    )
    
    assert booking is not None
    assert booking["title"] == "30 Minute Meeting"
    assert booking["status"] == "accepted"
    assert "attendees" in booking
    assert len(booking["attendees"]) == 1
    assert booking["attendees"][0]["name"] == "Jane Doe"
    assert booking["attendees"][0]["email"] == "jane@example.com"


@pytest.mark.asyncio
async def test_create_booking_requires_confirmation(monkeypatch):
    """Test creating a booking that requires confirmation."""
    event_type = {
        "id": 1,
        "user_id": 10,
        "title": "Consultation",
        "duration_minutes": 60,
        "buffer_before_minutes": 0,
        "buffer_after_minutes": 0,
        "is_active": True,
        "requires_confirmation": True,  # Requires manual confirmation
        "location_type": "phone",
        "location_value": "+1234567890",
    }
    
    event_types_repo = _MockEventTypesRepo(event_type)
    bookings_repo = _MockBookingsRepo()
    
    monkeypatch.setattr("app.services.bookings.event_types_repo", event_types_repo)
    monkeypatch.setattr("app.services.bookings.bookings_repo", bookings_repo)
    
    start_time = datetime.now(timezone.utc) + timedelta(hours=24)
    
    booking = await bookings.create_booking_with_attendee(
        event_type_id=1,
        start_time=start_time,
        attendee_name="John Smith",
        attendee_email="john@example.com",
    )
    
    assert booking is not None
    assert booking["status"] == "pending"  # Should be pending, not accepted


@pytest.mark.asyncio
async def test_reschedule_booking(monkeypatch):
    """Test rescheduling a booking."""
    event_types_repo = _MockEventTypesRepo()
    bookings_repo = _MockBookingsRepo()
    
    monkeypatch.setattr("app.services.bookings.event_types_repo", event_types_repo)
    monkeypatch.setattr("app.services.bookings.bookings_repo", bookings_repo)
    
    # Create initial booking
    start_time = datetime.now(timezone.utc) + timedelta(hours=24)
    booking = await bookings.create_booking_with_attendee(
        event_type_id=1,
        start_time=start_time,
        attendee_name="Jane Doe",
        attendee_email="jane@example.com",
    )
    
    # Reschedule to different time
    new_start_time = start_time + timedelta(hours=2)
    rescheduled = await bookings.reschedule_booking(
        booking_id=booking["id"],
        new_start_time=new_start_time,
    )
    
    assert rescheduled is not None
    assert rescheduled["start_time"] == new_start_time
    assert rescheduled["end_time"] == new_start_time + timedelta(minutes=30)


@pytest.mark.asyncio
async def test_cancel_booking(monkeypatch):
    """Test cancelling a booking."""
    event_types_repo = _MockEventTypesRepo()
    bookings_repo = _MockBookingsRepo()
    
    monkeypatch.setattr("app.services.bookings.event_types_repo", event_types_repo)
    monkeypatch.setattr("app.services.bookings.bookings_repo", bookings_repo)
    
    # Create initial booking
    start_time = datetime.now(timezone.utc) + timedelta(hours=24)
    booking = await bookings.create_booking_with_attendee(
        event_type_id=1,
        start_time=start_time,
        attendee_name="Jane Doe",
        attendee_email="jane@example.com",
    )
    
    # Cancel the booking
    cancelled = await bookings.cancel_booking(booking_id=booking["id"])
    
    assert cancelled is not None
    assert cancelled["status"] == "cancelled"
