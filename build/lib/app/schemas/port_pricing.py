from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal, Optional

from pydantic import BaseModel, Field

PricingStatus = Literal["draft", "pending_review", "approved", "rejected"]


class PortPricingBase(BaseModel):
    version_label: str = Field(..., max_length=100)
    currency: str = Field(default="USD", min_length=3, max_length=3)
    base_rate: Decimal = Field(default=Decimal("0"))
    handling_rate: Decimal = Field(default=Decimal("0"))
    storage_rate: Decimal = Field(default=Decimal("0"))
    notes: Optional[str] = None
    effective_from: Optional[date] = None
    effective_to: Optional[date] = None


class PortPricingCreate(PortPricingBase):
    status: PricingStatus = "draft"


class PortPricingResponse(PortPricingBase):
    id: int
    port_id: int
    status: PricingStatus
    submitted_by: Optional[int] = None
    approved_by: Optional[int] = None
    submitted_at: Optional[datetime] = None
    approved_at: Optional[datetime] = None
    rejection_reason: Optional[str] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class PricingStatusUpdate(BaseModel):
    status: PricingStatus
    rejection_reason: Optional[str] = None


class PricingReviewAction(BaseModel):
    rejection_reason: Optional[str] = Field(default=None, max_length=500)
