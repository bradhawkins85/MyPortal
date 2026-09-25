"""Public shapes for permission-scoped documentation discovery."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class DocumentationSearchResult(BaseModel):
    source: Literal["assets", "knowledge_base"]
    source_id: str
    title: str
    snippet: str
    url: str
    company_id: int | None = None
    score: float
    metadata: dict[str, Any] = Field(default_factory=dict)


class DocumentationSearchResponse(BaseModel):
    results: list[DocumentationSearchResult]
    total: int
    page: int
    page_size: int
    pages: int
    counts: dict[str, int] = Field(default_factory=dict)
