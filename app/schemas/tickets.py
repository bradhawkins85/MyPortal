from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


class TicketBase(BaseModel):
    subject: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = None
    status: str = Field(default="open", max_length=32)
    priority: str = Field(default="normal", max_length=32)
    category: Optional[str] = Field(default=None, max_length=64)
    module_slug: Optional[str] = Field(default=None, max_length=64)
    company_id: Optional[int] = None
    assigned_user_id: Optional[int] = None
    external_reference: Optional[str] = Field(default=None, max_length=128)


class TicketCreate(TicketBase):
    requester_id: Optional[int] = None


class TicketUpdate(BaseModel):
    subject: Optional[str] = Field(default=None, min_length=1, max_length=255)
    description: Optional[str] = None
    status: Optional[str] = Field(default=None, max_length=32)
    priority: Optional[str] = Field(default=None, max_length=32)
    category: Optional[str] = Field(default=None, max_length=64)
    module_slug: Optional[str] = Field(default=None, max_length=64)
    company_id: Optional[int] = None
    assigned_user_id: Optional[int] = None
    external_reference: Optional[str] = Field(default=None, max_length=128)


class TicketReplyCreate(BaseModel):
    body: str = Field(..., min_length=1)
    is_internal: bool = False


class TicketReply(BaseModel):
    id: int
    ticket_id: int
    author_id: Optional[int]
    body: str
    is_internal: bool
    created_at: datetime

    class Config:
        from_attributes = True


class TicketWatcher(BaseModel):
    id: int
    ticket_id: int
    user_id: int
    created_at: datetime

    class Config:
        from_attributes = True


class TicketResponse(TicketBase):
    id: int
    requester_id: Optional[int]
    created_at: datetime
    updated_at: datetime
    closed_at: Optional[datetime]
    ai_summary: Optional[str] = None
    ai_summary_status: Optional[str] = None
    ai_summary_model: Optional[str] = None
    ai_resolution_state: Optional[str] = None
    ai_summary_updated_at: Optional[datetime] = None
    ai_tags: Optional[list[str]] = None
    ai_tags_status: Optional[str] = None
    ai_tags_model: Optional[str] = None
    ai_tags_updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class TicketDetail(TicketResponse):
    replies: list[TicketReply] = Field(default_factory=list)
    watchers: list[TicketWatcher] = Field(default_factory=list)


class TicketListResponse(BaseModel):
    items: list[TicketResponse]
    total: int


class TicketReplyResponse(BaseModel):
    ticket: TicketResponse
    reply: TicketReply


class TicketSearchFilters(BaseModel):
    status: Optional[str] = None
    module_slug: Optional[str] = None
    company_id: Optional[int] = None
    assigned_user_id: Optional[int] = None
    search: Optional[str] = None
    limit: int = Field(default=100, ge=1, le=500)
    offset: int = Field(default=0, ge=0)


class TicketWatcherUpdate(BaseModel):
    user_ids: list[int] = Field(default_factory=list)
