from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query, Request, UploadFile, File, status

from app.api.dependencies.auth import get_current_session, get_current_user, get_optional_user, require_super_admin
from app.repositories import knowledge_base as kb_repo
from app.schemas.knowledge_base import (
    KnowledgeBaseArticleCreate,
    KnowledgeBaseFeedbackCreateRequest,
    KnowledgeBaseFeedbackCreateResponse,
    KnowledgeBaseArticleListItem,
    KnowledgeBaseArticleResponse,
    KnowledgeBaseArticleUpdate,
    KnowledgeBaseSearchRequest,
    KnowledgeBaseSearchResponse,
)
from app.security.session import SessionData
from app.services import audit as audit_service
from app.services import knowledge_base as kb_service
from app.services import file_storage
from app.services import tickets as tickets_service

router = APIRouter(prefix="/api/knowledge-base", tags=["Knowledge Base"])

# Define uploads path at module level
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
_PRIVATE_UPLOADS_PATH = _PROJECT_ROOT / "private_uploads"


# Knowledge base articles can contain very long HTML bodies; capturing only
# metadata + small fields keeps the audit log readable and avoids storing
# duplicate content alongside the source-of-truth article record.
_AUDIT_ARTICLE_FIELDS: tuple[str, ...] = (
    "id",
    "slug",
    "title",
    "summary",
    "permission_scope",
    "is_published",
    "ai_tags",
    "excluded_ai_tags",
    "allowed_user_ids",
    "allowed_company_ids",
    "company_admin_ids",
)


def _audit_article_summary(article: dict | None) -> dict | None:
    if not article:
        return None
    return {key: article.get(key) for key in _AUDIT_ARTICLE_FIELDS}


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
    request: Request,
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
    await audit_service.record(
        action="knowledge_base.article.create",
        request=request,
        user_id=author_id_int,
        entity_type="knowledge_base_article",
        entity_id=int(article["id"]) if article.get("id") is not None else None,
        before=None,
        after=_audit_article_summary(article),
    )
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
    request: Request,
    current_user: dict = Depends(require_super_admin),
) -> KnowledgeBaseArticleResponse:
    existing_article = await kb_repo.get_article_by_id(article_id)
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
    await audit_service.record(
        action="knowledge_base.article.update",
        request=request,
        user_id=int(current_user["id"]),
        entity_type="knowledge_base_article",
        entity_id=article_id,
        before=_audit_article_summary(existing_article),
        after=_audit_article_summary(article),
    )
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
    request: Request,
    current_user: dict = Depends(require_super_admin),
) -> None:
    existing_article = await kb_repo.get_article_by_id(article_id)
    await kb_service.delete_article(article_id)
    await audit_service.record(
        action="knowledge_base.article.delete",
        request=request,
        user_id=int(current_user["id"]),
        entity_type="knowledge_base_article",
        entity_id=article_id,
        before=_audit_article_summary(existing_article),
        after=None,
    )


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


@router.post(
    "/articles/{slug}/feedback",
    response_model=KnowledgeBaseFeedbackCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
async def submit_article_feedback(
    slug: str,
    payload: KnowledgeBaseFeedbackCreateRequest,
    request: Request,
    current_user: dict = Depends(get_current_user),
    session: SessionData = Depends(get_current_session),
) -> KnowledgeBaseFeedbackCreateResponse:
    access_context = await kb_service.build_access_context(current_user)
    article = await kb_service.get_article_by_slug_for_context(
        slug,
        access_context,
        include_unpublished=False,
        include_permissions=False,
    )
    if not article:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Article not found")

    user_id = int(current_user["id"])
    status_value = await tickets_service.resolve_status_or_default(None)
    rating_label = "Thumbs up" if payload.rating == "up" else "Thumbs down"
    article_title = article.get("title") or slug
    article_slug = article.get("slug") or slug
    feedback_text = (payload.feedback or "").strip()
    feedback_block = feedback_text or "(No additional comments provided.)"
    subject = f"Knowledge base feedback: {rating_label} · {article_title}"
    description = (
        f"Knowledge base feedback submitted by user {user_id}.\n"
        f"Article: {article_title}\n"
        f"Article URL: /knowledge-base/articles/{article_slug}\n"
        f"Rating: {rating_label}\n\n"
        f"Feedback:\n{feedback_block}"
    )

    ticket = await tickets_service.create_ticket(
        subject=subject,
        description=description,
        requester_id=user_id,
        company_id=session.active_company_id or current_user.get("company_id"),
        assigned_user_id=None,
        priority="normal",
        status=status_value,
        category="knowledge_base_feedback",
        module_slug="knowledge_base",
        external_reference=f"kb-feedback:{article_slug}:{payload.rating}",
        trigger_automations=True,
        initial_reply_author_id=user_id,
        requester_email=current_user.get("email"),
    )
    ticket_id = int(ticket["id"])
    await audit_service.record(
        action="knowledge_base.feedback.submit",
        request=request,
        user_id=user_id,
        entity_type="ticket",
        entity_id=ticket_id,
        before=None,
        after={
            "ticket_id": ticket_id,
            "article_slug": article_slug,
            "rating": payload.rating,
            "feedback_length": len(feedback_text),
        },
    )
    return KnowledgeBaseFeedbackCreateResponse(
        ticket_id=ticket_id,
        message="Thanks for the feedback. A ticket has been created for review.",
    )


@router.post("/upload-image")
async def upload_image(
    file: UploadFile = File(...),
    current_user: dict = Depends(require_super_admin),
) -> dict[str, str]:
    """Upload an image for use in knowledge base articles.
    
    Returns the URL of the uploaded image that can be used in article content.
    """
    try:
        image_url = await file_storage.store_knowledge_base_image(
            upload=file,
            uploads_root=_PRIVATE_UPLOADS_PATH,
        )
        return {"url": image_url}
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to upload image: {str(exc)}"
        ) from exc
