from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, Field

_SLUG_PATTERN = r"^[a-z0-9](?:[a-z0-9._-]{0,118}[a-z0-9])?$"


class MessageTemplateBase(BaseModel):
    slug: str = Field(..., min_length=1, max_length=120, pattern=_SLUG_PATTERN)
    name: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = None
    content_type: Literal["text/plain", "text/html"] = Field(default="text/plain")
    content: str = Field(..., min_length=1)


class MessageTemplateCreate(MessageTemplateBase):
    pass


class MessageTemplateUpdate(BaseModel):
    slug: Optional[str] = Field(default=None, min_length=1, max_length=120, pattern=_SLUG_PATTERN)
    name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    description: Optional[str] = None
    content_type: Optional[Literal["text/plain", "text/html"]] = Field(default=None)
    content: Optional[str] = Field(default=None, min_length=1)


class MessageTemplateResponse(MessageTemplateBase):
    id: int
    created_at: Optional[datetime]
    updated_at: Optional[datetime]

    class Config:
        from_attributes = True
