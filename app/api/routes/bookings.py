from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.dependencies.auth import get_current_user
from app.api.dependencies.database import require_database
from app.repositories import booking_event_types as event_types_repo
from app.repositories import booking_schedules as schedules_repo
from app.repositories import booking_webhooks as webhooks_repo
from app.repositories import bookings as bookings_repo
from app.repositories import users as users_repo
from app.schemas.bookings import (
    AvailableSlotsRequest,
    AvailableSlotsResponse,
    BookingCreate,
    BookingReschedule,
    BookingResponse,
    EventTypeCreate,
    EventTypeResponse,
    EventTypeUpdate,
    WebhookCreate,
    WebhookResponse,
    WebhookUpdate,
)
from app.services import bookings as bookings_service

router = APIRouter(prefix="/v2/bookings", tags=["Bookings"])


@router.post("", response_model=BookingResponse, status_code=status.HTTP_201_CREATED)
async def create_booking(
    payload: BookingCreate,
    _: None = Depends(require_database),
):
    """Create a new booking."""
    try:
        booking = await bookings_service.create_booking_with_attendee(
            event_type_id=payload.event_type_id,
            start_time=payload.start,
            attendee_name=payload.attendee.name,
            attendee_email=payload.attendee.email,
            attendee_timezone=payload.attendee.timezone,
            attendee_notes=payload.attendee.notes,
            metadata=payload.metadata,
        )

        # Get host user info
        host = await users_repo.get_user_by_id(booking["host_user_id"])
        if host:
            booking["host"] = {
                "id": host["id"],
                "name": f"{host.get('first_name', '')} {host.get('last_name', '')}".strip() or host.get("email"),
                "email": host.get("email"),
            }

        return booking
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get("/{uid}", response_model=BookingResponse)
async def get_booking(
    uid: str,
    _: None = Depends(require_database),
):
    """Get a booking by UID."""
    booking = await bookings_repo.get_booking_by_uid(uid)
    if not booking:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Booking not found")

    # Get attendees
    attendees = await bookings_repo.list_attendees(booking["id"])
    booking["attendees"] = attendees

    # Get host user info
    host = await users_repo.get_user_by_id(booking["host_user_id"])
    if host:
        booking["host"] = {
            "id": host["id"],
            "name": f"{host.get('first_name', '')} {host.get('last_name', '')}".strip() or host.get("email"),
            "email": host.get("email"),
        }

    return booking


@router.patch("/{uid}", response_model=BookingResponse)
async def reschedule_booking(
    uid: str,
    payload: BookingReschedule,
    _: None = Depends(require_database),
):
    """Reschedule a booking."""
    booking = await bookings_repo.get_booking_by_uid(uid)
    if not booking:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Booking not found")

    try:
        updated_booking = await bookings_service.reschedule_booking(
            booking_id=booking["id"],
            new_start_time=payload.start,
        )

        # Get host user info
        host = await users_repo.get_user_by_id(updated_booking["host_user_id"])
        if host:
            updated_booking["host"] = {
                "id": host["id"],
                "name": f"{host.get('first_name', '')} {host.get('last_name', '')}".strip() or host.get("email"),
                "email": host.get("email"),
            }

        return updated_booking
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.delete("/{uid}", status_code=status.HTTP_204_NO_CONTENT)
async def cancel_booking(
    uid: str,
    _: None = Depends(require_database),
):
    """Cancel a booking."""
    booking = await bookings_repo.get_booking_by_uid(uid)
    if not booking:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Booking not found")

    try:
        await bookings_service.cancel_booking(booking_id=booking["id"])
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get("/slots/available", response_model=AvailableSlotsResponse)
async def get_available_slots(
    event_type_id: int = Query(..., description="Event type ID"),
    start_time: str = Query(..., description="Start time in ISO 8601 format"),
    end_time: str = Query(..., description="End time in ISO 8601 format"),
    timezone: str = Query(default="UTC", description="Timezone for slot calculation"),
    _: None = Depends(require_database),
):
    """Get available time slots for an event type."""
    try:
        start_dt = datetime.fromisoformat(start_time.replace("Z", "+00:00"))
        end_dt = datetime.fromisoformat(end_time.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid datetime format. Use ISO 8601 format."
        ) from exc

    try:
        slots = await bookings_service.calculate_available_slots(
            event_type_id=event_type_id,
            start_date=start_dt,
            end_date=end_dt,
            timezone=timezone,
        )
        return {"slots": slots}
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


# Event Types endpoints
event_types_router = APIRouter(prefix="/v2/event-types", tags=["Event Types"])


@event_types_router.get("", response_model=list[EventTypeResponse])
async def list_event_types(
    is_active: bool | None = Query(default=None, description="Filter by active status"),
    _: None = Depends(require_database),
    current_user: dict = Depends(get_current_user),
):
    """List event types for the current user."""
    event_types = await event_types_repo.list_event_types(
        user_id=current_user["id"],
        is_active=is_active,
    )
    return event_types


