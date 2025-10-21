from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class ScheduledTaskBase(BaseModel):
    name: str
    command: str
    cron: str
    company_id: int | None = Field(default=None, validation_alias="companyId")
    description: str | None = None
    active: bool = True
    max_retries: int = Field(default=0, validation_alias="maxRetries")
    retry_backoff_seconds: int = Field(default=300, validation_alias="retryBackoffSeconds")

    model_config = {
        "populate_by_name": True,
    }


class ScheduledTaskCreate(ScheduledTaskBase):
    pass


class ScheduledTaskUpdate(BaseModel):
    name: str | None = None
    command: str | None = None
    cron: str | None = None
    company_id: int | None = Field(default=None, validation_alias="companyId")
    description: str | None = None
    active: bool | None = None
    max_retries: int | None = Field(default=None, validation_alias="maxRetries")
    retry_backoff_seconds: int | None = Field(default=None, validation_alias="retryBackoffSeconds")

    model_config = {
        "populate_by_name": True,
    }


class ScheduledTaskResponse(BaseModel):
    id: int
    name: str
    command: str
    cron: str
    company_id: int | None = Field(default=None, serialization_alias="companyId")
    description: str | None = None
    active: bool
    max_retries: int = Field(serialization_alias="maxRetries")
    retry_backoff_seconds: int = Field(serialization_alias="retryBackoffSeconds")
    last_run_at: datetime | None = Field(default=None, serialization_alias="lastRunAt")
    last_status: str | None = Field(default=None, serialization_alias="lastStatus")
    last_error: str | None = Field(default=None, serialization_alias="lastError")

    model_config = {
        "populate_by_name": True,
    }


class ScheduledTaskRunResponse(BaseModel):
    id: int
    task_id: int = Field(serialization_alias="taskId")
    task_name: str = Field(serialization_alias="taskName")
    status: str
    started_at: datetime | None = Field(default=None, serialization_alias="startedAt")
    finished_at: datetime | None = Field(default=None, serialization_alias="finishedAt")
    duration_ms: int | None = Field(default=None, serialization_alias="durationMs")
    details: str | None = None

    model_config = {
        "populate_by_name": True,
    }


class WebhookEventResponse(BaseModel):
    id: int
    name: str
    target_url: str = Field(serialization_alias="targetUrl")
    headers: dict[str, Any] | None = None
    payload: Any = None
    status: str
    attempt_count: int = Field(serialization_alias="attemptCount")
    max_attempts: int = Field(serialization_alias="maxAttempts")
    backoff_seconds: int = Field(serialization_alias="backoffSeconds")
    next_attempt_at: datetime | None = Field(default=None, serialization_alias="nextAttemptAt")
    last_error: str | None = Field(default=None, serialization_alias="lastError")
    response_status: int | None = Field(default=None, serialization_alias="responseStatus")
    response_body: str | None = Field(default=None, serialization_alias="responseBody")
    created_at: datetime | None = Field(default=None, serialization_alias="createdAt")
    updated_at: datetime | None = Field(default=None, serialization_alias="updatedAt")

    model_config = {
        "populate_by_name": True,
    }


class WebhookEventAttemptResponse(BaseModel):
    id: int
    event_id: int = Field(serialization_alias="eventId")
    attempt_number: int = Field(serialization_alias="attemptNumber")
    status: str
    response_status: int | None = Field(default=None, serialization_alias="responseStatus")
    response_body: str | None = Field(default=None, serialization_alias="responseBody")
    error_message: str | None = Field(default=None, serialization_alias="errorMessage")
    request_headers: dict[str, Any] | None = Field(default=None, serialization_alias="requestHeaders")
    request_body: Any = Field(default=None, serialization_alias="requestBody")
    response_headers: dict[str, Any] | None = Field(default=None, serialization_alias="responseHeaders")
    attempted_at: datetime | None = Field(default=None, serialization_alias="attemptedAt")

    model_config = {
        "populate_by_name": True,
    }


class ActivateTaskRequest(BaseModel):
    active: bool


class RunTaskResponse(BaseModel):
    accepted: bool = True
