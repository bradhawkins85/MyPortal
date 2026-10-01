from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, EmailStr, Field, model_validator


class SpamPurgeRequestCreate(BaseModel):
    sender: EmailStr | None = None
    subject: str | None = Field(default=None, max_length=500)
    received_from: date | None = None
    received_to: date | None = None
    content_match_query: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def validate_criteria(self):
        if self.received_from and self.received_to and self.received_from > self.received_to:
            raise ValueError("received_from must be on or before received_to")
        if not any((self.sender, (self.subject or "").strip(), (self.content_match_query or "").strip())):
            raise ValueError("Provide a sender, subject, or advanced content match query")
        return self


class SpamPurgeRequestResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    company_id: int
    created_by: int
    requested_by_email: str | None = None
    search_name: str
    action_name: str
    content_match_query: str
    sender: str | None = None
    subject: str | None = None
    received_from: date | None = None
    received_to: date | None = None
    search_status: str
    purge_status: str
    matched_items: int = 0
    matched_size: int = 0
    removed_items: int = 0
    search_details: dict[str, Any] | list[Any] | None = None
    purge_details: dict[str, Any] | list[Any] | None = None
    error_message: str | None = None
    search_started_at: datetime | None = None
    search_completed_at: datetime | None = None
    purge_started_at: datetime | None = None
    purge_completed_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
