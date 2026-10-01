from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from app.api.dependencies.auth import require_super_admin
from app.repositories import tag_synonyms as tag_synonyms_repo
from app.services import tagging

router = APIRouter(prefix="/api/tag-synonyms", tags=["Tag Synonyms"])


class TagSynonymCreate(BaseModel):
    variant: str = Field(..., min_length=1, max_length=80, description="Variant tag to merge")
    canonical: str = Field(..., min_length=1, max_length=80, description="Canonical tag to keep")
    apply_to_existing: bool = Field(
        True, description="Also rewrite the variant on existing tickets and articles"
    )


def _serialise(row: dict[str, Any]) -> dict[str, Any]:
    created_at = row.get("created_at")
    return {
        "id": row.get("id"),
        "variant_slug": row.get("variant_slug"),
        "canonical_slug": row.get("canonical_slug"),
        "created_at": created_at.isoformat() if hasattr(created_at, "isoformat") else "",
        "created_by": row.get("created_by"),
    }


def _user_id(current_user: dict) -> int | None:
    try:
        return int(current_user.get("id")) if current_user.get("id") else None
    except (TypeError, ValueError):
        return None


@router.get("")
async def list_tag_synonyms(
    current_user: dict = Depends(require_super_admin),
) -> dict[str, Any]:
    """List tag synonyms and the preferred tags currently offered to the AI."""

    synonyms = await tag_synonyms_repo.list_synonyms()
    return {
        "synonyms": [_serialise(row) for row in synonyms],
        "preferred_tags": await tagging.get_preferred_tags(),
    }


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_tag_synonym(
    request: TagSynonymCreate,
    current_user: dict = Depends(require_super_admin),
) -> dict[str, Any]:
    """Merge a variant tag into a canonical tag."""

    variant = tagging.slugify_tag(request.variant)
    canonical = tagging.slugify_tag(request.canonical)
    if not variant or not canonical:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Invalid tag")

    synonyms = await tag_synonyms_repo.get_synonym_map()
    # Always point at the end of an existing chain so lookups stay one hop.
    canonical = tagging.canonicalise_slug(canonical, synonyms)
    if variant == canonical:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="The variant and canonical tag must be different",
        )
    if variant in synonyms:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=f"'{variant}' is already merged into '{synonyms[variant]}'",
        )

    created = await tag_synonyms_repo.add_synonym(variant, canonical, _user_id(current_user))
    # Tags that were merged into the new variant now follow it to the canonical tag.
    await tag_synonyms_repo.repoint_canonical(variant, canonical)
    tagging.clear_tag_vocabulary_cache()

    merged = {"tickets_updated": 0, "articles_updated": 0}
    if request.apply_to_existing:
        merged = await tagging.merge_existing_tags(variant, canonical)
    return {**_serialise(created), **merged}


@router.delete("/{variant_slug}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_tag_synonym(
    variant_slug: str,
    current_user: dict = Depends(require_super_admin),
) -> None:
    """Stop merging a variant tag. Tags already rewritten are left as they are."""

    if not await tag_synonyms_repo.delete_synonym(variant_slug):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Tag synonym not found")
    tagging.clear_tag_vocabulary_cache()
