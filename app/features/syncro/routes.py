"""Syncro routes for the ``syncro`` feature pack."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import ValidationError

from app.core.config import get_settings
from app.core.logging import log_error, log_info
from app.schemas.tickets import SyncroTicketImportRequest
from app.services import background as background_tasks
from app.services import company_importer, modules as modules_service, staff_importer, ticket_importer


router = APIRouter(tags=["Syncro"])
settings = get_settings()


def _main():
    from app import main as main_module

    return main_module


def _parse_syncro_rate_limit(value: Any) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return 180
    return max(1, min(600, parsed))


async def _load_syncro_module() -> dict[str, Any] | None:
    try:
        return await modules_service.get_module("syncro", redact=False)
    except Exception as exc:  # pragma: no cover - defensive logging
        log_error("Failed to load Syncro module configuration", error=str(exc))
        return None


def _describe_syncro_module(module: dict[str, Any] | None) -> dict[str, Any]:
    settings_payload = (module or {}).get("settings") or {}
    base_url = str(settings_payload.get("base_url") or "").strip()
    api_key_present = bool(str(settings_payload.get("api_key") or "").strip())
    env_base_url = str(settings.syncro_webhook_url or "").strip()
    env_api_key = str(settings.syncro_api_key or "").strip()
    effective_base_url = (base_url or env_base_url).rstrip("/")
    return {
        "enabled": bool(module and module.get("enabled")),
        "base_url": base_url,
        "effective_base_url": effective_base_url,
        "has_api_key": api_key_present or bool(env_api_key),
        "rate_limit_per_minute": _parse_syncro_rate_limit(
            settings_payload.get("rate_limit_per_minute")
        ),
    }


async def _render_syncro_ticket_import(
    request: Request,
    user: dict[str, Any],
    *,
    success_message: str | None = None,
    error_message: str | None = None,
    status_code: int = status.HTTP_200_OK,
) -> HTMLResponse:
    module = await _load_syncro_module()
    module_description = _describe_syncro_module(module)
    if not module_description.get("enabled"):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Syncro ticket import is not available",
        )
    extra = {
        "title": "Syncro ticket import",
        "success_message": success_message,
        "error_message": error_message,
        "syncro_module": module_description,
    }
    response = await _main()._render_template(
        "admin/syncro_ticket_import.html",
        request,
        user,
        extra=extra,
    )
    response.status_code = status_code
    return response


@router.get("/admin/tickets/syncro-import", response_class=HTMLResponse)
async def admin_syncro_ticket_import_page(
    request: Request,
    success: str | None = Query(default=None),
    error: str | None = Query(default=None),
):
    main_module = _main()
    current_user, redirect = await main_module._require_helpdesk_page(request)
    if redirect:
        return redirect
    return await _render_syncro_ticket_import(
        request,
        current_user,
        success_message=main_module._sanitize_message(success),
        error_message=main_module._sanitize_message(error),
    )


@router.post("/admin/syncro/import-contacts")
async def route_import_syncro_contacts(request: Request):
    main_module = _main()
    (
        user,
        membership,
        company,
        staff_permission,
        company_id,
        redirect,
    ) = await main_module._load_staff_context(request, require_super_admin=True)
    if redirect:
        return redirect
    module = await _load_syncro_module()
    if not module or not module.get("enabled"):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Syncro module is disabled",
        )
    payload = await request.json()
    syncro_company_id = payload.get("syncroCompanyId") or payload.get("syncro_company_id")
    if not syncro_company_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="syncroCompanyId required",
        )
    summary = await staff_importer.import_contacts_for_syncro_id(str(syncro_company_id))
    return JSONResponse(
        {
            "success": True,
            "created": summary.created,
            "updated": summary.updated,
            "skipped": summary.skipped,
        }
    )


@router.post("/admin/syncro/import-companies")
async def route_import_syncro_companies(request: Request):
    current_user, redirect = await _main()._require_super_admin_page(request)
    if redirect:
        return redirect
    module = await _load_syncro_module()
    if not module or not module.get("enabled"):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Syncro module is disabled",
        )
    log_info(
        "Syncro company import admin request received",
        user_id=current_user.get("id"),
        request_path=str(request.url),
    )
    task_id = uuid4().hex

    def _on_success(summary: company_importer.CompanyImportSummary) -> None:
        summary_data = summary.as_dict()
        log_info(
            "Syncro company import background task completed",
            task_id=task_id,
            fetched=summary_data.get("fetched", 0),
            created=summary_data.get("created", 0),
            updated=summary_data.get("updated", 0),
            skipped=summary_data.get("skipped", 0),
        )

    async def _on_error(exc: Exception) -> None:
        log_error(
            "Syncro company import background task failed",
            task_id=task_id,
            error=str(exc),
        )

    background_tasks.queue_background_task(
        lambda: company_importer.import_all_companies(),
        task_id=task_id,
        description="syncro-company-import",
        on_complete=_on_success,
        on_error=_on_error,
    )

    log_info(
        "Syncro company import queued",
        task_id=task_id,
        user_id=current_user.get("id"),
        request_path=str(request.url),
    )

    if _main()._request_prefers_json(request):
        return JSONResponse(
            {
                "status": "queued",
                "taskId": task_id,
                "message": "Syncro company import queued.",
            },
            status_code=status.HTTP_202_ACCEPTED,
        )
    message = f"Syncro company import queued. Task ID: {task_id[:8]}"
    redirect_url = str(request.url_for("admin_modules_page"))
    if message:
        redirect_url = f"{redirect_url}?{urlencode({'success': message})}"
    redirect_url = f"{redirect_url}#module-syncro"
    return RedirectResponse(redirect_url, status_code=status.HTTP_303_SEE_OTHER)


@router.post("/admin/syncro/import-tickets")
async def route_import_syncro_tickets(request: Request):
    current_user, redirect = await _main()._require_super_admin_page(request)
    if redirect:
        return redirect
    module = await _load_syncro_module()
    if not module or not module.get("enabled"):
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Syncro module is disabled",
        )
    payload = await request.json()
    try:
        import_request = SyncroTicketImportRequest.model_validate(payload)
    except ValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=exc.errors(),
        ) from exc
    log_info(
        "Syncro ticket import admin request received",
        user_id=current_user.get("id"),
        mode=import_request.mode.value,
        ticket_id=import_request.ticket_id,
        start_id=import_request.start_id,
        end_id=import_request.end_id,
        request_path=str(request.url),
    )
    try:
        summary = await ticket_importer.import_from_request(
            mode=import_request.mode.value,
            ticket_id=import_request.ticket_id,
            start_id=import_request.start_id,
            end_id=import_request.end_id,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    return JSONResponse(summary.as_dict())


__all__ = ["router"]
