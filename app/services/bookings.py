from __future__ import annotations

from datetime import datetime, time, timedelta
from typing import Any

from app.repositories import booking_event_types as event_types_repo
from app.repositories import booking_schedules as schedules_repo
from app.repositories import bookings as bookings_repo


async def calculate_available_slots(
    event_type_id: int,
    start_date: datetime,
    end_date: datetime,
    timezone: str = "UTC"
) -> dict[str, list[str]]:
    """
    Calculate available time slots for an event type within a date range.
    
    Returns a dictionary mapping dates to available time slots.
    """
    event_type = await event_types_repo.get_event_type(event_type_id)
    if not event_type:
        raise ValueError("Event type not found")

    user_id = event_type["user_id"]
    duration_minutes = event_type["duration_minutes"]
    buffer_before = event_type.get("buffer_before_minutes", 0)
    buffer_after = event_type.get("buffer_after_minutes", 0)
    minimum_notice_hours = event_type.get("minimum_notice_hours", 0)

    # Get schedules for the user
    schedules = await schedules_repo.list_schedules(user_id)
    if not schedules:
        return {}

    # Use the first (default) schedule
    schedule = schedules[0]
    schedule_id = schedule["id"]

    # Get availability rules
    availability_rules = await schedules_repo.list_availability(schedule_id)
    date_overrides = await schedules_repo.list_date_overrides(schedule_id)

    # Get existing bookings in the date range
    existing_bookings = await bookings_repo.list_bookings(
        host_user_id=user_id,
        start_after=start_date,
        start_before=end_date,
        status="accepted"
    )

    slots: dict[str, list[str]] = {}
    current_date = start_date.date()
    end_date_only = end_date.date()

    earliest_booking_time = datetime.now() + timedelta(hours=minimum_notice_hours)

    while current_date <= end_date_only:
        day_of_week = current_date.weekday()  # 0=Monday, 6=Sunday
        date_str = current_date.isoformat()

        # Check for date overrides first
        override = None
        for ovr in date_overrides:
            if ovr["override_date"] == current_date:
                override = ovr
                break

        if override and not override["is_available"]:
            # Date is blocked
            current_date += timedelta(days=1)
            continue

        # Get availability rules for this day
        day_rules = [rule for rule in availability_rules if rule["day_of_week"] == day_of_week]

        if not day_rules and not override:
            # No availability for this day
            current_date += timedelta(days=1)
            continue

        # Generate time slots
        day_slots = []

        for rule in day_rules:
            start_time = rule["start_time"]
            end_time = rule["end_time"]

            # Convert time objects to datetime for the current date
            slot_start = datetime.combine(current_date, start_time)
            slot_end = datetime.combine(current_date, end_time)

            current_slot = slot_start
            while current_slot + timedelta(minutes=duration_minutes) <= slot_end:
                # Check if slot is in the future with minimum notice
                if current_slot >= earliest_booking_time:
                    # Check for conflicts with existing bookings
                    slot_end_time = current_slot + timedelta(minutes=duration_minutes)
                    has_conflict = False

                    for booking in existing_bookings:
                        booking_start = booking["start_time"]
                        booking_end = booking["end_time"]

                        # Add buffers to booking time
                        buffered_start = booking_start - timedelta(minutes=buffer_before)
                        buffered_end = booking_end + timedelta(minutes=buffer_after)

                        # Check for overlap
                        if (current_slot < buffered_end and slot_end_time > buffered_start):
                            has_conflict = True
                            break

                    if not has_conflict:
                        day_slots.append(current_slot.strftime("%H:%M:%S"))

                # Move to next slot (increment by duration)
                current_slot += timedelta(minutes=duration_minutes)

        if day_slots:
            slots[date_str] = day_slots

        current_date += timedelta(days=1)

    return slots


async def create_booking_with_attendee(
    event_type_id: int,
    start_time: datetime,
    attendee_name: str,
    attendee_email: str,
    attendee_timezone: str = "UTC",
    attendee_notes: str | None = None,
    metadata: dict[str, Any] | None = None
) -> dict[str, Any]:
    """
    Create a booking with an attendee.
    
    Returns the created booking with attendee information.
    """
    event_type = await event_types_repo.get_event_type(event_type_id)
    if not event_type:
        raise ValueError("Event type not found")

    if not event_type.get("is_active"):
        raise ValueError("Event type is not active")

    user_id = event_type["user_id"]
    duration_minutes = event_type["duration_minutes"]
    title = event_type["title"]
    location_type = event_type.get("location_type")
    location_value = event_type.get("location_value")

    end_time = start_time + timedelta(minutes=duration_minutes)

    # Determine status based on confirmation requirement
    requires_confirmation = event_type.get("requires_confirmation", False)
    status = "pending" if requires_confirmation else "accepted"

    # Create the booking
    booking = await bookings_repo.create_booking(
        event_type_id=event_type_id,
        host_user_id=user_id,
        title=title,
        start_time=start_time,
        end_time=end_time,
        status=status,
        location_type=location_type,
        location_value=location_value,
        metadata=metadata
    )

    # Create the attendee
    await bookings_repo.create_attendee(
        booking_id=booking["id"],
        name=attendee_name,
        email=attendee_email,
        timezone=attendee_timezone,
        notes=attendee_notes,
        metadata=metadata
    )

    # Reload booking with attendee info
    booking = await bookings_repo.get_booking(booking["id"])
    attendees = await bookings_repo.list_attendees(booking["id"])
    booking["attendees"] = attendees

    return booking


async def reschedule_booking(
    booking_id: int,
    new_start_time: datetime
) -> dict[str, Any]:
    """
    Reschedule a booking to a new start time.
    
    Returns the updated booking.
    """
    booking = await bookings_repo.get_booking(booking_id)
    if not booking:
        raise ValueError("Booking not found")

    event_type = await event_types_repo.get_event_type(booking["event_type_id"])
    if not event_type:
        raise ValueError("Event type not found")

    duration_minutes = event_type["duration_minutes"]
    new_end_time = new_start_time + timedelta(minutes=duration_minutes)

    # Update the booking
    updated_booking = await bookings_repo.update_booking(
        booking_id,
        start_time=new_start_time,
        end_time=new_end_time
    )

    # Reload with attendees
    attendees = await bookings_repo.list_attendees(booking_id)
    updated_booking["attendees"] = attendees

    return updated_booking


async def cancel_booking(booking_id: int) -> dict[str, Any]:
    """
    Cancel a booking by setting its status to cancelled.
    
    Returns the cancelled booking.
    """
    booking = await bookings_repo.get_booking(booking_id)
    if not booking:
        raise ValueError("Booking not found")

    # Update status to cancelled
    cancelled_booking = await bookings_repo.update_booking(
        booking_id,
        status="cancelled"
    )

    # Reload with attendees
    attendees = await bookings_repo.list_attendees(booking_id)
    cancelled_booking["attendees"] = attendees

    return cancelled_booking
