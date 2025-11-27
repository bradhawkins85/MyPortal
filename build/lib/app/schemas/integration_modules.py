from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


class IntegrationModuleResponse(BaseModel):
    id: int
    slug: str
    name: str
    description: Optional[str]
    icon: Optional[str]
    enabled: bool
    settings: dict[str, Any]
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class IntegrationModuleUpdate(BaseModel):
    enabled: Optional[bool] = None
    settings: Optional[dict[str, Any]] = Field(default=None)

