"""Validated payloads exchanged with the Windows tray agent."""
from datetime import datetime
from typing import Any, Literal
from pydantic import BaseModel, Field, field_validator

class DefenderStatusReport(BaseModel):
    antivirus_enabled: bool = False
    realtime_protection_enabled: bool = False
    tamper_protection_enabled: bool = False
    signatures_updated_at: datetime | None = None
    last_scan_at: datetime | None = None
    scan_history: list["DefenderScanReport"] = Field(default_factory=list, max_length=20)
    health_status: Literal["healthy", "warning", "critical", "unknown"] = "unknown"
    details: dict[str, Any] = Field(default_factory=dict)

class DefenderScanReport(BaseModel):
    scan_type: Literal["quick", "full", "custom", "unknown"] = "unknown"
    started_at: datetime | None = None
    completed_at: datetime | None = None
    duration_seconds: int | None = Field(default=None, ge=0)
    status: Literal["completed", "running", "cancelled", "failed", "unknown"] = "unknown"

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

class DefenderExclusionListItem(BaseModel):
    exclusion_type: Literal["path", "process", "extension"]
    value: str = Field(min_length=1, max_length=1000)

class DefenderExclusionListCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    exclusions: list[DefenderExclusionListItem] = Field(default_factory=list)
    company_ids: list[int] = Field(default_factory=list)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("Name cannot be blank")
        return value

    @field_validator("company_ids")
    @classmethod
    def validate_company_ids(cls, value: list[int]) -> list[int]:
        if any(company_id < 1 for company_id in value):
            raise ValueError("Company IDs must be positive")
        return list(dict.fromkeys(value))

class DefenderSettingsUpdate(BaseModel):
    scheduled_scan_type: Literal["quick", "full"] | None = None
    scheduled_scan_day: int | None = Field(default=None, ge=0, le=6)
    scheduled_scan_time: str | None = Field(default=None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    auto_ticket_min_severity: Literal["low", "medium", "high", "critical"] | None = None
    auto_ticket_antivirus_off: bool = False
    auto_ticket_realtime_off: bool = False
    auto_ticket_tamper_off: bool = False
    auto_ticket_threat_detected: bool = False

class DefenderCommandResult(BaseModel):
    status: Literal["completed", "failed"]
    result: dict[str, Any] = Field(default_factory=dict)

class DefenderDetectionAction(BaseModel):
    action: Literal["acknowledge", "resolve", "reopen", "quarantine", "remediate"]
