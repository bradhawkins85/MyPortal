from __future__ import annotations

from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator


def _dedupe_mailboxes(mailboxes: list[str]) -> list[str]:
    """Avoid duplicate Graph writes (addresses are case-insensitive) in submitted order."""
    seen: set[str] = set()
    unique: list[str] = []
    for mailbox in mailboxes:
        key = str(mailbox).casefold()
        if key not in seen:
            seen.add(key)
            unique.append(mailbox)
    return unique


class OutOfOfficeCreate(BaseModel):
    mailboxes: list[EmailStr] = Field(min_length=1, max_length=100)
    start_time: datetime
    end_time: datetime
    internal_message: str = Field(min_length=1, max_length=10000)
    external_message: str | None = Field(default=None, max_length=10000)
    same_message: bool = True
    external_audience: Literal["none", "contactsOnly", "all"] = "none"

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
                if self.external_audience != "none":
                    raise ValueError("External message is required when messages are different")
                # No external sender receives this reply, so keep the mailbox's
                # external message meaningful rather than blanking it.
                self.external_message = self.internal_message
        self.mailboxes = _dedupe_mailboxes(self.mailboxes)
        return self


class OutOfOfficeResult(BaseModel):
    mailbox: EmailStr
    success: bool
    error: str | None = None


class OutOfOfficeDisable(BaseModel):
    mailboxes: list[EmailStr] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def remove_duplicates(self):
        self.mailboxes = _dedupe_mailboxes(self.mailboxes)
        return self
