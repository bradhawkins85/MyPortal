from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, model_validator


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
    minutes_spent: Optional[int] = Field(default=None, ge=0)
    is_billable: bool = False


class TicketReplyTimeUpdate(BaseModel):
    minutes_spent: Optional[int] = Field(
        default=None,
        ge=0,
        le=1440,
        validation_alias=AliasChoices("minutes_spent", "minutesSpent"),
    )
    is_billable: Optional[bool] = Field(
        default=None,
        validation_alias=AliasChoices("is_billable", "isBillable"),
    )

    model_config = ConfigDict(populate_by_name=True)


class TicketReply(BaseModel):
    id: int
    ticket_id: int
    author_id: Optional[int]
    body: str
    is_internal: bool
    minutes_spent: Optional[int] = Field(default=None, ge=0)
    is_billable: bool = False
    created_at: datetime
    time_summary: Optional[str] = None

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


class SyncroTicketImportMode(str, Enum):
    SINGLE = "single"
    RANGE = "range"
    ALL = "all"


class SyncroTicketImportRequest(BaseModel):
    mode: SyncroTicketImportMode
    ticket_id: Optional[int] = Field(
        default=None,
        alias="ticketId",
        validation_alias=AliasChoices("ticketId", "ticket_id"),
        ge=1,
    )
    start_id: Optional[int] = Field(
        default=None,
        alias="startId",
        validation_alias=AliasChoices("startId", "start_id"),
        ge=1,
    )
    end_id: Optional[int] = Field(
        default=None,
        alias="endId",
        validation_alias=AliasChoices("endId", "end_id"),
        ge=1,
    )

    model_config = ConfigDict(populate_by_name=True)

    @model_validator(mode="after")
    def _validate_mode(self) -> "SyncroTicketImportRequest":
        if self.mode is SyncroTicketImportMode.SINGLE:
            if self.ticket_id is None:
                raise ValueError("ticketId is required when mode is 'single'")
        elif self.mode is SyncroTicketImportMode.RANGE:
            if self.start_id is None or self.end_id is None:
                raise ValueError("startId and endId are required when mode is 'range'")
            if self.end_id < self.start_id:
                raise ValueError("endId must be greater than or equal to startId")
        return self


class SyncroTicketImportSummary(BaseModel):
    mode: SyncroTicketImportMode
    fetched: int = Field(default=0, ge=0)
    created: int = Field(default=0, ge=0)
    updated: int = Field(default=0, ge=0)
    skipped: int = Field(default=0, ge=0)
