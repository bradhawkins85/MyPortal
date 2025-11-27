from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class FormBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    url: str = Field(..., description="Absolute OpnForm form URL")
    description: Optional[str] = Field(default=None, max_length=1024)
    embed_code: Optional[str] = Field(
        default=None,
        description="Optional OpnForm embed snippet containing an iframe",
    )


class FormCreate(FormBase):
    pass


class FormUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    url: Optional[str] = Field(default=None, description="Absolute OpnForm form URL")
    description: Optional[str] = Field(default=None, max_length=1024)
    embed_code: Optional[str] = Field(
        default=None,
        description="Optional OpnForm embed snippet containing an iframe",
    )


class FormResponse(FormBase):
    id: int

    class Config:
        from_attributes = True


class FormPermissionUpdate(BaseModel):
    user_ids: list[int] = Field(default_factory=list, description="User identifiers to grant access")
