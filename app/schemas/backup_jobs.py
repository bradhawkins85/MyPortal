from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class BackupStatusReport(BaseModel):
    """Webhook body sent by a backup script to report status.

    The ``job_id`` is the unique random token shown to the admin when the
    job is created. ``status`` accepts pass / fail / warn / unknown plus
    common aliases (ok, success, error, warning, ...).
    """

    model_config = ConfigDict(extra="ignore", populate_by_name=True)

    job_id: str = Field(
        ...,
        alias="job_id",
        min_length=1,
        max_length=128,
        description="Unique random JobID issued when the backup job was created.",
    )
    status: str = Field(
        ...,
        min_length=1,
        max_length=32,
        description="Status of the backup run (pass / fail / warn / unknown).",
    )
    message: str | None = Field(
        default=None,
        max_length=2000,
        description="Optional human-readable status detail.",
    )


class BackupStatusReportResponse(BaseModel):
    job_id: int
    company_id: int
    name: str
    event_date: date
    status: str
    status_message: str | None = None
    reported_at: datetime | None = None


class BackupJobBase(BaseModel):
    company_id: int = Field(..., gt=0)
    name: str = Field(..., min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    is_active: bool = True
    pass_protection: bool = Field(
        default=False,
        description=(
            "When enabled, a Pass status recorded for the current day cannot be "
            "overwritten by a subsequent Warn or Fail report. Designed for jobs "
            "that run multiple times a day against unreliable storage."
        ),
    )
    alert_no_success_days: int | None = Field(
        default=None,
        ge=1,
        description="Raise a ticket when no successful backup has been recorded in this many days. Leave empty to disable.",
    )
    alert_fail_days: int | None = Field(
        default=None,
        ge=1,
        description="Raise a ticket when the backup has failed for this many consecutive days. Leave empty to disable.",
    )
    alert_unknown_days: int | None = Field(
        default=None,
        ge=1,
        description="Raise a ticket when the backup status has been unknown for this many consecutive days. Leave empty to disable.",
    )


class BackupJobCreate(BackupJobBase):
    pass


class BackupJobUpdate(BaseModel):
    company_id: int | None = Field(default=None, gt=0)
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    is_active: bool | None = None
    pass_protection: bool | None = None
    alert_no_success_days: int | None = Field(default=None, ge=1)
    alert_fail_days: int | None = Field(default=None, ge=1)
    alert_unknown_days: int | None = Field(default=None, ge=1)


class BackupJobResponse(BaseModel):
    id: int
    company_id: int
    name: str
    description: str | None = None
    token: str
    is_active: bool = True
    pass_protection: bool = False
    created_by: int | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    latest_status: str | None = None
    latest_event_date: date | None = None
    alert_no_success_days: int | None = None
    alert_fail_days: int | None = None
    alert_unknown_days: int | None = None


def serialise_job(job: dict[str, Any]) -> BackupJobResponse:
    latest = job.get("latest_event") or {}
    return BackupJobResponse(
        id=int(job["id"]),
        company_id=int(job["company_id"]),
        name=str(job.get("name") or ""),
        description=job.get("description"),
        token=str(job.get("token") or ""),
        is_active=bool(job.get("is_active", True)),
        pass_protection=bool(job.get("pass_protection", False)),
        created_by=job.get("created_by"),
        created_at=job.get("created_at"),
        updated_at=job.get("updated_at"),
        latest_status=latest.get("status"),
        latest_event_date=latest.get("event_date"),
        alert_no_success_days=job.get("alert_no_success_days"),
        alert_fail_days=job.get("alert_fail_days"),
        alert_unknown_days=job.get("alert_unknown_days"),
    )


__all__ = [
    "BackupJobBase",
    "BackupJobCreate",
    "BackupJobResponse",
    "BackupJobUpdate",
    "BackupStatusReport",
    "BackupStatusReportResponse",
    "serialise_job",
]
