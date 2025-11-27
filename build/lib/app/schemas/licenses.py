from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class LicenseBase(BaseModel):
    company_id: int = Field(..., ge=1)
    name: str
    platform: str
    count: int = Field(..., ge=0)
    expiry_date: Optional[datetime] = None
    contract_term: Optional[str] = None


class LicenseCreate(LicenseBase):
    pass


class LicenseUpdate(BaseModel):
    name: Optional[str] = None
    platform: Optional[str] = None
    count: Optional[int] = Field(default=None, ge=0)
    expiry_date: Optional[datetime] = None
    contract_term: Optional[str] = None


class LicenseResponse(LicenseBase):
    id: int
    allocated: int = 0
    display_name: Optional[str] = None

    class Config:
        from_attributes = True


class LicenseStaffResponse(BaseModel):
    id: int
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    email: Optional[str] = None

