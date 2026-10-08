"""Knowledge Base page routes for the ``knowledge_base`` feature pack."""

from __future__ import annotations

import asyncio
import re
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import HTMLResponse, RedirectResponse, Response

from app.core.logging import log_error
from app.services import knowledge_base as knowledge_base_service
from app.services import audit as audit_service
from app.services import rag_relationships as rag_relationships_service
from app.repositories import assets as asset_repo
from app.repositories import roles as role_repo
from app.repositories import customer_content_audience as audience_repo


router = APIRouter(tags=["Knowledge Base"])

# Backstop so the admin KB page can never hold a worker forever if a downstream
# database or Redis call stalls (e.g. an exhausted connection pool or a
# half-open socket). Individual calls are already bounded by the DB pool and
# Redis timeouts; this is the final page-level guard.
KB_PAGE_TIMEOUT = 30.0
KB_REVIEW_SIGNALS_TIMEOUT = 2.0


def _main():
    from app import main as main_module

    return main_module


def _write_pdf(html: str, base_url: str) -> bytes:
    try:
        from weasyprint import HTML  # type: ignore
    except (ImportError, OSError) as exc:  # pragma: no cover - environment dependent
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="PDF export is temporarily unavailable.",
        ) from exc

    return HTML(string=html, base_url=base_url).write_pdf()


@router.get("/knowledge-base", response_class=HTMLResponse)
async def knowledge_base_index(request: Request, article: str | None = Query(None, alias="slug")):
    if article:
        target = str(request.url_for("knowledge_base_article", slug=article))
        return RedirectResponse(url=target, status_code=status.HTTP_307_TEMPORARY_REDIRECT)

    main_module = _main()
    user, _ = await main_module._get_optional_user(request)
    access_context = await knowledge_base_service.build_access_context(user)
    include_unpublished = bool(user and user.get("is_super_admin"))
    articles = await knowledge_base_service.list_articles_for_context(
        access_context,
        include_unpublished=include_unpublished,
    )
    extra_context = {
        "title": "Knowledge base",
        "kb_articles": articles,
        "kb_is_super_admin": bool(user and user.get("is_super_admin")),
    }
    context = await main_module._build_portal_context(request, user, extra=extra_context)
    return main_module.templates.TemplateResponse(
        context["request"],
        "knowledge_base/index.html",
        context,
    )


@router.get("/knowledge-base/articles/{slug}", response_class=HTMLResponse)
async def knowledge_base_article(request: Request, slug: str):
    main_module = _main()
    user, _ = await main_module._get_optional_user(request)
    access_context = await knowledge_base_service.build_access_context(user)
    include_unpublished = bool(user and user.get("is_super_admin"))
    article = await knowledge_base_service.get_article_by_slug_for_context(
        slug,
        access_context,
        include_unpublished=include_unpublished,
        include_permissions=include_unpublished,
    )
    if not article:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Article not found")
    extra_context = {
        "title": article.get("title") or "Knowledge base",
        "kb_article": article,
        "kb_is_super_admin": bool(user and user.get("is_super_admin")),
    }
    context = await main_module._build_portal_context(request, user, extra=extra_context)
    return main_module.templates.TemplateResponse(
        context["request"],
        "knowledge_base/article.html",
        context,
    )


@router.get(
    "/knowledge-base/articles/{slug}/export.pdf",
    response_class=Response,
    name="knowledge_base_article_pdf",
)
async def knowledge_base_article_pdf(request: Request, slug: str) -> Response:
    """Download an accessible knowledge base article as a PDF."""

    main_module = _main()
    user, _ = await main_module._get_optional_user(request)
    access_context = await knowledge_base_service.build_access_context(user)
    include_unpublished = bool(user and user.get("is_super_admin"))
    article = await knowledge_base_service.get_article_by_slug_for_context(
        slug,
        access_context,
        include_unpublished=include_unpublished,
        include_permissions=include_unpublished,
    )
    if not article:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Article not found")

    template = main_module.templates.get_template("knowledge_base/article_pdf.html")
    rendered_html = template.render(
        kb_article=article,
        generated_at=datetime.now(timezone.utc),
    )
    pdf_bytes = _write_pdf(rendered_html, str(request.base_url))
    company_id = next(iter(access_context.memberships), None)
    await audit_service.record(
        action="knowledge_base.article.export", request=request,
        user_id=int(user["id"]) if user and user.get("id") is not None else None,
        entity_type="knowledge_base_article", entity_id=int(article["id"]),
        after={"format": "pdf", "company_id": company_id},
        metadata={"company_id": company_id} if company_id is not None else {},
        actor="authenticated_user" if user else "anonymous",
    )
    safe_slug = re.sub(r"[^A-Za-z0-9_-]+", "-", slug).strip("-") or "article"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{safe_slug}.pdf"'},
    )


