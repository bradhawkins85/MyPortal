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
