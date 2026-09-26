from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr


class CredentialCreate(BaseModel):
    company_id: int = Field(gt=0)
    name: str = Field(min_length=1, max_length=191)
    username: str | None = Field(default=None, max_length=191)
    credential_class: Literal["onboarding", "shared", "admin", "service", "other"] = (
        "other"
    )
    owner: str | None = Field(default=None, max_length=191)
    intended_recipient: str | None = Field(default=None, max_length=191)
    expires_on: date | None = None
    review_on: date | None = None
    secret: SecretStr = Field(min_length=1, max_length=65535)
    links: list[tuple[Literal["asset", "staff", "ticket", "process_run"], int]] = Field(
        default_factory=list
    )


class CredentialMetadata(BaseModel):
    id: int
    company_id: int
    name: str
    username: str | None = None
    credential_class: str = "other"
    owner: str | None = None
    intended_recipient: str | None = None
    expires_on: date | str | None = None
    review_on: date | str | None = None
    last_rotated_at: datetime | str | None = None
    archived_at: datetime | str | None = None
    revoked_at: datetime | str | None = None
    current_version: int
    created_at: datetime | str | None = None
    updated_at: datetime | str | None = None
    model_config = ConfigDict(extra="ignore")


class SecretReplace(BaseModel):
    secret: SecretStr = Field(min_length=1, max_length=65535)


class CredentialUpdate(BaseModel):
    name: str = Field(min_length=1, max_length=191)
    username: str | None = Field(default=None, max_length=191)
    credential_class: Literal["onboarding", "shared", "admin", "service", "other"]
    owner: str | None = Field(default=None, max_length=191)
    intended_recipient: str | None = Field(default=None, max_length=191)
    expires_on: date | None = None
    review_on: date | None = None


class SecretGenerate(BaseModel):
    length: int = Field(default=24, ge=16, le=128)


class SecretReveal(BaseModel):
    """Only returned by the explicit reveal operation; never used in lists."""

    credential_id: int
    version: int
    secret: str


class CredentialGrantCreate(BaseModel):
    """Grant one immutable onboarding credential version to one recipient."""

    recipient_user_id: int | None = Field(default=None, gt=0)
    recipient_email: str | None = Field(default=None, min_length=3, max_length=254)
    staff_id: int = Field(gt=0)
    reason: str = Field(min_length=3, max_length=500)
    expires_at: datetime


class CredentialGrant(BaseModel):
    id: int
    credential_id: int
    credential_version: int
    company_id: int
    staff_id: int
    recipient_user_id: int | None = None
    recipient_email: str | None = None
    reason: str
    expires_at: datetime | str
    revoked_at: datetime | str | None = None
    consumed_at: datetime | str | None = None


class ExternalGrantCreated(CredentialGrant):
    """Secrets returned once for delivery over two independent channels."""

    share_token: str
    verification_code: str


class ShareVerification(BaseModel):
    share_token: str = Field(min_length=32, max_length=256)
    verification_code: str = Field(min_length=6, max_length=32)


class ShareToken(BaseModel):
    share_token: str = Field(min_length=32, max_length=256)
