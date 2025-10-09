from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.dependencies.auth import get_current_user
from app.api.dependencies.database import require_database
from app.repositories import notifications as notifications_repo
from app.schemas.notifications import (
    NotificationAcknowledgeRequest,
    NotificationResponse,
)

router = APIRouter(prefix="/notifications", tags=["Notifications"])


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
