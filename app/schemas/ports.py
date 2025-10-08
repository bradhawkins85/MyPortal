from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Optional

from pydantic import BaseModel, Field


class PortBase(BaseModel):
    name: str = Field(..., max_length=255)
    code: str = Field(..., max_length=20)
    country: str = Field(..., max_length=100)
    region: Optional[str] = Field(default=None, max_length=100)
    timezone: str = Field(default="UTC", max_length=64)
    description: Optional[str] = None
    latitude: Optional[Decimal] = Field(default=None)
    longitude: Optional[Decimal] = Field(default=None)
    is_active: bool = True


class PortCreate(PortBase):
    pass


class PortUpdate(BaseModel):
    name: Optional[str] = Field(default=None, max_length=255)
    code: Optional[str] = Field(default=None, max_length=20)
    country: Optional[str] = Field(default=None, max_length=100)
    region: Optional[str] = Field(default=None, max_length=100)
    timezone: Optional[str] = Field(default=None, max_length=64)
    description: Optional[str] = None
    latitude: Optional[Decimal] = None
    longitude: Optional[Decimal] = None
    is_active: Optional[bool] = None


class PortResponse(PortBase):
    id: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
