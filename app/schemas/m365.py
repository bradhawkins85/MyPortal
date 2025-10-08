from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class M365CredentialBase(BaseModel):
    tenant_id: str = Field(..., alias="tenantId")
    client_id: str = Field(..., alias="clientId")


class M365CredentialCreate(M365CredentialBase):
    client_secret: str = Field(..., alias="clientSecret")


class M365CredentialResponse(M365CredentialBase):
    token_expires_at: Optional[datetime] = Field(default=None, alias="tokenExpiresAt")

    class Config:
        populate_by_name = True

