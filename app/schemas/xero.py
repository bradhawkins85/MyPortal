from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class XeroCallbackResponse(BaseModel):
    status: str


class XeroTenantConnection(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    tenant_id: str | None = Field(default=None, alias="tenantId", description="Xero tenant identifier.")
    tenant_name: str | None = Field(default=None, alias="tenantName", description="Display name of the Xero tenant.")
    tenant_type: str | None = Field(default=None, alias="tenantType", description="Xero tenant type, such as ORGANISATION.")
    created_date_utc: datetime | None = Field(
        default=None,
        alias="createdDateUtc",
        description="Tenant creation timestamp returned by Xero in UTC.",
    )


class XeroTenantListResponse(BaseModel):
    tenants: list[XeroTenantConnection]
    current_tenant_id: str | None = None