@event_types_router.post("", response_model=EventTypeResponse, status_code=status.HTTP_201_CREATED)
async def create_event_type(
    payload: EventTypeCreate,
    _: None = Depends(require_database),
    current_user: dict = Depends(get_current_user),
):
    """Create a new event type."""
    # Check for duplicate slug
    existing = await event_types_repo.get_event_type_by_slug(current_user["id"], payload.slug)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An event type with this slug already exists"
        )

    data = payload.model_dump()
    event_type = await event_types_repo.create_event_type(user_id=current_user["id"], **data)
    return event_type


@event_types_router.get("/{event_type_id}", response_model=EventTypeResponse)
async def get_event_type(
    event_type_id: int,
    _: None = Depends(require_database),
    current_user: dict = Depends(get_current_user),
):
    """Get an event type by ID."""
    event_type = await event_types_repo.get_event_type(event_type_id)
    if not event_type:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Event type not found")

    # Check ownership
    if event_type["user_id"] != current_user["id"] and not current_user.get("is_super_admin"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")

    return event_type


@event_types_router.patch("/{event_type_id}", response_model=EventTypeResponse)
async def update_event_type(
    event_type_id: int,
    payload: EventTypeUpdate,
    _: None = Depends(require_database),
    current_user: dict = Depends(get_current_user),
):
    """Update an event type."""
    event_type = await event_types_repo.get_event_type(event_type_id)
    if not event_type:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Event type not found")

    # Check ownership
    if event_type["user_id"] != current_user["id"] and not current_user.get("is_super_admin"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")

    data = payload.model_dump(exclude_unset=True)
    updated = await event_types_repo.update_event_type(event_type_id, **data)
    return updated


@event_types_router.delete("/{event_type_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_event_type(
    event_type_id: int,
    _: None = Depends(require_database),
    current_user: dict = Depends(get_current_user),
):
    """Delete an event type."""
    event_type = await event_types_repo.get_event_type(event_type_id)
    if not event_type:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Event type not found")

    # Check ownership
    if event_type["user_id"] != current_user["id"] and not current_user.get("is_super_admin"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")

    await event_types_repo.delete_event_type(event_type_id)


# Webhooks endpoints
webhooks_router = APIRouter(prefix="/v2/webhooks", tags=["Booking Webhooks"])


@webhooks_router.get("", response_model=list[WebhookResponse])
async def list_webhooks(
    _: None = Depends(require_database),
    current_user: dict = Depends(get_current_user),
):
    """List webhooks for the current user."""
    webhooks = await webhooks_repo.list_webhooks(user_id=current_user["id"])
    # Remove secret_hash from response
    for webhook in webhooks:
        webhook.pop("secret_hash", None)
    return webhooks


@webhooks_router.post("", response_model=WebhookResponse, status_code=status.HTTP_201_CREATED)
async def create_webhook(
    payload: WebhookCreate,
    _: None = Depends(require_database),
    current_user: dict = Depends(get_current_user),
):
    """Create a new webhook."""
    import hashlib

    secret_hash = None
    if payload.secret:
        secret_hash = hashlib.sha256(payload.secret.encode()).hexdigest()

    webhook = await webhooks_repo.create_webhook(
        user_id=current_user["id"],
        subscriber_url=payload.subscriber_url,
        event_triggers=payload.event_triggers,
        active=payload.active,
        secret_hash=secret_hash,
    )

    # Remove secret_hash from response
    webhook.pop("secret_hash", None)
    return webhook


@webhooks_router.patch("/{webhook_id}", response_model=WebhookResponse)
async def update_webhook(
    webhook_id: int,
    payload: WebhookUpdate,
    _: None = Depends(require_database),
    current_user: dict = Depends(get_current_user),
):
    """Update a webhook."""
    webhook = await webhooks_repo.get_webhook(webhook_id)
    if not webhook:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Webhook not found")

    # Check ownership
    if webhook["user_id"] != current_user["id"] and not current_user.get("is_super_admin"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")

    data = payload.model_dump(exclude_unset=True)
    updated = await webhooks_repo.update_webhook(webhook_id, **data)

    # Remove secret_hash from response
    updated.pop("secret_hash", None)
    return updated


@webhooks_router.delete("/{webhook_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_webhook(
    webhook_id: int,
    _: None = Depends(require_database),
    current_user: dict = Depends(get_current_user),
):
    """Delete a webhook."""
    webhook = await webhooks_repo.get_webhook(webhook_id)
    if not webhook:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Webhook not found")

    # Check ownership
    if webhook["user_id"] != current_user["id"] and not current_user.get("is_super_admin"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")

    await webhooks_repo.delete_webhook(webhook_id)
