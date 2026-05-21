"""Help feature pack routes.

Provides:

* ``GET /help``                              – Help index listing all sections and articles.
* ``GET /help/{section_slug}/{article_slug}`` – Renders a single help article.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import HTMLResponse

from .service import find_article, list_sections, render_article


router = APIRouter(tags=["Help"])


def _main():
    from app import main as main_module
    return main_module


@router.get("/help", response_class=HTMLResponse)
async def help_index(request: Request):
    main_module = _main()
    user, redirect = await main_module._require_authenticated_user(request)
    if redirect:
        return redirect

    sections = list_sections()
    return await main_module._render_template(
        "help/index.html",
        request,
        user,
        extra={
            "title": "Help",
            "help_sections": sections,
        },
    )


@router.get("/help/{section_slug}/{article_slug}", response_class=HTMLResponse)
async def help_article(request: Request, section_slug: str, article_slug: str):
    main_module = _main()
    user, redirect = await main_module._require_authenticated_user(request)
    if redirect:
        return redirect

    article = find_article(section_slug, article_slug)
    if not article:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Article not found")

    content_html = render_article(article)
    sections = list_sections()

    return await main_module._render_template(
        "help/article.html",
        request,
        user,
        extra={
            "title": f"{article['name']} · Help",
            "help_article": article,
            "help_article_html": content_html,
            "help_sections": sections,
        },
    )
