from __future__ import annotations

from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel


class AssetResponse(BaseModel):
    id: int
    company_id: int
    name: str
    type: Optional[str] = None
    serial_number: Optional[str] = None
    status: Optional[str] = None
    os_name: Optional[str] = None
    cpu_name: Optional[str] = None
    ram_gb: Optional[float] = None
    hdd_size: Optional[str] = None
    last_sync: Optional[datetime] = None
    motherboard_manufacturer: Optional[str] = None
    form_factor: Optional[str] = None
    last_user: Optional[str] = None
    approx_age: Optional[float] = None
    performance_score: Optional[float] = None
    warranty_status: Optional[str] = None
    warranty_end_date: Optional[date] = None
    syncro_asset_id: Optional[str] = None
    tactical_asset_id: Optional[str] = None
