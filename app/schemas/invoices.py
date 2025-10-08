from __future__ import annotations

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, Field


class InvoiceBase(BaseModel):
    company_id: int = Field(alias="companyId")
    invoice_number: str = Field(alias="invoiceNumber", min_length=1, max_length=255)
    amount: Decimal = Field(gt=-1_000_000_000, max_digits=12, decimal_places=2)
    due_date: date | None = Field(default=None, alias="dueDate")
    status: str | None = Field(default=None, max_length=255)

    model_config = {
        "populate_by_name": True,
        "str_strip_whitespace": True,
    }


class InvoiceCreate(InvoiceBase):
    pass


class InvoiceUpdate(BaseModel):
    company_id: int | None = Field(default=None, alias="companyId")
    invoice_number: str | None = Field(default=None, alias="invoiceNumber", min_length=1, max_length=255)
    amount: Decimal | None = Field(default=None, gt=-1_000_000_000, max_digits=12, decimal_places=2)
    due_date: date | None = Field(default=None, alias="dueDate")
    status: str | None = Field(default=None, max_length=255)

    model_config = {
        "populate_by_name": True,
        "str_strip_whitespace": True,
    }


class InvoiceResponse(BaseModel):
    id: int
    company_id: int = Field(alias="companyId")
    invoice_number: str = Field(alias="invoiceNumber")
    amount: Decimal
    due_date: date | None = Field(default=None, alias="dueDate")
    status: str | None

    model_config = {"populate_by_name": True}
