from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr


class CredentialCreate(BaseModel):
    company_id: int = Field(gt=0)
    name: str = Field(min_length=1, max_length=191)
    username: str | None = Field(default=None, max_length=191)
    secret: SecretStr = Field(min_length=1, max_length=65535)
    links: list[tuple[Literal["asset", "staff", "ticket"], int]] = Field(
        default_factory=list
    )


class CredentialMetadata(BaseModel):
    id: int
    company_id: int
    name: str
    username: str | None = None
    current_version: int
    created_at: datetime | str | None = None
    updated_at: datetime | str | None = None
    model_config = ConfigDict(extra="ignore")


class SecretReplace(BaseModel):
    secret: SecretStr = Field(min_length=1, max_length=65535)


class SecretReveal(BaseModel):
    """Only returned by the explicit reveal operation; never used in lists."""

    credential_id: int
    version: int
    secret: str
