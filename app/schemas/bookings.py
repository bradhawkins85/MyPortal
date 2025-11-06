from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, EmailStr, Field


class EventTypeBase(BaseModel):
    """Base model for event types."""
    title: str = Field(..., min_length=1, max_length=255)
    slug: str = Field(..., min_length=1, max_length=255)
    description: str | None = None
    duration_minutes: int = Field(default=30, ge=5, le=1440)
    buffer_before_minutes: int = Field(default=0, ge=0, le=120)
    buffer_after_minutes: int = Field(default=0, ge=0, le=120)
    minimum_notice_hours: int = Field(default=0, ge=0)
    max_days_in_future: int = Field(default=60, ge=1, le=365)
    location_type: str | None = None
    location_value: str | None = None
    is_active: bool = True
    requires_confirmation: bool = False
    allow_guests: bool = True
    max_guests: int = Field(default=0, ge=0, le=100)
    metadata: dict[str, Any] | None = None


class EventTypeCreate(EventTypeBase):
    """Request model for creating an event type."""
    pass


class EventTypeUpdate(BaseModel):
    """Request model for updating an event type."""
    title: str | None = Field(None, min_length=1, max_length=255)
    description: str | None = None
    duration_minutes: int | None = Field(None, ge=5, le=1440)
    buffer_before_minutes: int | None = Field(None, ge=0, le=120)
    buffer_after_minutes: int | None = Field(None, ge=0, le=120)
    minimum_notice_hours: int | None = Field(None, ge=0)
    max_days_in_future: int | None = Field(None, ge=1, le=365)
    location_type: str | None = None
    location_value: str | None = None
    is_active: bool | None = None
    requires_confirmation: bool | None = None
    allow_guests: bool | None = None
    max_guests: int | None = Field(None, ge=0, le=100)
    metadata: dict[str, Any] | None = None


class EventTypeResponse(EventTypeBase):
    """Response model for event types."""
    id: int
    user_id: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class AttendeeInfo(BaseModel):
    """Information about a booking attendee."""
    name: str = Field(..., min_length=1, max_length=255)
    email: EmailStr
    timezone: str = Field(default="UTC")
    notes: str | None = None
    metadata: dict[str, Any] | None = None


class BookingCreate(BaseModel):
    """Request model for creating a booking."""
    event_type_id: int
    start: datetime
    attendee: AttendeeInfo
    metadata: dict[str, Any] | None = None


class BookingReschedule(BaseModel):
    """Request model for rescheduling a booking."""
    start: datetime


class BookingResponse(BaseModel):
    """Response model for bookings."""
    uid: str
    id: int
    event_type_id: int
    title: str
    start: datetime = Field(alias="start_time")
    end: datetime = Field(alias="end_time")
    status: str
    location_type: str | None = None
    location_value: str | None = None
    host: dict[str, Any] | None = None
    attendees: list[dict[str, Any]] = []
    metadata: dict[str, Any] | None = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
        populate_by_name = True


class AvailableSlotsRequest(BaseModel):
    """Request model for getting available slots."""
    event_type_id: int
    start_time: datetime
    end_time: datetime
    timezone: str = Field(default="UTC")


class AvailableSlotsResponse(BaseModel):
    """Response model for available slots."""
    slots: dict[str, list[str]]


class WebhookCreate(BaseModel):
    """Request model for creating a webhook."""
    subscriber_url: str = Field(..., min_length=1, max_length=500)
    event_triggers: list[str] = Field(..., min_items=1)
    active: bool = True
    secret: str | None = None


class WebhookUpdate(BaseModel):
    """Request model for updating a webhook."""
    subscriber_url: str | None = Field(None, min_length=1, max_length=500)
    event_triggers: list[str] | None = Field(None, min_items=1)
    active: bool | None = None


class WebhookResponse(BaseModel):
    """Response model for webhooks."""
    id: int
    user_id: int
    subscriber_url: str
    event_triggers: list[str]
    active: bool
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
