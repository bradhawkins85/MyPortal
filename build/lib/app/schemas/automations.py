from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel, Field


class AutomationBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = None
    kind: str = Field(..., pattern=r"^(scheduled|event)$")
    cadence: Optional[str] = Field(default=None, max_length=64)
    cron_expression: Optional[str] = Field(default=None, max_length=255)
    scheduled_time: Optional[datetime] = None
    run_once: bool = False
    trigger_event: Optional[str] = Field(default=None, max_length=128)
    trigger_filters: Optional[dict[str, Any]] = None
    action_module: Optional[str] = Field(default=None, max_length=64)
    action_payload: Optional[dict[str, Any]] = None
    status: str = Field(default="inactive", pattern=r"^(active|inactive)$")


class AutomationCreate(AutomationBase):
    pass


class AutomationUpdate(BaseModel):
    name: Optional[str] = Field(default=None, min_length=1, max_length=255)
    description: Optional[str] = None
    kind: Optional[str] = Field(default=None, pattern=r"^(scheduled|event)$")
    cadence: Optional[str] = Field(default=None, max_length=64)
    cron_expression: Optional[str] = Field(default=None, max_length=255)
    scheduled_time: Optional[datetime] = None
    run_once: Optional[bool] = None
    trigger_event: Optional[str] = Field(default=None, max_length=128)
    trigger_filters: Optional[dict[str, Any]] = None
    action_module: Optional[str] = Field(default=None, max_length=64)
    action_payload: Optional[dict[str, Any]] = None
    status: Optional[str] = Field(default=None, pattern=r"^(active|inactive)$")


class AutomationResponse(AutomationBase):
    id: int
    scheduled_time: Optional[datetime]
    run_once: bool
    next_run_at: Optional[datetime]
    last_run_at: Optional[datetime]
    last_error: Optional[str]
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class AutomationRunResponse(BaseModel):
    id: int
    automation_id: int
    status: str
    started_at: datetime
    finished_at: Optional[datetime]
    duration_ms: Optional[int]
    result_payload: Optional[dict[str, Any]]
    error_message: Optional[str]

    class Config:
        from_attributes = True


class AutomationExecutionResult(BaseModel):
    status: str
    result: Optional[Any]
    error: Optional[str]
    started_at: datetime
    finished_at: datetime
    next_run_at: Optional[datetime]
