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
    labour_type_id: Optional[int] = Field(default=None, alias="labourTypeId", ge=1)

    model_config = ConfigDict(populate_by_name=True)


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
    labour_type_id: Optional[int] = Field(
        default=None,
        ge=1,
        validation_alias=AliasChoices("labour_type_id", "labourTypeId"),
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
    labour_type_id: Optional[int] = None
    labour_type_name: Optional[str] = None
    labour_type_code: Optional[str] = None

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


class TicketDashboardRow(BaseModel):
    id: int
    subject: str
    status: str
    priority: str
    company_id: Optional[int] = None
    company_name: Optional[str] = None
    assigned_user_id: Optional[int] = None
    assigned_user_email: Optional[str] = None
    module_slug: Optional[str] = None
    requester_id: Optional[int] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class TicketReplyResponse(BaseModel):
    ticket: TicketResponse
    reply: TicketReply


class LabourTypeModel(BaseModel):
    id: int
    code: str
    name: str
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class LabourTypeListResponse(BaseModel):
    labour_types: list[LabourTypeModel] = Field(default_factory=list)


class LabourTypeUpdateRequest(BaseModel):
    code: Optional[str] = Field(default=None, min_length=1, max_length=64)
    name: Optional[str] = Field(default=None, min_length=1, max_length=128)


class LabourTypeCreateRequest(BaseModel):
    code: str = Field(..., min_length=1, max_length=64)
    name: str = Field(..., min_length=1, max_length=128)


class TicketSearchFilters(BaseModel):
    status: Optional[str] = None
    module_slug: Optional[str] = None
    company_id: Optional[int] = None
    assigned_user_id: Optional[int] = None
    search: Optional[str] = None
    limit: int = Field(default=100, ge=1, le=500)
    offset: int = Field(default=0, ge=0)


class TicketDashboardResponse(BaseModel):
    items: list[TicketDashboardRow]
    total: int
    status_counts: dict[str, int]
    filters: TicketSearchFilters


class TicketStatusDefinitionModel(BaseModel):
    tech_status: str = Field(alias="techStatus")
    tech_label: str = Field(alias="techLabel")
    public_status: str = Field(alias="publicStatus")

    model_config = ConfigDict(populate_by_name=True)


class TicketStatusUpdateInput(BaseModel):
    tech_label: str = Field(..., alias="techLabel", min_length=1, max_length=128)
    public_status: str = Field(..., alias="publicStatus", min_length=1, max_length=128)
    existing_slug: str | None = Field(default=None, alias="existingSlug", max_length=64)
    tech_status: str | None = Field(default=None, alias="techStatus", max_length=64)

    model_config = ConfigDict(populate_by_name=True)


class TicketStatusUpdateRequest(BaseModel):
    statuses: list[TicketStatusUpdateInput] = Field(default_factory=list)

    model_config = ConfigDict(populate_by_name=True)


class TicketStatusListResponse(BaseModel):
    statuses: list[TicketStatusDefinitionModel] = Field(default_factory=list)


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


class TicketTaskCreate(BaseModel):
    task_name: str = Field(..., min_length=1, max_length=255, alias="taskName")
    sort_order: int = Field(default=0, alias="sortOrder")

    model_config = ConfigDict(populate_by_name=True)


class TicketTaskUpdate(BaseModel):
    task_name: Optional[str] = Field(default=None, min_length=1, max_length=255, alias="taskName")
    is_completed: Optional[bool] = Field(default=None, alias="isCompleted")
    sort_order: Optional[int] = Field(default=None, alias="sortOrder")

    model_config = ConfigDict(populate_by_name=True)


class TicketTask(BaseModel):
    id: int
    ticket_id: int
    task_name: str
    is_completed: bool
    completed_at: Optional[datetime] = None
    completed_by: Optional[int] = None
    sort_order: int
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class TicketTaskListResponse(BaseModel):
    items: list[TicketTask] = Field(default_factory=list)
