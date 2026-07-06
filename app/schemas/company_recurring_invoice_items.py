from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel, Field, field_validator, model_validator


BILLING_FREQUENCIES = {"every_run", "once", "weekly", "monthly", "quarterly", "yearly", "times_per_year"}


class RecurringInvoiceItemBase(BaseModel):
    product_code: str = Field(..., description="Product code from Xero")
    description_template: str = Field(..., description="Description template with variable support")
    qty_expression: str = Field(..., description="Quantity expression (static number or variable)")
    price_override: Optional[float] = Field(None, description="Price override, null to use Xero default")
    active: bool = Field(True, description="Whether this item is active")
    billing_frequency: str = Field(
        "every_run",
        description="Billing limit for automatic invoice generation: every_run, once, weekly, monthly, quarterly, yearly, or times_per_year",
    )
    billing_interval: Optional[int] = Field(
        None,
        ge=1,
        le=365,
        description="Frequency value used by times_per_year, for example 12 for twelve times per year",
    )
    start_date: Optional[date] = Field(None, description="Optional first UTC date this item may be billed")
    end_date: Optional[date] = Field(None, description="Optional final UTC date this item may be billed")

    @field_validator("billing_frequency")
    @classmethod
    def validate_billing_frequency(cls, value: str) -> str:
        normalized = (value or "every_run").strip().lower()
        if normalized not in BILLING_FREQUENCIES:
            raise ValueError("Unsupported billing frequency")
        return normalized

    @model_validator(mode="after")
    def validate_schedule(self):
        if self.billing_frequency == "times_per_year" and not self.billing_interval:
            raise ValueError("billing_interval is required when billing_frequency is times_per_year")
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValueError("end_date must be on or after start_date")
        return self


class RecurringInvoiceItemCreate(RecurringInvoiceItemBase):
    pass


class RecurringInvoiceItemUpdate(BaseModel):
    product_code: Optional[str] = None
    description_template: Optional[str] = None
    qty_expression: Optional[str] = None
    price_override: Optional[float] = None
    active: Optional[bool] = None
    billing_frequency: Optional[str] = None
    billing_interval: Optional[int] = Field(None, ge=1, le=365)
    start_date: Optional[date] = None
    end_date: Optional[date] = None

    @field_validator("billing_frequency")
    @classmethod
    def validate_billing_frequency(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        normalized = value.strip().lower()
        if normalized not in BILLING_FREQUENCIES:
            raise ValueError("Unsupported billing frequency")
        return normalized

    @model_validator(mode="after")
    def validate_schedule(self):
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValueError("end_date must be on or after start_date")
        return self


class RecurringInvoiceItemResponse(RecurringInvoiceItemBase):
    id: int
    company_id: int
    last_billed_at: Optional[datetime] = None
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True
