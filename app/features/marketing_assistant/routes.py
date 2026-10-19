"""Marketing Assistant routes.

The assistant page for each marketing page lists its source websites and
runs, and starts new runs; see :mod:`app.services.marketing_assistant`.
Access follows the marketing pages themselves (``marketing.access``).
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, Response

from app.features.marketing import routes as marketing_routes
from app.repositories import marketing as marketing_repo
from app.repositories import marketing_assistant as assistant_repo
from app.security.flash import flash_redirect
from app.services import audit as audit_service
from app.services import marketing_assistant as assistant_service
from app.services.component_availability import get_component_availability

router = APIRouter(tags=["Marketing Assistant"])

_MAX_INSTRUCTIONS_CHARS = 2000


async def _require_access(request: Request):
    """Marketing access, while the Marketing feature pack itself is available."""

    if not get_component_availability().feature_pack_available("marketing"):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Not found")
    return await marketing_routes._require_marketing_access(request)


def _assistant_url(page_id: int) -> str:
    return f"/admin/marketing/pages/{page_id}/assistant"


async def _page_or_404(page_id: int) -> dict[str, Any]:
    page = await marketing_repo.get_page_by_id(page_id)
    if not page:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Marketing page not found")
    return page


def split_urls(raw: str) -> list[str]:
    """Admin-entered addresses, one per line (commas and spaces also separate them)."""

    parts = [part.strip() for line in str(raw or "").splitlines() for part in line.replace(",", " ").split()]
    return list(dict.fromkeys(part for part in parts if part))


async def add_sources(page_id: int, raw: str) -> tuple[int, list[str]]:
    """Add each new, valid address to the page; returns ``(added, errors)``."""

    existing = {source["url"] for source in await assistant_repo.list_sources(page_id)}
    added = 0
    errors: list[str] = []
    for raw_url in split_urls(raw):
        try:
            url = await assistant_service.normalise_source_url(raw_url)
        except ValueError as exc:
            errors.append(str(exc))
            continue
        if url in existing:
            continue
        await assistant_repo.add_source(page_id, url)
        existing.add(url)
        added += 1
    return added, errors


@router.get("/admin/marketing/pages/{page_id}/assistant", response_class=HTMLResponse)
async def marketing_assistant_page(page_id: int, request: Request):
    current_user, redirect = await _require_access(request)
    if redirect:
        return redirect
    page = await _page_or_404(page_id)
    runs = await assistant_repo.list_runs(page_id)
    llm = assistant_service.build_llm_config()
    return await marketing_routes._main()._render_template(
        "admin/marketing_assistant.html",
        request,
        current_user,
        extra={
            "title": f"Marketing Assistant — {page['title']}",
            "marketing_page": page,
            "assistant_sources": await assistant_repo.list_sources(page_id),
            "assistant_runs": runs,
            "assistant_running": any(run["status"] == "running" for run in runs),
            "assistant_model": llm.get("model") or "",
            "assistant_max_sources": assistant_service.max_sources(),
            "assistant_min_sources_per_claim": assistant_service.min_sources_per_claim(),
            "assistant_has_generated": any(s.get("assistant_generated") for s in page.get("sections") or []),
        },
    )


@router.get("/admin/marketing/pages/{page_id}/assistant/status", response_class=JSONResponse)
async def marketing_assistant_status(page_id: int, request: Request):
    _, redirect = await _require_access(request)
    if redirect:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Sign in required")
    await _page_or_404(page_id)
    runs = await assistant_repo.list_runs(page_id, limit=1)
    latest = runs[0] if runs else None
    return {
        "running": bool(latest and latest["status"] == "running"),
        "latest_run": {"id": latest["id"], "status": latest["status"]} if latest else None,
    }


@router.post("/admin/marketing/pages/{page_id}/assistant/sources", response_class=HTMLResponse)
async def marketing_assistant_add_sources(page_id: int, request: Request):
    _, redirect = await _require_access(request)
    if redirect:
        return redirect
    await _page_or_404(page_id)
    form = await request.form()
    added, errors = await add_sources(page_id, str(form.get("urls") or ""))
    if errors:
        return flash_redirect(_assistant_url(page_id), " ".join(errors)[:900], "error")
    if not added:
        return flash_redirect(_assistant_url(page_id), "Those websites are already listed.", "info")
    return flash_redirect(_assistant_url(page_id), f"Added {added} source website(s).", "success")


@router.post("/admin/marketing/assistant/sources/{source_id}/delete", response_class=HTMLResponse)
async def marketing_assistant_delete_source(source_id: int, request: Request):
    _, redirect = await _require_access(request)
    if redirect:
        return redirect
    source = await assistant_repo.get_source(source_id)
    if not source:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Source not found")
    await assistant_repo.delete_source(source_id)
    return flash_redirect(_assistant_url(source["page_id"]), "Source website removed.", "success")


@router.post("/admin/marketing/pages/{page_id}/assistant/run", response_class=HTMLResponse)
async def marketing_assistant_run(page_id: int, request: Request):
    current_user, redirect = await _require_access(request)
    if redirect:
        return redirect
    await _page_or_404(page_id)
    form = await request.form()
    mode = str(form.get("mode") or "generate").strip().lower()
    instructions = str(form.get("instructions") or "").strip()[:_MAX_INSTRUCTIONS_CHARS] or None
    update_intro = marketing_routes._form_bool(form, "update_intro")
    try:
        run_id = await assistant_service.start_run(
            page_id,
            mode=mode,
            instructions=instructions,
            update_intro=update_intro,
            requested_by_user_id=int(current_user["id"]),
        )
    except ValueError as exc:
        return flash_redirect(_assistant_url(page_id), str(exc), "error")
    await audit_service.record(
        action="marketing.assistant.run",
        request=request,
        user_id=int(current_user["id"]),
        entity_type="marketing_page",
        entity_id=page_id,
        metadata={"run_id": run_id, "mode": mode},
    )
    return flash_redirect(
        _assistant_url(page_id),
        "The Marketing Assistant is reading the source websites. This page updates when it finishes.",
        "success",
    )


@router.post("/admin/marketing/assistant/runs/{run_id}/restore", response_class=HTMLResponse)
async def marketing_assistant_restore(run_id: int, request: Request):
    current_user, redirect = await _require_access(request)
    if redirect:
        return redirect
    run = await assistant_repo.get_run(run_id)
    if not run:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Run not found")
    try:
        page_id = await assistant_service.restore_run(run_id)
    except ValueError as exc:
        return flash_redirect(_assistant_url(run["page_id"]), str(exc), "error")
    await audit_service.record(
        action="marketing.assistant.restore",
        request=request,
        user_id=int(current_user["id"]),
        entity_type="marketing_page",
        entity_id=page_id,
        metadata={"run_id": run_id},
    )
    return flash_redirect(
        _assistant_url(page_id), "The page's assistant sections were put back as they were before that run.",
        "success",
    )


@router.get("/marketing/assistant-images/{image_id}")
async def marketing_assistant_image(image_id: int, request: Request):
    image = await assistant_repo.get_image(image_id)
    if not image or not get_component_availability().feature_pack_available("marketing"):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Image not found")
    public = image["page_published"] and await assistant_repo.image_in_use(image_id)
    if not public:
        _, redirect = await _require_access(request)
        if redirect:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Image not found")
    return Response(
        content=image["data"],
        media_type=image["content_type"],
        headers={
            "Cache-Control": "public, max-age=86400" if public else "private, no-store",
            "Content-Security-Policy": "default-src 'none'; sandbox",
        },
    )


__all__ = ["add_sources", "router", "split_urls"]
