from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import AliasChoices, BaseModel, Field, field_validator


class UptimeKumaAlertPayload(BaseModel):
    monitor_id: int | None = Field(
        default=None,
        validation_alias=AliasChoices("monitorID", "monitor_id"),
        description="Numeric identifier of the monitor in Uptime Kuma.",
    )
    monitor_name: str | None = Field(
        default=None,
        validation_alias=AliasChoices("monitorName", "monitor_name"),
    )
    monitor_url: str | None = Field(
        default=None,
        validation_alias=AliasChoices("monitorURL", "monitor_url"),
    )
    monitor_type: str | None = Field(
        default=None,
        validation_alias=AliasChoices("monitorType", "monitor_type"),
    )
    monitor_hostname: str | None = Field(
        default=None,
        validation_alias=AliasChoices("monitorHostname", "monitor_hostname"),
    )
    monitor_port: int | str | None = Field(
        default=None,
        validation_alias=AliasChoices("monitorPort", "monitor_port"),
    )
    status: str = Field(..., description="Current status reported by Uptime Kuma (e.g. up, down).")
    previous_status: str | None = Field(
        default=None,
        validation_alias=AliasChoices("previousStatus", "previous_status"),
    )
    importance: bool | None = Field(default=None, validation_alias=AliasChoices("important"))
    alert_type: str | None = Field(default=None, validation_alias=AliasChoices("type", "alertType"))
    reason: str | None = Field(default=None, validation_alias=AliasChoices("reason", "downtimeReason"))
    message: str | None = Field(default=None, validation_alias=AliasChoices("msg", "message"))
    duration: float | int | None = Field(default=None, validation_alias=AliasChoices("duration"))
    ping: float | int | None = Field(default=None, validation_alias=AliasChoices("ping", "avgPing", "monitorPing"))
    time: datetime | float | int | str | None = Field(default=None, validation_alias=AliasChoices("time", "timestamp"))
    incident_id: str | None = Field(default=None, validation_alias=AliasChoices("incidentID", "incidentId", "incident_id"))
    uuid: str | None = Field(default=None, validation_alias=AliasChoices("uuid", "id"))
    tags: list[str] | None = Field(default=None, validation_alias=AliasChoices("tags"))

    model_config = {"extra": "allow", "populate_by_name": True}

    @field_validator("tags", mode="before")
    @classmethod
    def _coerce_tags(cls, value: Any) -> list[str] | None:
        if value in (None, "", []):
            return None
        if isinstance(value, list):
            return [str(item) for item in value if str(item)] or None
        return [str(value)]


class UptimeKumaAlertResponse(BaseModel):
    id: int
    event_uuid: str | None = None
    monitor_id: int | None = None
    monitor_name: str | None = None
    monitor_url: str | None = None
    monitor_type: str | None = None
    monitor_hostname: str | None = None
    monitor_port: str | None = None
    status: str
    previous_status: str | None = None
    importance: bool
    alert_type: str | None = None
    reason: str | None = None
    message: str | None = None
    duration_seconds: float | None = None
    ping_ms: float | None = None
    occurred_at: datetime | None = None
    received_at: datetime
    acknowledged_at: datetime | None = None
    acknowledged_by: int | None = None
    remote_addr: str | None = None
    user_agent: str | None = None
    payload: dict[str, Any]


class UptimeKumaAlertIngestResponse(BaseModel):
    status: str
    alert_id: int
