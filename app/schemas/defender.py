"""Validated payloads exchanged with the Windows tray agent."""
from datetime import datetime
from typing import Any, Literal
from pydantic import BaseModel, Field

class DefenderStatusReport(BaseModel):
    antivirus_enabled: bool = False
    realtime_protection_enabled: bool = False
    tamper_protection_enabled: bool = False
    signatures_updated_at: datetime | None = None
    last_scan_at: datetime | None = None
    health_status: Literal["healthy", "warning", "critical", "unknown"] = "unknown"
    details: dict[str, Any] = Field(default_factory=dict)

class DefenderDetectionReport(BaseModel):
    detection_uid: str = Field(min_length=1, max_length=255)
    threat_name: str = Field(min_length=1, max_length=500)
    severity: Literal["low", "medium", "high", "critical", "unknown"] = "unknown"
    status: str = Field(default="active", max_length=32)
    detected_at: datetime
    details: dict[str, Any] = Field(default_factory=dict)

class DefenderExclusionCreate(BaseModel):
    scope: Literal["global", "company", "device"]
    exclusion_type: Literal["path", "process", "extension"]
    value: str = Field(min_length=1, max_length=1000)
    tray_device_id: int | None = Field(default=None, ge=1)

class DefenderSettingsUpdate(BaseModel):
    scheduled_scan_type: Literal["quick", "full"] | None = None
    scheduled_scan_day: int | None = Field(default=None, ge=0, le=6)
    scheduled_scan_time: str | None = Field(default=None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    auto_ticket_min_severity: Literal["low", "medium", "high", "critical"] | None = None

class DefenderCommandResult(BaseModel):
    status: Literal["completed", "failed"]
    result: dict[str, Any] = Field(default_factory=dict)

class DefenderDetectionAction(BaseModel):
    action: Literal["acknowledge", "resolve", "reopen", "quarantine", "remediate"]
