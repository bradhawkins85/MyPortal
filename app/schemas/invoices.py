from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, Field


class InvoiceBase(BaseModel):
    company_id: int = Field(alias="companyId")
    invoice_number: str = Field(alias="invoiceNumber", min_length=1, max_length=255)
    amount: Decimal = Field(gt=-1_000_000_000, max_digits=12, decimal_places=2)
    due_date: date | None = Field(default=None, alias="dueDate")
    status: str | None = Field(default=None, max_length=255)
    approval_required: bool = Field(default=False, alias="approvalRequired")
    approved_by: int | None = Field(default=None, alias="approvedBy")
    approved_at: datetime | None = Field(default=None, alias="approvedAt")
    billing_adjustment_type: str | None = Field(
        default=None, alias="billingAdjustmentType", max_length=32
    )
    billing_adjustment_amount: Decimal | None = Field(
        default=None,
        alias="billingAdjustmentAmount",
        gt=-1_000_000_000,
        max_digits=12,
        decimal_places=2,
    )
    billing_adjustment_reason: str | None = Field(
        default=None, alias="billingAdjustmentReason", max_length=255
    )

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
    approval_required: bool | None = Field(default=None, alias="approvalRequired")
    approved_by: int | None = Field(default=None, alias="approvedBy")
    approved_at: datetime | None = Field(default=None, alias="approvedAt")
    billing_adjustment_type: str | None = Field(
        default=None, alias="billingAdjustmentType", max_length=32
    )
    billing_adjustment_amount: Decimal | None = Field(
        default=None,
        alias="billingAdjustmentAmount",
        gt=-1_000_000_000,
        max_digits=12,
        decimal_places=2,
    )
    billing_adjustment_reason: str | None = Field(
        default=None, alias="billingAdjustmentReason", max_length=255
    )
    xero_sync_error: str | None = Field(default=None, alias="xeroSyncError", max_length=500)
    xero_sync_attempted_at: datetime | None = Field(default=None, alias="xeroSyncAttemptedAt")

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
    created_at: datetime | None = Field(default=None, alias="createdAt")
    approval_required: bool = Field(default=False, alias="approvalRequired")
    approved_by: int | None = Field(default=None, alias="approvedBy")
    approved_at: datetime | None = Field(default=None, alias="approvedAt")
    billing_adjustment_type: str | None = Field(
        default=None, alias="billingAdjustmentType"
    )
    billing_adjustment_amount: Decimal | None = Field(
        default=None, alias="billingAdjustmentAmount"
    )
    billing_adjustment_reason: str | None = Field(
        default=None, alias="billingAdjustmentReason"
    )
    xero_invoice_id: str | None = Field(default=None, alias="xeroInvoiceId")
    xero_sync_error: str | None = Field(default=None, alias="xeroSyncError")
    xero_sync_attempted_at: datetime | None = Field(
        default=None, alias="xeroSyncAttemptedAt"
    )

    model_config = {"populate_by_name": True}
