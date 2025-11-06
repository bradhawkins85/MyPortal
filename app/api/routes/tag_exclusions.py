from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.api.dependencies.auth import get_current_user, require_super_admin
from app.core.logging import log_error
from app.repositories import tag_exclusions as tag_exclusions_repo
from app.repositories import tickets as tickets_repo
from app.repositories import knowledge_base as kb_repo
from app.services.tagging import slugify_tag

router = APIRouter(prefix="/api/tag-exclusions", tags=["Tag Exclusions"])


class TagExclusionCreate(BaseModel):
    tag_slug: str = Field(..., min_length=1, max_length=48, description="Tag slug to exclude")


class TagExclusionResponse(BaseModel):
    id: int
    tag_slug: str
    created_at: str
    created_by: int | None


class TagExclusionListResponse(BaseModel):
    exclusions: list[TagExclusionResponse]


class RemoveTagRequest(BaseModel):
    tag_slug: str = Field(..., min_length=1, max_length=48, description="Tag slug to remove")


@router.get("", response_model=TagExclusionListResponse)
async def list_tag_exclusions(
    current_user: dict = Depends(require_super_admin),
) -> TagExclusionListResponse:
    """List all tag exclusions."""
    exclusions = await tag_exclusions_repo.list_tag_exclusions()
    return TagExclusionListResponse(
        exclusions=[
            TagExclusionResponse(
                id=exc["id"],
                tag_slug=exc["tag_slug"],
                created_at=exc["created_at"].isoformat() if exc.get("created_at") else "",
                created_by=exc.get("created_by"),
            )
            for exc in exclusions
        ]
    )


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_tag_exclusion(
    request: TagExclusionCreate,
    current_user: dict = Depends(require_super_admin),
) -> dict[str, Any]:
    """Add a new tag exclusion."""
    # Normalize the tag slug
    normalized_slug = slugify_tag(request.tag_slug)
    if not normalized_slug:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid tag slug",
        )
    
    # Check if it already exists
    if await tag_exclusions_repo.is_tag_excluded(normalized_slug):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Tag exclusion already exists",
        )
    
    user_id = current_user.get("id")
    try:
        user_id_int = int(user_id) if user_id else None
    except (TypeError, ValueError):
        user_id_int = None
    
    exclusion = await tag_exclusions_repo.add_tag_exclusion(normalized_slug, user_id_int)
    if not exclusion:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to create tag exclusion",
        )
    
    return {
        "id": exclusion["id"],
        "tag_slug": exclusion["tag_slug"],
        "created_at": exclusion["created_at"].isoformat() if exclusion.get("created_at") else "",
        "created_by": exclusion.get("created_by"),
    }


@router.delete("/{tag_slug}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_tag_exclusion(
    tag_slug: str,
    current_user: dict = Depends(require_super_admin),
) -> None:
    """Delete a tag exclusion."""
    if not await tag_exclusions_repo.delete_tag_exclusion(tag_slug):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Tag exclusion not found",
        )


@router.post("/tickets/{ticket_id}/remove-tag", status_code=status.HTTP_200_OK)
async def remove_ticket_tag(
    ticket_id: int,
    request: RemoveTagRequest,
    current_user: dict = Depends(get_current_user),
) -> dict[str, Any]:
    """Remove a specific tag from a ticket's AI tags."""
    ticket = await tickets_repo.get_ticket(ticket_id)
    if not ticket:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Ticket not found",
        )
    
    ai_tags = ticket.get("ai_tags", [])
    if not isinstance(ai_tags, list):
        ai_tags = []
    
    normalized_slug = slugify_tag(request.tag_slug)
    if normalized_slug and normalized_slug in ai_tags:
        updated_tags = [tag for tag in ai_tags if tag != normalized_slug]
        await tickets_repo.update_ticket(ticket_id, ai_tags=updated_tags)
        return {"success": True, "removed": normalized_slug, "remaining_tags": updated_tags}
    
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="Tag not found in ticket",
    )


@router.post("/knowledge-base/{article_id}/remove-tag", status_code=status.HTTP_200_OK)
async def remove_kb_article_tag(
    article_id: int,
    request: RemoveTagRequest,
    current_user: dict = Depends(require_super_admin),
) -> dict[str, Any]:
    """Remove a specific tag from a knowledge base article's AI tags."""
    article = await kb_repo.get_article_by_id(article_id)
    if not article:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Article not found",
        )
    
    ai_tags = article.get("ai_tags", [])
    if not isinstance(ai_tags, list):
        ai_tags = []
    
    normalized_slug = slugify_tag(request.tag_slug)
    if normalized_slug and normalized_slug in ai_tags:
        updated_tags = [tag for tag in ai_tags if tag != normalized_slug]
        await kb_repo.update_article(article_id, ai_tags=updated_tags)
        return {"success": True, "removed": normalized_slug, "remaining_tags": updated_tags}
    
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="Tag not found in article",
    )
