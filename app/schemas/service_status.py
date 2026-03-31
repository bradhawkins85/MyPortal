from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class AILookupMixin(BaseModel):
    ai_lookup_enabled: bool = False
    ai_lookup_url: str | None = None
    ai_lookup_prompt: str | None = None
    ai_lookup_model_override: str | None = None
    ai_lookup_frequency_operational: int = 60
    ai_lookup_frequency_degraded: int = 15
    ai_lookup_frequency_partial_outage: int = 10
    ai_lookup_frequency_outage: int = 5
    ai_lookup_frequency_maintenance: int = 60


class ServiceStatusBase(AILookupMixin):
    name: str
    description: str | None = None
    status: str = Field(default="operational")
    status_message: str | None = None
    display_order: int = 0
    is_active: bool = True
    company_ids: list[int] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)


class ServiceStatusCreate(ServiceStatusBase):
    pass


class ServiceStatusUpdate(BaseModel):
    name: str | None = None
    description: str | None = None
    status: str | None = None
    status_message: str | None = None
    display_order: int | None = None
    is_active: bool | None = None
    company_ids: list[int] | None = None
    tags: list[str] | None = None
    ai_lookup_enabled: bool | None = None
    ai_lookup_url: str | None = None
    ai_lookup_prompt: str | None = None
    ai_lookup_model_override: str | None = None
    ai_lookup_frequency_operational: int | None = None
    ai_lookup_frequency_degraded: int | None = None
    ai_lookup_frequency_partial_outage: int | None = None
    ai_lookup_frequency_outage: int | None = None
    ai_lookup_frequency_maintenance: int | None = None


class ServiceStatusResponse(AILookupMixin):
    id: int
    name: str
    description: str | None = None
    status: str
    status_message: str | None = None
    display_order: int
    is_active: bool
    company_ids: list[int] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    created_at: datetime | None = None
    updated_at: datetime | None = None
    updated_by: int | None = None
    ai_lookup_last_checked_at: datetime | None = None
    ai_lookup_last_status: str | None = None
    ai_lookup_last_message: str | None = None


class ServiceStatusUpdateStatusRequest(BaseModel):
    status: str
    status_message: str | None = None
