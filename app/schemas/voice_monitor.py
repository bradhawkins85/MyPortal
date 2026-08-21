"""Validation models for tenant voice monitoring."""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Annotated, Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import phonenumbers
from phonenumbers import PhoneNumberType
from pydantic import BaseModel, Field, ValidationInfo, field_validator, model_validator


class DialingPolicy(BaseModel):
    """Deployment-specific restrictions applied after E.164 normalization."""

    allowed_country_codes: set[int] | None = None
    blocked_e164_prefixes: set[str] = Field(default_factory=set)
    allow_toll_free: bool = True


def normalize_dial_destination(value: str, policy: DialingPolicy | None = None) -> str:
    """Normalize a dial target and reject unsafe/non-subscriber destinations."""
    policy = policy or DialingPolicy()
    try:
        number = phonenumbers.parse(value, None)
    except phonenumbers.NumberParseException as exc:
        raise ValueError("destination must be a valid E.164 number") from exc
    if not value.startswith("+") or not phonenumbers.is_valid_number(number):
        raise ValueError("destination must be a valid E.164 number")
    e164 = phonenumbers.format_number(number, phonenumbers.PhoneNumberFormat.E164)
    number_type = phonenumbers.number_type(number)
    prohibited = {
        PhoneNumberType.PREMIUM_RATE,
        PhoneNumberType.SHARED_COST,
        PhoneNumberType.PERSONAL_NUMBER,
        PhoneNumberType.PAGER,
        PhoneNumberType.UNKNOWN,
    }
    if number_type in prohibited:
        raise ValueError("destination type is prohibited by dialing policy")
    if number_type == PhoneNumberType.TOLL_FREE and not policy.allow_toll_free:
        raise ValueError("toll-free destinations are prohibited by dialing policy")
    if phonenumbers.shortnumberinfo.is_emergency_number(value, None) or len(str(number.national_number)) < 7:
        raise ValueError("emergency and short-code destinations are prohibited")
    if policy.allowed_country_codes is not None and number.country_code not in policy.allowed_country_codes:
        raise ValueError("destination country is prohibited by dialing policy")
    if any(e164.startswith(prefix) for prefix in policy.blocked_e164_prefixes):
        raise ValueError("destination prefix is prohibited by dialing policy")
    return e164


class ExpectedBehavior(str, Enum):
    answer = "answer"
    no_answer = "no_answer"
    busy = "busy"
    voicemail = "voicemail"
    any = "any"


class AttemptStatus(str, Enum):
    queued = "queued"
    dialing = "dialing"
    answered = "answered"
    passed = "passed"
    failed = "failed"
    timed_out = "timed_out"
    cancelled = "cancelled"


class VoiceMonitorConfiguration(BaseModel):
    subscription_id: str | None = None
    destination_e164: str
    display_label: Annotated[str, Field(min_length=1, max_length=255)]
    enabled: bool = True
    timezone: str = "UTC"
    schedule_cron: str | None = None
    interval_seconds: Annotated[int | None, Field(ge=60)] = None
    timeout_seconds: Annotated[int, Field(ge=5, le=300)] = 30
    max_retries: Annotated[int, Field(ge=0, le=20)] = 0
    retry_delay_seconds: Annotated[int, Field(ge=0, le=86400)] = 60
    expected_behavior: ExpectedBehavior = ExpectedBehavior.answer
    transcription_enabled: bool = False
    ticket_on_failure: bool = False
    ticket_failure_threshold: Annotated[int, Field(ge=1, le=100)] = 1

    @field_validator("destination_e164")
    @classmethod
    def destination_is_safe(cls, value: str, info: ValidationInfo) -> str:
        context: dict[str, Any] = info.context or {}
        return normalize_dial_destination(value, context.get("dialing_policy"))

    @field_validator("timezone")
    @classmethod
    def timezone_exists(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("timezone must be a valid IANA timezone") from exc
        return value

    @model_validator(mode="after")
    def exactly_one_schedule(self) -> "VoiceMonitorConfiguration":
        if (self.schedule_cron is None) == (self.interval_seconds is None):
            raise ValueError("exactly one of schedule_cron or interval_seconds is required")
        return self


class VoiceMonitorConfigurationUpdate(VoiceMonitorConfiguration):
    pass


class VoiceMonitorAttemptResult(BaseModel):
    id: int
    endpoint_id: int | None
    company_id: int
    queued_at: datetime
    started_at: datetime | None = None
    answered_at: datetime | None = None
    completed_at: datetime | None = None
    outcome_status: AttemptStatus
    provider_response_code: str | None = None
    provider_call_id: str | None = None
    failure_category: str | None = None
    failure_detail: str | None = None
    duration_seconds: int | None = None
    media_artifact_reference: str | None = None
    transcript_status: str
    transcript_text_reference: str | None = None
    retry_count: int = 0
    worker_identity: str | None = None
    created_ticket_id: int | None = None


class VoiceMonitorAttemptPage(BaseModel):
    items: list[VoiceMonitorAttemptResult]
    total: int = Field(ge=0)
    limit: int = Field(ge=1, le=500)
    offset: int = Field(ge=0)


class VoiceMonitorAdminTestCallRequest(BaseModel):
    company_id: int = Field(gt=0)
    destination_e164: str
    timeout_seconds: int = Field(default=30, ge=5, le=300)
    expected_behavior: ExpectedBehavior = ExpectedBehavior.answer
    transcription_enabled: bool = False

    @field_validator("destination_e164")
    @classmethod
    def destination_is_safe(cls, value: str, info: ValidationInfo) -> str:
        context: dict[str, Any] = info.context or {}
        return normalize_dial_destination(value, context.get("dialing_policy"))
