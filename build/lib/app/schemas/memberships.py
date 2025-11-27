from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import AliasChoices, BaseModel, ConfigDict, Field


class MembershipBase(BaseModel):
    role_id: int = Field(validation_alias=AliasChoices("role_id", "roleId"))
    status: str = Field(default="active", pattern=r"^(invited|active|suspended)$")


class MembershipCreate(MembershipBase):
    user_id: int = Field(validation_alias=AliasChoices("user_id", "userId"))


class MembershipUpdate(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    role_id: Optional[int] = Field(
        default=None,
        validation_alias=AliasChoices("role_id", "roleId"),
    )
    status: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("status", "status"),
        pattern=r"^(invited|active|suspended)$",
    )


class MembershipUser(BaseModel):
    id: int
    email: str
    first_name: Optional[str] = None
    last_name: Optional[str] = None


class MembershipRole(BaseModel):
    id: int
    name: str
    permissions: list[str] = Field(default_factory=list)


class MembershipResponse(BaseModel):
    id: int
    company_id: int
    user_id: int
    role_id: int
    status: str
    invited_by: Optional[int] = None
    invited_at: Optional[datetime] = None
    joined_at: Optional[datetime] = None
    last_seen_at: Optional[datetime] = None
    user_email: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    role_name: Optional[str] = None
    permissions: list[str] = Field(default_factory=list)
    user_permissions: list[str] = Field(default_factory=list)
    combined_permissions: list[str] = Field(default_factory=list)

    class Config:
        from_attributes = True
