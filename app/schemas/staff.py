from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, EmailStr, Field


class StaffBase(BaseModel):
    first_name: str = Field(validation_alias="firstName")
    last_name: str = Field(validation_alias="lastName")
    email: EmailStr
    mobile_phone: Optional[str] = Field(default=None, validation_alias="mobilePhone")
    date_onboarded: Optional[datetime] = Field(default=None, validation_alias="dateOnboarded")
    date_offboarded: Optional[datetime] = Field(default=None, validation_alias="dateOffboarded")
    enabled: bool = True
    street: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    postcode: Optional[str] = None
    country: Optional[str] = None
    department: Optional[str] = None
    job_title: Optional[str] = Field(default=None, validation_alias="jobTitle")
    org_company: Optional[str] = Field(default=None, validation_alias="company")
    manager_name: Optional[str] = Field(default=None, validation_alias="managerName")
    account_action: Optional[str] = Field(default=None, validation_alias="accountAction")
    syncro_contact_id: Optional[str] = Field(
        default=None, validation_alias="syncroContactId"
    )


class StaffCreate(StaffBase):
    company_id: int = Field(validation_alias="companyId")


class StaffUpdate(BaseModel):
    first_name: Optional[str] = Field(default=None, validation_alias="firstName")
    last_name: Optional[str] = Field(default=None, validation_alias="lastName")
    email: Optional[EmailStr] = None
    mobile_phone: Optional[str] = Field(default=None, validation_alias="mobilePhone")
    date_onboarded: Optional[datetime] = Field(default=None, validation_alias="dateOnboarded")
    date_offboarded: Optional[datetime] = Field(default=None, validation_alias="dateOffboarded")
    enabled: Optional[bool] = None
    street: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    postcode: Optional[str] = None
    country: Optional[str] = None
    department: Optional[str] = None
    job_title: Optional[str] = Field(default=None, validation_alias="jobTitle")
    org_company: Optional[str] = Field(default=None, validation_alias="company")
    manager_name: Optional[str] = Field(default=None, validation_alias="managerName")
    account_action: Optional[str] = Field(default=None, validation_alias="accountAction")
    syncro_contact_id: Optional[str] = Field(
        default=None, validation_alias="syncroContactId"
    )


class StaffResponse(BaseModel):
    id: int
    company_id: int
    first_name: str
    last_name: str
    email: str
    mobile_phone: Optional[str] = None
    date_onboarded: Optional[datetime] = None
    date_offboarded: Optional[datetime] = None
    enabled: bool
    street: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    postcode: Optional[str] = None
    country: Optional[str] = None
    department: Optional[str] = None
    job_title: Optional[str] = None
    org_company: Optional[str] = None
    manager_name: Optional[str] = None
    account_action: Optional[str] = None
    verification_code: Optional[str] = None
    verification_admin_name: Optional[str] = None
    syncro_contact_id: Optional[str] = None

    model_config = {
        "from_attributes": True,
        "populate_by_name": True,
    }
