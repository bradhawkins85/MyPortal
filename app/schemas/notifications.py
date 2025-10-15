from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


class NotificationResponse(BaseModel):
    id: int
    user_id: Optional[int] = None
    event_type: str
    message: str
    metadata: Optional[dict[str, Any]] = None
    created_at: datetime
    read_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class NotificationCreate(BaseModel):
    event_type: str = Field(..., max_length=100)
    message: str
    user_id: Optional[int] = None
    metadata: Optional[dict[str, Any]] = None


class NotificationAcknowledgeRequest(BaseModel):
    notification_ids: list[int] = Field(
        ...,
        min_length=1,
        max_length=200,
        description="List of notification identifiers to acknowledge.",
    )


class NotificationPreference(BaseModel):
    event_type: str = Field(..., max_length=100)
    channel_in_app: bool = Field(True, description="Store notifications in the in-app feed")
    channel_email: bool = Field(False, description="Deliver notifications via email")
    channel_sms: bool = Field(False, description="Deliver notifications via SMS")


class NotificationPreferenceResponse(NotificationPreference):
    class Config:
        from_attributes = True


class NotificationPreferenceUpdateRequest(BaseModel):
    preferences: list[NotificationPreference] = Field(
        default_factory=list,
        max_length=100,
        description="Complete set of notification preferences to persist for the current user.",
    )


class NotificationSummaryResponse(BaseModel):
    total_count: int = Field(0, ge=0, description="Total notifications that match the supplied filters.")
    filtered_unread_count: int = Field(
        0,
        ge=0,
        description="Unread notifications that match the supplied filters.",
    )
    global_unread_count: int = Field(
        0,
        ge=0,
        description="All unread notifications for the authenticated user regardless of filters.",
    )
