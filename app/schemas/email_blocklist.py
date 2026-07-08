from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class EmailBlocklistCreate(BaseModel):
    email: str = Field(..., min_length=3, max_length=320, description="Email address that must not receive portal email.")
    reason: str | None = Field(default=None, max_length=2000)


class EmailBlocklistEntry(BaseModel):
    id: int
    email: str
    reason: str | None = None
    source: Literal["manual", "smtp2go_webhook"] | str
    last_event_type: str | None = None
    created_by_user_id: int | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class EmailBlocklistListResponse(BaseModel):
    items: list[EmailBlocklistEntry]
    total: int
