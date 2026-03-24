from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class M365MailAccountBase(BaseModel):
    name: str = Field(..., max_length=255)
    company_id: int
    user_principal_name: str = Field(..., max_length=255)
    mailbox_type: str = Field("user", max_length=32)
    folder: str = Field("Inbox", max_length=255)
    schedule_cron: str = Field(..., max_length=100)
    filter_query: dict[str, Any] | None = None
    process_unread_only: bool = True
    mark_as_read: bool = True
    sync_known_only: bool = False
    active: bool = True
    priority: int = Field(100, ge=0, le=32767)


class M365MailAccountCreate(M365MailAccountBase):
    pass


class M365MailAccountUpdate(BaseModel):
    name: str | None = Field(default=None, max_length=255)
    company_id: int | None = None
    user_principal_name: str | None = Field(default=None, max_length=255)
    mailbox_type: str | None = Field(default=None, max_length=32)
    folder: str | None = Field(default=None, max_length=255)
    schedule_cron: str | None = Field(default=None, max_length=100)
    filter_query: dict[str, Any] | None = None
    process_unread_only: bool | None = None
    mark_as_read: bool | None = None
    sync_known_only: bool | None = None
    active: bool | None = None
    priority: int | None = Field(default=None, ge=0, le=32767)


class M365MailAccountResponse(BaseModel):
    id: int
    name: str
    company_id: int
    user_principal_name: str
    mailbox_type: str
    folder: str
    schedule_cron: str
    filter_query: dict[str, Any] | None
    process_unread_only: bool
    mark_as_read: bool
    sync_known_only: bool
    active: bool
    priority: int
    last_synced_at: datetime | None
    scheduled_task_id: int | None

    model_config = {
        "from_attributes": True,
    }


class M365MailSyncResponse(BaseModel):
    status: str
    processed: int | None = None
    errors: list[dict[str, object]] | None = None
    reason: str | None = None
    error: str | None = None
