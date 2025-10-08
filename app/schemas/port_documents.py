from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field


class PortDocumentResponse(BaseModel):
    id: int
    port_id: int
    file_name: str
    storage_path: str
    content_type: Optional[str] = None
    file_size: int
    description: Optional[str] = Field(default=None, max_length=255)
    uploaded_by: Optional[int] = None
    uploaded_at: datetime

    class Config:
        from_attributes = True


class PortDocumentUploadResponse(PortDocumentResponse):
    pass
