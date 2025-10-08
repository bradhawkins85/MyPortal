from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class RoleBase(BaseModel):
    name: str = Field(..., min_length=2, max_length=100)
    description: Optional[str] = None
    permissions: list[str] = Field(default_factory=list)


class RoleCreate(RoleBase):
    is_system: bool = False


class RoleUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=2, max_length=100)
    description: Optional[str] = None
    permissions: Optional[list[str]] = None


class RoleResponse(RoleBase):
    id: int
    is_system: bool = False

    class Config:
        from_attributes = True
