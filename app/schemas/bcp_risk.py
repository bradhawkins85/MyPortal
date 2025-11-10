"""
Pydantic schemas for BCP Risk Assessment.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, field_validator


class RiskBase(BaseModel):
    """Base schema for risk data."""
    description: str = Field(..., min_length=1, max_length=5000)
    likelihood: int = Field(..., ge=1, le=4, description="Likelihood rating 1-4")
    impact: int = Field(..., ge=1, le=4, description="Impact rating 1-4")
    preventative_actions: Optional[str] = Field(None, max_length=5000)
    contingency_plans: Optional[str] = Field(None, max_length=5000)


class RiskCreate(RiskBase):
    """Schema for creating a new risk."""
    pass


class RiskUpdate(BaseModel):
    """Schema for updating a risk."""
    description: Optional[str] = Field(None, min_length=1, max_length=5000)
    likelihood: Optional[int] = Field(None, ge=1, le=4)
    impact: Optional[int] = Field(None, ge=1, le=4)
    preventative_actions: Optional[str] = Field(None, max_length=5000)
    contingency_plans: Optional[str] = Field(None, max_length=5000)


class RiskResponse(RiskBase):
    """Schema for risk response."""
    id: int
    plan_id: int
    rating: int = Field(..., description="Computed risk rating (likelihood × impact)")
    severity: str = Field(..., description="Computed severity: Low, Moderate, High, Severe")
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class RiskHeatmapCell(BaseModel):
    """Schema for a heatmap cell."""
    likelihood: int
    impact: int
    count: int
    rating: int
    severity: str


class RiskHeatmapResponse(BaseModel):
    """Schema for heatmap response."""
    cells: list[RiskHeatmapCell]
    total: int
