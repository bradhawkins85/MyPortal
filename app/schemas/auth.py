from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, EmailStr, Field

from app.schemas.users import UserResponse


class RegistrationRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    mobile_phone: Optional[str] = None
    company_id: Optional[int] = None


class LoginRequest(BaseModel):
    email: EmailStr
    password: str
    totp_code: Optional[str] = None


class SessionInfo(BaseModel):
    id: int
    user_id: int
    created_at: datetime
    expires_at: datetime
    last_seen_at: datetime
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None
    csrf_token: str


class LoginResponse(BaseModel):
    session: SessionInfo
    user: UserResponse


class SessionResponse(LoginResponse):
    pass


class PasswordResetRequest(BaseModel):
    email: EmailStr


class PasswordResetConfirm(BaseModel):
    token: str
    password: str = Field(min_length=12)


class PasswordResetStatus(BaseModel):
    detail: str


class TOTPSetupResponse(BaseModel):
    secret: str
    otpauth_url: str


class TOTPVerifyRequest(BaseModel):
    code: str
    name: Optional[str] = None


class TOTPAuthenticator(BaseModel):
    id: int
    name: str


class TOTPListResponse(BaseModel):
    items: list[TOTPAuthenticator]
