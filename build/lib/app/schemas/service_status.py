from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class ServiceStatusBase(BaseModel):
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


class ServiceStatusResponse(BaseModel):
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


class ServiceStatusUpdateStatusRequest(BaseModel):
    status: str
    status_message: str | None = None
