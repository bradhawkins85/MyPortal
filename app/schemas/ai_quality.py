from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class AiFeedbackRequest(BaseModel):
    response_id: int = Field(gt=0, description="The AI output being rated")
    rating: Literal["up", "down"] | None = Field(description="`up`, `down`, or null to clear your vote")
    reason: str | None = Field(default=None, max_length=40, description="Why it was not helpful (👎 only)")
    comment: str | None = Field(default=None, max_length=1000)


class AiFeedbackResponse(BaseModel):
    response_id: int
    rating: Literal["up", "down"] | None
    helpful: int
    unhelpful: int
