from __future__ import annotations

from datetime import datetime
from datetime import datetime
from typing import Optional

from pydantic import AliasChoices, BaseModel, ConfigDict, EmailStr, Field, field_validator

from app.schemas.users import UserResponse


class RegistrationRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8)
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    mobile_phone: Optional[str] = None
    company_id: Optional[int] = None


class LoginRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    email: EmailStr
    password: str
    totp_code: Optional[str] = Field(
        default=None,
        validation_alias=AliasChoices("totp_code", "totpCode", "totp"),
        serialization_alias="totp_code",
        description=(
            "6-digit authenticator code. Accepts `totp_code`, `totpCode`, or `totp` keys "
            "and ignores internal whitespace."
        ),
    )

    @field_validator("totp_code")
    @classmethod
    def _normalise_totp_code(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None

        normalised = "".join(value.split())
        if not normalised:
            return None
        if not normalised.isdigit():
            raise ValueError("TOTP code must contain only digits")
        return normalised


class SessionInfo(BaseModel):
    id: int
    user_id: int
    created_at: datetime
    expires_at: datetime
    last_seen_at: datetime
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None
    csrf_token: str
    active_company_id: Optional[int] = None
    impersonator_user_id: Optional[int] = None
    impersonator_session_id: Optional[int] = None
    impersonation_started_at: Optional[datetime] = None


class LoginResponse(BaseModel):
    session: SessionInfo
    user: UserResponse


class SessionResponse(LoginResponse):
    pass


class ImpersonationRequest(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    user_id: int = Field(
        validation_alias=AliasChoices("user_id", "userId"),
        serialization_alias="user_id",
        description="Identifier of the user to impersonate.",
        ge=1,
    )


class PasswordResetRequest(BaseModel):
    email: EmailStr


class PasswordResetConfirm(BaseModel):
    token: str
    password: str = Field(min_length=12)


class PasswordResetStatus(BaseModel):
    detail: str


class PasswordChangeRequest(BaseModel):
    current_password: str = Field(min_length=1)
    new_password: str = Field(
        min_length=12,
        description="New password. Must differ from the current password and contain at least 12 characters.",
    )


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
