from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field


class OrderSummaryResponse(BaseModel):
    order_number: str = Field(alias="orderNumber")
    company_id: int = Field(alias="companyId")
    status: str = Field(default="", max_length=50)
    shipping_status: str = Field(default="", alias="shippingStatus", max_length=50)
    notes: str | None = None
    po_number: str | None = Field(default=None, alias="poNumber", max_length=50)
    consignment_id: str | None = Field(default=None, alias="consignmentId", max_length=100)
    order_date: datetime | None = Field(default=None, alias="orderDate")
    eta: datetime | None = None

    model_config = {"populate_by_name": True}


class OrderItemResponse(BaseModel):
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
    shipping_status: str = Field(default="", alias="shippingStatus", max_length=50)
    notes: str | None = None
    po_number: str | None = Field(default=None, alias="poNumber", max_length=50)
    consignment_id: str | None = Field(default=None, alias="consignmentId", max_length=100)
    order_number: str = Field(alias="orderNumber")
    order_date: datetime | None = Field(default=None, alias="orderDate")
    eta: datetime | None = None

    model_config = {
        "populate_by_name": True,
        "json_encoders": {Decimal: lambda value: format(value, "f")},
    }


class OrderDetailResponse(OrderSummaryResponse):
    items: list[OrderItemResponse]


class OrderUpdateRequest(BaseModel):
    status: str | None = Field(default=None, min_length=1, max_length=50)
    shipping_status: str | None = Field(default=None, alias="shippingStatus", min_length=1, max_length=50)
    notes: str | None = Field(default=None, max_length=2000)
    po_number: str | None = Field(default=None, alias="poNumber", max_length=50)
    consignment_id: str | None = Field(default=None, alias="consignmentId", max_length=100)
    eta: datetime | None = None

    model_config = {"populate_by_name": True, "str_strip_whitespace": True}
