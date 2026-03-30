from __future__ import annotations

import re
from typing import Any

from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator, model_validator


_WORKFLOW_KEY_PATTERN = re.compile(r"[^a-z0-9_.-]+")


class WorkflowStepDefinition(BaseModel):
    key: str = Field(..., min_length=1, max_length=120)
    name: str = Field(..., min_length=1, max_length=255)
    enabled: bool = True
    config: dict[str, Any] = Field(default_factory=dict)

    @field_validator("key", mode="before")
    @classmethod
    def validate_key(cls, value: Any) -> str:
        text = str(value or "").strip().lower()
        text = _WORKFLOW_KEY_PATTERN.sub("_", text).strip("._-")
        if not text:
            raise ValueError("Step key is required.")
        return text

    @field_validator("name", mode="before")
    @classmethod
    def validate_name(cls, value: Any) -> str:
        text = str(value or "").strip()
        if not text:
            raise ValueError("Step name is required.")
        return text


class WorkflowFailurePolicy(BaseModel):
    mode: str = Field(default="fail_fast")
    create_ticket_on_failure: bool = Field(
        default=True,
        validation_alias=AliasChoices("create_ticket_on_failure", "createTicketOnFailure"),
    )
    notify_user_ids: list[int] = Field(
        default_factory=list,
        validation_alias=AliasChoices("notify_user_ids", "notifyUserIds"),
    )
    max_consecutive_failures: int = Field(
        default=1,
        ge=1,
        le=20,
        validation_alias=AliasChoices("max_consecutive_failures", "maxConsecutiveFailures"),
    )

    @field_validator("mode", mode="before")
    @classmethod
    def validate_mode(cls, value: Any) -> str:
        text = str(value or "fail_fast").strip().lower()
        allowed = {"fail_fast", "continue", "retry_then_fail"}
        if text not in allowed:
            raise ValueError(f"Failure policy mode must be one of: {', '.join(sorted(allowed))}.")
        return text


class WorkflowConfigSchema(BaseModel):
    version: int = Field(default=1, ge=1, le=10)
    steps: list[WorkflowStepDefinition] = Field(default_factory=list)
    failure_policy: WorkflowFailurePolicy = Field(default_factory=WorkflowFailurePolicy)


class CompanyWorkflowPolicyUpsertSchema(BaseModel):
    workflow_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("workflow_key", "workflowKey"),
    )
    workflow_name: str | None = Field(
        default=None,
        validation_alias=AliasChoices("workflow_name", "workflowName", "name"),
    )
    enabled: bool = Field(
        default=True,
        validation_alias=AliasChoices("enabled", "is_enabled", "isEnabled"),
    )
    max_retries: int = Field(
        default=2,
        ge=0,
        le=20,
        validation_alias=AliasChoices("max_retries", "maxRetries"),
    )
    config: dict[str, Any] = Field(
        default_factory=dict,
        validation_alias=AliasChoices("config", "config_json", "configJson"),
    )
    model_config = ConfigDict(populate_by_name=True)

    @field_validator("workflow_key", "workflow_name", mode="before")
    @classmethod
    def clean_text(cls, value: Any) -> str | None:
        text = str(value or "").strip()
        return text or None

    @model_validator(mode="after")
    def ensure_workflow_key(self) -> "CompanyWorkflowPolicyUpsertSchema":
        base_key = self.workflow_key or self.workflow_name
        if not base_key:
            raise ValueError("Workflow key or workflow name is required.")
        normalized = _WORKFLOW_KEY_PATTERN.sub("_", base_key.strip().lower()).strip("._-")
        if not normalized:
            raise ValueError("Workflow key must include at least one letter or number.")
        self.workflow_key = normalized
        return self
