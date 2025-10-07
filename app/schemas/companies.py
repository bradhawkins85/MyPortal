from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class CompanyBase(BaseModel):
    name: str
    address: Optional[str] = None
    is_vip: Optional[int] = None
    syncro_company_id: Optional[str] = None
    xero_id: Optional[str] = None


class CompanyCreate(CompanyBase):
    pass


class CompanyUpdate(BaseModel):
    name: Optional[str] = None
    address: Optional[str] = None
    is_vip: Optional[int] = None
    syncro_company_id: Optional[str] = None
    xero_id: Optional[str] = None


class CompanyResponse(CompanyBase):
    id: int

    class Config:
        from_attributes = True
