from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator


class OutOfOfficeCreate(BaseModel):
    mailboxes: list[EmailStr] = Field(min_length=1, max_length=100)
    start_time: datetime
    end_time: datetime
    internal_message: str = Field(min_length=1, max_length=10000)
    external_message: str | None = Field(default=None, max_length=10000)
    same_message: bool = True

    @field_validator("start_time", "end_time")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Start and end times must include a timezone")
        return value.astimezone(timezone.utc)

    @model_validator(mode="after")
    def validate_schedule(self):
        if self.end_time <= self.start_time:
            raise ValueError("End time must be after start time")
        self.internal_message = self.internal_message.strip()
        if not self.internal_message:
            raise ValueError("Internal message is required")
        if self.same_message:
            self.external_message = self.internal_message
        else:
            self.external_message = (self.external_message or "").strip()
            if not self.external_message:
                raise ValueError("External message is required when messages are different")
        # Avoid duplicate Graph writes while preserving the submitted order.
        self.mailboxes = list(dict.fromkeys(self.mailboxes))
        return self


class OutOfOfficeResult(BaseModel):
    mailbox: EmailStr
    success: bool
    error: str | None = None
