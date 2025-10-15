from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.dependencies.auth import get_current_user
from app.api.dependencies.auth import require_super_admin
from app.api.dependencies.database import require_database
from app.core.notifications import DEFAULT_NOTIFICATION_EVENT_TYPES, merge_event_types
from app.repositories import notifications as notifications_repo
from app.repositories import notification_preferences as preferences_repo
from app.schemas.notifications import (
    NotificationAcknowledgeRequest,
    NotificationCreate,
    NotificationPreferenceResponse,
    NotificationPreferenceUpdateRequest,
    NotificationResponse,
)

router = APIRouter(prefix="/api/notifications", tags=["Notifications"])


@router.get(
    "",
    response_model=list[NotificationResponse],
    summary="List notifications",
    response_description="A collection of notifications that match the requested filters.",
)
async def list_notifications(
    unread_only: bool = Query(
        default=False,
        description="Deprecated. Use read_state=unread instead.",
        deprecated=True,
    ),
    read_state: Literal["all", "unread", "read"] = Query(
        default="all",
        description="Read state to filter by.",
    ),
    event_types: list[str] | None = Query(
        default=None,
        alias="event_type",
        description="Filter by one or more event types.",
    ),
    search: str | None = Query(
        default=None,
        min_length=1,
        max_length=200,
        description="Full-text search across notification messages and metadata.",
    ),
    created_from: datetime | None = Query(
        default=None,
        description="Return notifications created on or after this ISO 8601 timestamp.",
    ),
    created_to: datetime | None = Query(
        default=None,
        description=(
            "Return notifications created before this ISO 8601 timestamp. "
            "Provide a date with time 00:00 to include the entire day."
        ),
    ),
    sort_by: Literal["created_at", "event_type", "read_at"] = Query(
        default="created_at",
        description="Column to sort by.",
    ),
    sort_direction: Literal["asc", "desc"] = Query(
        default="desc",
        alias="sort_order",
        description="Sort direction.",
    ),
    limit: int = Query(default=50, ge=1, le=200, description="Maximum records to return."),
    offset: int = Query(default=0, ge=0, description="Number of records to skip."),
    _: None = Depends(require_database),
    current_user: dict = Depends(get_current_user),
):
    selected_read_state = read_state
    if unread_only and read_state == "all":
        selected_read_state = "unread"

    records = await notifications_repo.list_notifications(
        user_id=current_user.get("id"),
        read_state=selected_read_state,
        event_types=event_types,
        search=search.strip() if search else None,
        created_from=created_from,
        created_to=created_to,
        sort_by=sort_by,
        sort_direction=sort_direction,
        limit=limit,
        offset=offset,
    )
    return records


@router.post(
    "",
    response_model=NotificationResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a notification",
    response_description="The newly created notification resource.",
)
async def create_notification(
    payload: NotificationCreate,
    _: None = Depends(require_database),
    _current_user: dict = Depends(require_super_admin),
):
    """Create a notification entry for an individual or all users."""

    record = await notifications_repo.create_notification(
        event_type=payload.event_type,
        message=payload.message,
        user_id=payload.user_id,
        metadata=payload.metadata or {},
    )
    return record


@router.post(
    "/{notification_id}/read",
    response_model=NotificationResponse,
    summary="Mark a notification as read",
    response_description="The updated notification resource.",
)
async def mark_notification_read(
    notification_id: int,
    _: None = Depends(require_database),
    current_user: dict = Depends(get_current_user),
):
    record = await notifications_repo.get_notification(notification_id)
    if not record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Notification not found")
    if record.get("user_id") not in (None, current_user.get("id")):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not permitted to update this notification")
    updated = await notifications_repo.mark_read(notification_id)
    return updated


@router.post(
    "/acknowledge",
    response_model=list[NotificationResponse],
    summary="Mark multiple notifications as read",
    response_description="The updated notifications in the order they were acknowledged.",
)
async def acknowledge_notifications(
    payload: NotificationAcknowledgeRequest,
    _: None = Depends(require_database),
    current_user: dict = Depends(get_current_user),
):
    """Acknowledge a collection of notifications in a single request."""

    unique_ids: list[int] = []
    seen: set[int] = set()
    for identifier in payload.notification_ids:
        if identifier in seen:
            continue
        unique_ids.append(identifier)
        seen.add(identifier)

    if not unique_ids:
        return []

    for notification_id in unique_ids:
        record = await notifications_repo.get_notification(notification_id)
        if not record:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Notification not found")
        if record.get("user_id") not in (None, current_user.get("id")):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not permitted to update one or more notifications",
            )

    updated = await notifications_repo.mark_read_bulk(unique_ids)
    return updated


@router.get(
    "/preferences",
    response_model=list[NotificationPreferenceResponse],
    summary="List notification preferences",
    response_description="Notification delivery preferences for the authenticated user.",
)
async def list_notification_preferences(
    _: None = Depends(require_database),
    current_user: dict = Depends(get_current_user),
):
    try:
        user_id = int(current_user.get("id"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User session is invalid")

    stored_preferences = await preferences_repo.list_preferences(user_id)
    event_types = merge_event_types(
        DEFAULT_NOTIFICATION_EVENT_TYPES,
        [preference.get("event_type") for preference in stored_preferences],
        await notifications_repo.list_event_types(user_id=user_id),
    )

    mapped = {pref.get("event_type"): pref for pref in stored_preferences if pref.get("event_type")}
    results: list[NotificationPreferenceResponse] = []
    for event_type in event_types:
        pref = mapped.get(event_type)
        if pref:
            results.append(NotificationPreferenceResponse(**pref))
            continue
        results.append(
            NotificationPreferenceResponse(
                event_type=event_type,
                channel_in_app=True,
                channel_email=False,
                channel_sms=False,
            )
        )
    return results


@router.put(
    "/preferences",
    response_model=list[NotificationPreferenceResponse],
    summary="Update notification preferences",
    response_description="The persisted preferences after applying the update.",
)
async def update_notification_preferences(
    payload: NotificationPreferenceUpdateRequest,
    _: None = Depends(require_database),
    current_user: dict = Depends(get_current_user),
):
    try:
        user_id = int(current_user.get("id"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User session is invalid")

    updated = await preferences_repo.upsert_preferences(
        user_id,
        [preference.model_dump() for preference in payload.preferences],
    )
    mapped = {pref.get("event_type"): pref for pref in updated if pref.get("event_type")}
    event_types = merge_event_types(
        DEFAULT_NOTIFICATION_EVENT_TYPES,
        mapped.keys(),
        await notifications_repo.list_event_types(user_id=user_id),
    )
    results: list[NotificationPreferenceResponse] = []
    for event_type in event_types:
        pref = mapped.get(event_type)
        if pref:
            results.append(NotificationPreferenceResponse(**pref))
        else:
            results.append(
                NotificationPreferenceResponse(
                    event_type=event_type,
                    channel_in_app=True,
                    channel_email=False,
                    channel_sms=False,
                )
            )
    return results
