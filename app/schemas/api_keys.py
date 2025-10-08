from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel, Field


class ApiKeyUsageEntry(BaseModel):
    ip_address: str = Field(..., max_length=45)
    usage_count: int = Field(..., ge=0)
    last_used_at: Optional[datetime]


class ApiKeyCreateRequest(BaseModel):
    description: Optional[str] = Field(default=None, max_length=255)
    expiry_date: Optional[date]


class ApiKeyRotateRequest(BaseModel):
    description: Optional[str] = Field(default=None, max_length=255)
    expiry_date: Optional[date]
    retire_previous: bool = True


class ApiKeyResponse(BaseModel):
    id: int
    description: Optional[str]
    expiry_date: Optional[date]
    created_at: datetime
    last_used_at: Optional[datetime]
    last_seen_at: Optional[datetime]
    usage_count: int = 0
    key_preview: str = Field(..., max_length=64)
    usage: list[ApiKeyUsageEntry] = Field(default_factory=list)


class ApiKeyDetailResponse(ApiKeyResponse):
    pass


class ApiKeyCreateResponse(ApiKeyResponse):
    api_key: str = Field(..., min_length=32)
