"""Pydantic schemas for the Solidtime integration API."""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


class SolidtimeOrganisation(BaseModel):
    """A subset of the membership/organisation payload returned by Solidtime."""

    id: str = Field(..., description="Solidtime organisation/membership UUID")
    name: Optional[str] = Field(None, description="Display name of the organisation")
    role: Optional[str] = Field(None, description="The signed-in user's role within the organisation")


class SolidtimeOrganisationListResponse(BaseModel):
    organizations: list[SolidtimeOrganisation] = Field(default_factory=list)


class SolidtimeTestConnectionResponse(BaseModel):
    ok: bool
    message: str = ""
    organizations: list[SolidtimeOrganisation] = Field(default_factory=list)


class SolidtimeSyncResponse(BaseModel):
    ok: bool
    detail: str = ""
    project_url: Optional[str] = None
    time_entry_id: Optional[str] = None
    project_id: Optional[str] = None


class SolidtimeWebhookEvent(BaseModel):
    """Inbound Solidtime webhook payload.

    Solidtime does not currently emit native webhooks; this schema describes
    the format expected when an operator forwards events to MyPortal via a
    self-hosted relay. The ``data`` payload is intentionally permissive
    because Solidtime adds new fields over time.
    """

    type: str = Field(..., description="Event type, e.g. 'project.updated'")
    organization_id: Optional[str] = Field(
        None, description="Solidtime organisation UUID"
    )
    data: dict[str, Any] = Field(default_factory=dict)
