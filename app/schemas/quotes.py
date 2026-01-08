from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field


class QuoteSummaryResponse(BaseModel):
    quote_number: str = Field(alias="quoteNumber")
    company_id: int = Field(alias="companyId")
    user_id: int | None = Field(default=None, alias="userId")
    name: str | None = None
    status: str = Field(default="", max_length=50)
    notes: str | None = None
    po_number: str | None = Field(default=None, alias="poNumber", max_length=100)
    assigned_user_id: int | None = Field(default=None, alias="assignedUserId")
    created_at: datetime | None = Field(default=None, alias="createdAt")
    expires_at: datetime | None = Field(default=None, alias="expiresAt")

    model_config = {"populate_by_name": True}


class QuoteItemResponse(BaseModel):
    id: int | None = None
    company_id: int | None = Field(default=None, alias="companyId")
    user_id: int | None = Field(default=None, alias="userId")
    product_id: int | None = Field(default=None, alias="productId")
    product_name: str | None = Field(default=None, alias="productName")
    sku: str | None = None
    description: str | None = None
    image_url: str | None = Field(default=None, alias="imageUrl")
    quantity: int
    price: Decimal
    status: str = Field(default="", max_length=50)
    notes: str | None = None
    po_number: str | None = Field(default=None, alias="poNumber", max_length=100)
    quote_number: str = Field(alias="quoteNumber")
    created_at: datetime | None = Field(default=None, alias="createdAt")
    expires_at: datetime | None = Field(default=None, alias="expiresAt")
    stock: int | None = None
    stock_nsw: int | None = Field(default=None, alias="stockNsw")
    stock_qld: int | None = Field(default=None, alias="stockQld")
    stock_vic: int | None = Field(default=None, alias="stockVic")
    stock_sa: int | None = Field(default=None, alias="stockSa")

    model_config = {
        "populate_by_name": True,
        "json_encoders": {Decimal: lambda value: format(value, "f")},
    }


class QuoteDetailResponse(QuoteSummaryResponse):
    items: list[QuoteItemResponse]


class QuoteUpdateRequest(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    status: str | None = Field(default=None, min_length=1, max_length=50)
    notes: str | None = Field(default=None, max_length=2000)
    po_number: str | None = Field(default=None, alias="poNumber", max_length=100)
    assigned_user_id: int | None = Field(default=None, alias="assignedUserId")

    model_config = {"populate_by_name": True, "str_strip_whitespace": True}


class QuoteAssignRequest(BaseModel):
    assigned_user_id: int | None = Field(alias="assignedUserId")

    model_config = {"populate_by_name": True}
