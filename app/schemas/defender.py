"""Validated payloads exchanged with the Windows tray agent."""
from datetime import datetime
from typing import Annotated, Any, Literal
from pydantic import BaseModel, BeforeValidator, Field, field_validator


def _truncate(limit: int):
    """Clip agent-supplied text instead of rejecting the whole status report."""
    return BeforeValidator(lambda value: value[:limit] if isinstance(value, str) else value)

class DefenderDeviceManagementUpdate(BaseModel):
    """Enable or exclude one endpoint from Defender checks and commands."""
    managed: bool

class DefenderStatusReport(BaseModel):
    antivirus_enabled: bool = False
    realtime_protection_enabled: bool = False
    tamper_protection_enabled: bool = False
    firewall_domain_enabled: bool | None = None
    firewall_private_enabled: bool | None = None
    firewall_public_enabled: bool | None = None
    signatures_updated_at: datetime | None = None
    last_scan_at: datetime | None = None
    scan_history: list["DefenderScanReport"] = Field(default_factory=list, max_length=20)
    health_status: Literal["healthy", "warning", "critical", "unknown"] = "unknown"
    details: dict[str, Any] = Field(default_factory=dict)
    detections: list["DefenderDetectionReport"] = Field(default_factory=list, max_length=100)
    policy_result: "DefenderPolicyResultReport | None" = None

class DefenderTamperStateReport(BaseModel):
    """Local controls that can stop the agent applying Defender settings."""
    enabled: bool = False
    source: Annotated[str | None, _truncate(64)] = None
    # None when Defender does not report whether exclusions are protected.
    protects_exclusions: bool | None = None
    local_admin_merge_disabled: bool = False
    schedule_managed_by_policy: bool = False

class DefenderPolicyItemReport(BaseModel):
    setting: Annotated[str, _truncate(64)]
    value: Annotated[str, _truncate(1000)] = ""
    action: Annotated[str, _truncate(32)] = ""
    status: Literal["applied", "removed", "blocked_tamper_protection", "managed_by_policy",
                    "unsupported", "failed"]
    message: Annotated[str | None, _truncate(2000)] = None

class DefenderPolicyResultReport(BaseModel):
    """How the tray agent reconciled the portal policy on the endpoint."""
    status: Literal["applied", "partial", "blocked", "failed", "not_configured"]
    evaluated_at: datetime | None = None
    tamper_protection: DefenderTamperStateReport = Field(default_factory=DefenderTamperStateReport)
    items: list[DefenderPolicyItemReport] = Field(default_factory=list, max_length=2000)

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
    infected_files: list[str] = Field(default_factory=list, max_length=100)
    details: dict[str, Any] = Field(default_factory=dict)

def _clean_exclusion_value(value: str) -> str:
    value = value.strip()
    if not value:
        raise ValueError("Exclusion value cannot be blank")
    if any(ord(character) < 32 for character in value):
        raise ValueError("Exclusion value cannot contain line breaks or control characters")
    return value

class DefenderExclusionCreate(BaseModel):
    scope: Literal["global", "company", "device"]
    # Registry entries are accepted for record keeping, but Microsoft Defender
    # Antivirus has no registry exclusions, so agents report them unsupported.
    exclusion_type: Literal["path", "process", "extension", "registry"]
    value: str = Field(min_length=1, max_length=1000)
    tray_device_id: int | None = Field(default=None, ge=1)

    _clean_value = field_validator("value")(_clean_exclusion_value)

class DefenderExclusionListItem(BaseModel):
    exclusion_type: Literal["path", "process", "extension", "registry"]
    value: str = Field(min_length=1, max_length=1000)

    _clean_value = field_validator("value")(_clean_exclusion_value)

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
