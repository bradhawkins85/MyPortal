from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field, field_validator

from app.services.company_domains import EmailDomainError, normalise_email_domains


class CompanyBase(BaseModel):
    name: str
    address: Optional[str] = None
    is_vip: Optional[int] = None
    syncro_company_id: Optional[str] = None
    tacticalrmm_client_id: Optional[str] = None
    xero_id: Optional[str] = None
    email_domains: list[str] = Field(default_factory=list)

    @field_validator("email_domains", mode="after")
    @classmethod
    def _validate_email_domains(cls, value: list[str]) -> list[str]:
        try:
            return normalise_email_domains(value)
        except EmailDomainError as exc:
            raise ValueError(str(exc)) from exc


class CompanyCreate(CompanyBase):
    pass


class CompanyUpdate(BaseModel):
    name: Optional[str] = None
    address: Optional[str] = None
    is_vip: Optional[int] = None
    syncro_company_id: Optional[str] = None
    tacticalrmm_client_id: Optional[str] = None
    xero_id: Optional[str] = None
    email_domains: Optional[list[str]] = None


class CompanyResponse(CompanyBase):
    id: int

    class Config:
        from_attributes = True
