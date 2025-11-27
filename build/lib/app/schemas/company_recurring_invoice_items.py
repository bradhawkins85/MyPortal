from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class RecurringInvoiceItemBase(BaseModel):
    product_code: str = Field(..., description="Product code from Xero")
    description_template: str = Field(..., description="Description template with variable support")
    qty_expression: str = Field(..., description="Quantity expression (static number or variable)")
    price_override: Optional[float] = Field(None, description="Price override, null to use Xero default")
    active: bool = Field(True, description="Whether this item is active")


class RecurringInvoiceItemCreate(RecurringInvoiceItemBase):
    pass


class RecurringInvoiceItemUpdate(BaseModel):
    product_code: Optional[str] = None
    description_template: Optional[str] = None
    qty_expression: Optional[str] = None
    price_override: Optional[float] = None
    active: Optional[bool] = None


class RecurringInvoiceItemResponse(RecurringInvoiceItemBase):
    id: int
    company_id: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
