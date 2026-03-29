from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

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
    onboarding_status: Optional[str] = Field(default=None, validation_alias="onboardingStatus")
    onboarding_complete: Optional[bool] = Field(default=None, validation_alias="onboardingComplete")
    onboarding_completed_at: Optional[datetime] = Field(
        default=None, validation_alias="onboardingCompletedAt"
    )
    approval_status: Optional[str] = Field(default=None, validation_alias="approvalStatus")
    request_notes: Optional[str] = Field(default=None, validation_alias="requestNotes")
    approval_notes: Optional[str] = Field(default=None, validation_alias="approvalNotes")


class StaffCreate(StaffBase):
    company_id: int = Field(validation_alias="companyId")
    custom_fields: dict[str, Any] | None = Field(default=None, validation_alias="customFields")


class StaffRequestCreate(StaffBase):
    custom_fields: dict[str, Any] | None = Field(default=None, validation_alias="customFields")


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
    onboarding_status: Optional[str] = Field(default=None, validation_alias="onboardingStatus")
    onboarding_complete: Optional[bool] = Field(default=None, validation_alias="onboardingComplete")
    onboarding_completed_at: Optional[datetime] = Field(
        default=None, validation_alias="onboardingCompletedAt"
    )
    approval_status: Optional[str] = Field(default=None, validation_alias="approvalStatus")
    request_notes: Optional[str] = Field(default=None, validation_alias="requestNotes")
    approval_notes: Optional[str] = Field(default=None, validation_alias="approvalNotes")
    custom_fields: dict[str, Any] | None = Field(default=None, validation_alias="customFields")


class StaffWorkflowStatus(BaseModel):
    state: Optional[str] = None
    current_step: Optional[str] = None
    retries_used: Optional[int] = None
    last_error: Optional[str] = None
    helpdesk_ticket_id: Optional[int] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    requested_at: Optional[datetime] = None


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
    is_ex_staff: bool = False
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
    onboarding_status: Optional[str] = None
    onboarding_complete: bool = False
    onboarding_completed_at: Optional[datetime] = None
    approval_status: str = "pending"
    requested_by_user_id: Optional[int] = None
    requested_at: Optional[datetime] = None
    approved_by_user_id: Optional[int] = None
    approved_at: Optional[datetime] = None
    request_notes: Optional[str] = None
    approval_notes: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    custom_fields: dict[str, Any] = Field(default_factory=dict)
    workflow_status: Optional[StaffWorkflowStatus] = None

    model_config = {
        "from_attributes": True,
        "populate_by_name": True,
    }