@router.get("/admin/knowledge-base", response_class=HTMLResponse)
async def admin_knowledge_base_page(request: Request):
    main_module = _main()
    current_user, redirect = await main_module._require_super_admin_page(request)
    if redirect:
        return redirect

    async def _render_page():
        articles = await knowledge_base_service.list_admin_article_summaries(current_user)
        now = datetime.now(timezone.utc)
        review_signals_unavailable = False
        try:
            update_signals = await asyncio.wait_for(
                rag_relationships_service.kb_articles_needing_update(),
                timeout=KB_REVIEW_SIGNALS_TIMEOUT,
            )
        except Exception as exc:
            log_error("Knowledge base review signals unavailable", error=str(exc))
            update_signals = {}
            review_signals_unavailable = True
        counts = {
            "all": len(articles), "published": 0, "draft": 0, "in_review": 0,
            "retired": 0, "overdue": 0, "needs_update": 0,
        }
        for article in articles:
            lifecycle = "published" if article.get("is_published") else str(article.get("lifecycle_status") or "draft")
            if lifecycle == "published" and not article.get("is_published"):
                lifecycle = "draft"
            counts[lifecycle] = counts.get(lifecycle, 0) + 1
            review_due = article.get("review_due_at")
            if review_due is not None and review_due.tzinfo is None:
                review_due = review_due.replace(tzinfo=timezone.utc)
            article["review_overdue"] = bool(review_due and review_due < now and lifecycle != "retired")
            counts["overdue"] += int(article["review_overdue"])
            signal = update_signals.get(int(article["id"])) if lifecycle != "retired" else None
            article["needs_update"] = signal
            counts["needs_update"] += int(bool(signal))
        extra = {
            "title": "Knowledge base admin",
            "kb_articles": jsonable_encoder(articles),
            "kb_status_counts": counts,
            "kb_review_signals_unavailable": review_signals_unavailable,
        }
        return await main_module._render_template(
            "admin/knowledge_base.html",
            request,
            current_user,
            extra=extra,
        )

    try:
        return await asyncio.wait_for(_render_page(), timeout=KB_PAGE_TIMEOUT)
    except asyncio.TimeoutError:
        log_error(
            "Knowledge base admin page timed out while loading",
            error=f"exceeded {KB_PAGE_TIMEOUT:g}s page backstop",
        )
        raise HTTPException(
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
            detail="The knowledge base took too long to load. Please try again.",
        )


@router.get("/admin/knowledge-base/new", response_class=HTMLResponse)
async def admin_new_knowledge_base_article_page(request: Request):
    main_module = _main()
    current_user, redirect = await main_module._require_super_admin_page(request)
    if redirect:
        return redirect

    user_options, company_options = await main_module._prepare_kb_editor_options()
    asset_options = await asset_repo.list_assets_for_knowledge_base_editor()
    role_options = await role_repo.list_roles()
    extra = {
        "title": "New knowledge base article",
        "kb_initial_article": None,
        "kb_user_options": user_options,
        "kb_company_options": company_options,
        "kb_asset_options": jsonable_encoder(asset_options),
        "kb_role_options": jsonable_encoder(role_options),
        "kb_form_mode": "create",
        "kb_catalogue_payload": [],
    }
    return await main_module._render_template(
        "admin/knowledge_base_editor.html",
        request,
        current_user,
        extra=extra,
    )


@router.get("/admin/knowledge-base/articles/{slug}", response_class=HTMLResponse)
async def admin_edit_knowledge_base_article_page(request: Request, slug: str):
    main_module = _main()
    current_user, redirect = await main_module._require_super_admin_page(request)
    if redirect:
        return redirect

    access_context = await knowledge_base_service.build_access_context(current_user)
    article = await knowledge_base_service.get_article_by_slug_for_context(
        slug,
        access_context,
        include_unpublished=True,
        include_permissions=True,
    )
    if not article:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Article not found")

    user_options, company_options = await main_module._prepare_kb_editor_options()
    asset_options = await asset_repo.list_assets_for_knowledge_base_editor()
    role_options = await role_repo.list_roles()
    serialised_article = jsonable_encoder(article)
    role_ids: set[int] = set()
    for company_id in serialised_article.get("allowed_company_ids", []):
        role_ids.update(await audience_repo.list_role_ids(int(company_id), "knowledge_base", int(article["id"])))
    serialised_article["allowed_role_ids"] = sorted(role_ids)
    extra = {
        "title": f"Edit knowledge base article · {article.get('title') or article.get('slug')}",
        "kb_initial_article": serialised_article,
        "kb_user_options": user_options,
        "kb_company_options": company_options,
        "kb_asset_options": jsonable_encoder(asset_options),
        "kb_role_options": jsonable_encoder(role_options),
        "kb_form_mode": "edit",
        "kb_catalogue_payload": [{"slug": serialised_article.get("slug")}],
    }
    return await main_module._render_template(
        "admin/knowledge_base_editor.html",
        request,
        current_user,
        extra=extra,
    )


__all__ = ["router"]
