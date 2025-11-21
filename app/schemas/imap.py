from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field, SecretStr


class IMAPAccountBase(BaseModel):
    name: str = Field(..., max_length=255)
    host: str = Field(..., max_length=255)
    port: int = Field(993, ge=1, le=65535)
    username: str = Field(..., max_length=255)
    folder: str = Field("INBOX", max_length=255)
    schedule_cron: str = Field(..., max_length=100)
    filter_query: dict[str, Any] | None = None
    process_unread_only: bool = True
    mark_as_read: bool = True
    sync_known_only: bool = False
    active: bool = True
    company_id: int | None = None
    priority: int = Field(100, ge=0, le=32767)


class IMAPAccountCreate(IMAPAccountBase):
    password: SecretStr


class IMAPAccountUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=255)
    host: str | None = Field(default=None, max_length=255)
    port: int | None = Field(default=None, ge=1, le=65535)
    username: str | None = Field(default=None, max_length=255)
    password: SecretStr | None = None
    folder: str | None = Field(default=None, max_length=255)
    schedule_cron: str | None = Field(default=None, max_length=100)
    filter_query: dict[str, Any] | None = None
    process_unread_only: bool | None = None
    mark_as_read: bool | None = None
    sync_known_only: bool | None = None
    active: bool | None = None
    company_id: int | None = None
    priority: int | None = Field(default=None, ge=0, le=32767)


class IMAPAccountResponse(BaseModel):
    id: int
    name: str
    host: str
    port: int
    username: str
    folder: str
    schedule_cron: str
    filter_query: dict[str, Any] | None
    process_unread_only: bool
    mark_as_read: bool
    sync_known_only: bool
    active: bool
    company_id: int | None
    priority: int
    last_synced_at: datetime | None
    scheduled_task_id: int | None

    model_config = {
        "from_attributes": True,
    }


class IMAPSyncResponse(BaseModel):
    status: str
    processed: int | None = None
    errors: list[dict[str, object]] | None = None
    reason: str | None = None
    error: str | None = None
