from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

PolicyMode = Literal["report", "alert", "remediate"]


class PolicyStandard(BaseModel):
    standard_id: str = Field(max_length=100)
    mode: PolicyMode = "report"
    settings: dict[str, Any] = Field(default_factory=dict)


class PolicyOverride(BaseModel):
    company_id: int = Field(gt=0)
    action: Literal["include", "exclude"]


class PolicyWrite(BaseModel):
    name: str = Field(min_length=1, max_length=150)
    description: str = Field(default="", max_length=2000)
    enabled: bool = True
    target_rules: dict[str, Any] | None = Field(
        default=None,
        description="An and/or rule tree; null targets every connected tenant.",
        examples=[{"operator": "and", "children": [
            {"field": "m365.license_sku", "operator": "in", "value": ["SPB"]},
        ]}],
    )
    standards: list[PolicyStandard] = Field(default_factory=list)
    overrides: list[PolicyOverride] = Field(default_factory=list)


class PolicyRead(PolicyWrite):
    id: int
    created_by: int | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class PolicyPreviewRequest(BaseModel):
    target_rules: dict[str, Any] | None = None
    overrides: list[PolicyOverride] = Field(default_factory=list)


class PolicyPreviewTenant(BaseModel):
    company_id: int
    company_name: str


class PolicyPreviewResponse(BaseModel):
    matched_count: int
    eligible_count: int
    tenants: list[PolicyPreviewTenant]


class StandardSummary(BaseModel):
    id: str
    name: str
    category: str
    description: str
    modes: list[PolicyMode]
    default_settings: dict[str, Any]


class StandardResult(BaseModel):
    company_id: int
    standard_id: str
    policy_id: int | None = None
    mode: PolicyMode
    status: Literal["compliant", "drift", "remediated", "failed", "unknown"]
    details: str | None = None
    affected: list[Any] = Field(default_factory=list)
    checked_at: datetime | None = None
    drift_since: datetime | None = None
    remediated_at: datetime | None = None
