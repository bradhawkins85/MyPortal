"""Knowledge Base page routes for the ``knowledge_base`` feature pack."""

from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.encoders import jsonable_encoder
from fastapi.responses import HTMLResponse, RedirectResponse

from app.services import knowledge_base as knowledge_base_service


router = APIRouter(tags=["Knowledge Base"])


def _main():
    from app import main as main_module

    return main_module


@router.get("/knowledge-base", response_class=HTMLResponse)
async def knowledge_base_index(request: Request, article: str | None = Query(None, alias="slug")):
    if article:
        target = f"/knowledge-base/articles/{quote(article, safe='')}"
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


@router.get("/admin/knowledge-base", response_class=HTMLResponse)
async def admin_knowledge_base_page(request: Request):
    main_module = _main()
    current_user, redirect = await main_module._require_super_admin_page(request)
    if redirect:
        return redirect

    access_context = await knowledge_base_service.build_access_context(current_user)
    articles = await knowledge_base_service.list_articles_for_context(
        access_context,
        include_unpublished=True,
        include_permissions=True,
    )
    extra = {
        "title": "Knowledge base admin",
        "kb_articles": jsonable_encoder(articles),
    }
    return await main_module._render_template(
        "admin/knowledge_base.html",
        request,
        current_user,
        extra=extra,
    )


@router.get("/admin/knowledge-base/new", response_class=HTMLResponse)
async def admin_new_knowledge_base_article_page(request: Request):
    main_module = _main()
    current_user, redirect = await main_module._require_super_admin_page(request)
    if redirect:
        return redirect

    user_options, company_options = await main_module._prepare_kb_editor_options()
    extra = {
        "title": "New knowledge base article",
        "kb_initial_article": None,
        "kb_user_options": user_options,
        "kb_company_options": company_options,
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
    serialised_article = jsonable_encoder(article)
    extra = {
        "title": f"Edit knowledge base article · {article.get('title') or article.get('slug')}",
        "kb_initial_article": serialised_article,
        "kb_user_options": user_options,
        "kb_company_options": company_options,
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
