"""Permission-aware documentation search API."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request

from app.api.dependencies.auth import get_current_user
from app.schemas.documentation_search import DocumentationSearchResponse
from app.services.documentation_search import search_documentation

router = APIRouter(prefix="/api/documentation", tags=["Documentation"])


@router.get("/search", response_model=DocumentationSearchResponse)
async def documentation_search(
    request: Request,
    q: Annotated[str, Query(min_length=1, max_length=500)],
    company_id: int | None = None,
    source: Annotated[list[str] | None, Query()] = None,
    asset_type: str | None = None,
    status: str | None = None,
    owner: str | None = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100)] = 20,
    current_user: dict = Depends(get_current_user),
) -> DocumentationSearchResponse:
    result = await search_documentation(
        q, current_user,
        active_company_id=(getattr(request.state, "active_company_id", None)
                           or current_user.get("company_id")),
        memberships=getattr(request.state, "available_companies", None),
        company_id=company_id, sources=source, asset_type=asset_type,
        status=status, owner=owner, page=page, page_size=page_size,
    )
    return DocumentationSearchResponse(**result)
