from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class MembershipBase(BaseModel):
    role_id: int
    status: str = Field(default="active", pattern=r"^(invited|active|suspended)$")


class MembershipCreate(MembershipBase):
    user_id: int


class MembershipUpdate(BaseModel):
    role_id: Optional[int] = None
    status: Optional[str] = Field(default=None, pattern=r"^(invited|active|suspended)$")


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

    class Config:
        from_attributes = True
