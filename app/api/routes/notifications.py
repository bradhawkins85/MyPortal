from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.dependencies.auth import get_current_user
from app.api.dependencies.database import require_database
from app.repositories import notifications as notifications_repo
from app.schemas.notifications import NotificationResponse

router = APIRouter(prefix="/notifications", tags=["Notifications"])


@router.get("", response_model=list[NotificationResponse])
async def list_notifications(
    unread_only: bool = Query(default=False),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    _: None = Depends(require_database),
    current_user: dict = Depends(get_current_user),
):
    records = await notifications_repo.list_notifications(
        user_id=current_user.get("id"),
        unread_only=unread_only,
        limit=limit,
        offset=offset,
    )
    return records


@router.post("/{notification_id}/read", response_model=NotificationResponse)
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
