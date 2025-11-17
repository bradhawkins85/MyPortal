from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from app.api.dependencies.auth import get_optional_user, require_super_admin
from app.schemas.knowledge_base import (
    KnowledgeBaseArticleCreate,
    KnowledgeBaseArticleListItem,
    KnowledgeBaseArticleResponse,
    KnowledgeBaseArticleUpdate,
    KnowledgeBaseSearchRequest,
    KnowledgeBaseSearchResponse,
)
from app.services import knowledge_base as kb_service

router = APIRouter(prefix="/api/knowledge-base", tags=["Knowledge Base"])


async def get_access_context(
    optional_user: dict | None = Depends(get_optional_user),
) -> kb_service.ArticleAccessContext:
    return await kb_service.build_access_context(optional_user)


@router.get("/articles", response_model=list[KnowledgeBaseArticleListItem])
async def list_articles(
    include_unpublished: bool = Query(
        False,
        description="Include unpublished drafts (super admin only).",
    ),
    context: kb_service.ArticleAccessContext = Depends(get_access_context),
    current_user: dict | None = Depends(get_optional_user),
) -> list[KnowledgeBaseArticleListItem]:
    if include_unpublished and not (current_user and current_user.get("is_super_admin")):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Super admin privileges required")
    articles = await kb_service.list_articles_for_context(
        context,
        include_unpublished=include_unpublished,
    )
    items: list[KnowledgeBaseArticleListItem] = []
    for article in articles:
        items.append(
            KnowledgeBaseArticleListItem(
                id=article["id"],
                slug=article["slug"],
                title=article["title"],
                summary=article.get("summary"),
                permission_scope=article["permission_scope"],
                is_published=article["is_published"],
                ai_tags=article.get("ai_tags", []),
                updated_at=article.get("updated_at"),
                updated_at_iso=article.get("updated_at_iso"),
                published_at_iso=article.get("published_at_iso"),
            )
        )
    return items


@router.get("/articles/{slug}", response_model=KnowledgeBaseArticleResponse)
async def get_article(
    slug: str,
    include_permissions: bool = Query(
        False,
        description="Include assignment metadata (super admin only).",
    ),
    context: kb_service.ArticleAccessContext = Depends(get_access_context),
    current_user: dict | None = Depends(get_optional_user),
) -> KnowledgeBaseArticleResponse:
    if include_permissions and not (current_user and current_user.get("is_super_admin")):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Super admin privileges required")
    article = await kb_service.get_article_by_slug_for_context(
        slug,
        context,
        include_unpublished=include_permissions,
        include_permissions=include_permissions,
    )
    if not article:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Article not found")
    return KnowledgeBaseArticleResponse(
        id=article["id"],
        slug=article["slug"],
        title=article["title"],
        summary=article.get("summary"),
        content=article.get("content", ""),
        sections=article.get("sections", []),
        permission_scope=article["permission_scope"],
        is_published=article["is_published"],
        ai_tags=article.get("ai_tags", []),
        excluded_ai_tags=article.get("excluded_ai_tags", []),
        allowed_user_ids=article.get("allowed_user_ids", []),
        allowed_company_ids=article.get("allowed_company_ids", []),
        company_admin_ids=article.get("company_admin_ids", []),
        conditional_companies=article.get("conditional_companies", []),
        created_by=article.get("created_by"),
        created_at=article.get("created_at"),
        updated_at=article.get("updated_at"),
        published_at=article.get("published_at"),
    )


@router.post("/articles", response_model=KnowledgeBaseArticleResponse, status_code=status.HTTP_201_CREATED)
async def create_article(
    payload: KnowledgeBaseArticleCreate,
    current_user: dict = Depends(require_super_admin),
) -> KnowledgeBaseArticleResponse:
    author_id = current_user.get("id")
    try:
        author_id_int = int(author_id)
    except (TypeError, ValueError):
        author_id_int = None
    try:
        created = await kb_service.create_article(payload.dict(), author_id=author_id_int)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    context = await kb_service.build_access_context(current_user)
    article = await kb_service.get_article_by_slug_for_context(
        created.get("slug"),
        context,
        include_unpublished=True,
        include_permissions=True,
    )
    if not article:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Failed to load created article")
    return KnowledgeBaseArticleResponse(
        id=article["id"],
        slug=article["slug"],
        title=article["title"],
        summary=article.get("summary"),
        content=article.get("content", ""),
        sections=article.get("sections", []),
        permission_scope=article["permission_scope"],
        is_published=article["is_published"],
        ai_tags=article.get("ai_tags", []),
        excluded_ai_tags=article.get("excluded_ai_tags", []),
        allowed_user_ids=article.get("allowed_user_ids", []),
        allowed_company_ids=article.get("allowed_company_ids", []),
        company_admin_ids=article.get("company_admin_ids", []),
        conditional_companies=article.get("conditional_companies", []),
        created_by=article.get("created_by"),
        created_at=article.get("created_at"),
        updated_at=article.get("updated_at"),
        published_at=article.get("published_at"),
    )


@router.put("/articles/{article_id}", response_model=KnowledgeBaseArticleResponse)
async def update_article(
    article_id: int,
    payload: KnowledgeBaseArticleUpdate,
    current_user: dict = Depends(require_super_admin),
) -> KnowledgeBaseArticleResponse:
    try:
        updated = await kb_service.update_article(article_id, payload.dict(exclude_unset=True))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    context = await kb_service.build_access_context(current_user)
    article = await kb_service.get_article_by_slug_for_context(
        updated.get("slug"),
        context,
        include_unpublished=True,
        include_permissions=True,
    )
    if not article:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Article not found")
    return KnowledgeBaseArticleResponse(
        id=article["id"],
        slug=article["slug"],
        title=article["title"],
        summary=article.get("summary"),
        content=article.get("content", ""),
        sections=article.get("sections", []),
        permission_scope=article["permission_scope"],
        is_published=article["is_published"],
        ai_tags=article.get("ai_tags", []),
        excluded_ai_tags=article.get("excluded_ai_tags", []),
        allowed_user_ids=article.get("allowed_user_ids", []),
        allowed_company_ids=article.get("allowed_company_ids", []),
        company_admin_ids=article.get("company_admin_ids", []),
        conditional_companies=article.get("conditional_companies", []),
        created_by=article.get("created_by"),
        created_at=article.get("created_at"),
        updated_at=article.get("updated_at"),
        published_at=article.get("published_at"),
    )


@router.delete("/articles/{article_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_article(
    article_id: int,
    current_user: dict = Depends(require_super_admin),
) -> None:
    await kb_service.delete_article(article_id)


@router.post("/articles/{article_id}/refresh-ai-tags", status_code=status.HTTP_200_OK)
async def refresh_article_ai_tags(
    article_id: int,
    current_user: dict = Depends(require_super_admin),
) -> dict[str, str]:
    """Refresh the AI-generated tags for an article."""
    await kb_service.refresh_article_ai_tags(article_id)
    return {"status": "queued", "message": "AI tag refresh has been queued"}


@router.post("/search", response_model=KnowledgeBaseSearchResponse)
async def search_articles(
    payload: KnowledgeBaseSearchRequest,
    context: kb_service.ArticleAccessContext = Depends(get_access_context),
) -> KnowledgeBaseSearchResponse:
    result = await kb_service.search_articles(payload.query, context)
    return KnowledgeBaseSearchResponse(**result)

