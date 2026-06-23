"""TacticalRMM handlers for the ``tacticalrmm`` feature pack."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from urllib.parse import quote
from uuid import uuid4

from fastapi import Request, status
from fastapi.responses import RedirectResponse


def _main():
    from app import main as main_module

    return main_module


async def admin_push_companies_to_tactical_rmm(request: Request):
    from app.core.logging import log_error, log_info
    from app.services import background as background_tasks
    from app.services import modules as modules_service

    current_user, redirect = await _main()._require_super_admin_page(request)
    if redirect:
        return redirect

    try:
        await modules_service.ensure_tacticalrmm_ready()
    except ValueError as exc:
        log_error("Unable to synchronise Tactical RMM companies", error=str(exc))
        return await _main()._render_modules_dashboard(
            request,
            current_user,
            error_message=str(exc),
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    except Exception as exc:  # pragma: no cover - defensive logging
        log_error("Failed to synchronise Tactical RMM companies", error=str(exc))
        return await _main()._render_modules_dashboard(
            request,
            current_user,
            error_message="Unable to synchronise companies with Tactical RMM. Please verify the module configuration.",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    task_id = uuid4().hex

    async def _on_success(summary: Mapping[str, Any]) -> None:
        created_clients = summary.get("created_clients") or []
        created_sites = summary.get("created_sites") or []
        existing_clients = summary.get("existing_clients") or []
        skipped = summary.get("skipped") or []
        errors = summary.get("errors") or []
        processed = int(summary.get("processed_companies") or 0)

        site_created_count = len(created_sites)
        created_count = len(created_clients)
        existing_count = len(existing_clients)
        skipped_count = len(skipped)
        error_count = len(errors)

        log_info(
            "Tactical RMM company synchronisation completed",
            task_id=task_id,
            processed=processed,
            created_clients=created_count,
            site_creations=site_created_count,
            existing_clients=existing_count,
            skipped=skipped_count,
            errors=error_count,
        )

        if error_count:
            example = errors[0]
            detail = example.get("error") or "Unknown error"
            log_error(
                "Tactical RMM synchronisation encountered errors",
                task_id=task_id,
                error_count=error_count,
                example=detail,
            )

    async def _on_error(exc: Exception) -> None:
        log_error(
            "Tactical RMM company synchronisation failed",
            task_id=task_id,
            error=str(exc),
        )

    background_tasks.queue_background_task(
        lambda: modules_service.push_companies_to_tacticalrmm(),
        task_id=task_id,
        description="tacticalrmm-company-sync",
        on_complete=_on_success,
        on_error=_on_error,
    )

    log_info(
        "Queued Tactical RMM company synchronisation",
        task_id=task_id,
        user_id=current_user.get("id"),
        request_path=str(request.url),
    )

    success_message = (
        f"Tactical RMM company synchronisation queued. Task ID: {task_id[:8]}"
    )
    query = f"success={quote(success_message)}"
    redirect_url = f"/admin/modules?{query}" if query else "/admin/modules"

    return RedirectResponse(url=redirect_url, status_code=status.HTTP_303_SEE_OTHER)


async def admin_pull_companies_from_tactical_rmm(request: Request):
    from app.core.logging import log_error, log_info
    from app.services import background as background_tasks
    from app.services import modules as modules_service

    current_user, redirect = await _main()._require_super_admin_page(request)
    if redirect:
        return redirect

    try:
        await modules_service.ensure_tacticalrmm_ready()
    except ValueError as exc:
        log_error("Unable to pull Tactical RMM companies", error=str(exc))
        return await _main()._render_modules_dashboard(
            request,
            current_user,
            error_message=str(exc),
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    except Exception as exc:  # pragma: no cover - defensive logging
        log_error("Failed to pull Tactical RMM companies", error=str(exc))
        return await _main()._render_modules_dashboard(
            request,
            current_user,
            error_message="Unable to pull companies from Tactical RMM. Please verify the module configuration.",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    task_id = uuid4().hex

    async def _on_success(summary: Mapping[str, Any]) -> None:
        fetched = int(summary.get("fetched") or 0)
        created = int(summary.get("created") or 0)
        updated = int(summary.get("updated") or 0)
        skipped = int(summary.get("skipped") or 0)
        errors = summary.get("errors") or []
        error_count = len(errors)

        log_info(
            "Tactical RMM company pull completed",
            task_id=task_id,
            fetched=fetched,
            created=created,
            updated=updated,
            skipped=skipped,
            errors=error_count,
        )

        if error_count:
            example = errors[0]
            detail = example.get("error") or "Unknown error"
            log_error(
                "Tactical RMM pull encountered errors",
                task_id=task_id,
                error_count=error_count,
                example=detail,
            )

    async def _on_error(exc: Exception) -> None:
        log_error(
            "Tactical RMM company pull failed",
            task_id=task_id,
            error=str(exc),
        )

    background_tasks.queue_background_task(
        lambda: modules_service.pull_companies_from_tacticalrmm(),
        task_id=task_id,
        description="tacticalrmm-company-pull",
        on_complete=_on_success,
        on_error=_on_error,
    )

    log_info(
        "Queued Tactical RMM company pull",
        task_id=task_id,
        user_id=current_user.get("id"),
        request_path=str(request.url),
    )

    success_message = f"Tactical RMM company pull queued. Task ID: {task_id[:8]}"
    query = f"success={quote(success_message)}"
    redirect_url = f"/admin/modules?{query}" if query else "/admin/modules"

    return RedirectResponse(url=redirect_url, status_code=status.HTTP_303_SEE_OTHER)


async def admin_sync_tray_tokens_to_tactical_rmm(request: Request):
    from app.core.logging import log_error, log_info
    from app.services import background as background_tasks
    from app.services import modules as modules_service
    from app.services import tray as tray_service

    current_user, redirect = await _main()._require_super_admin_page(request)
    if redirect:
        return redirect

    try:
        await modules_service.ensure_tacticalrmm_ready()
    except ValueError as exc:
        log_error("Unable to sync tray tokens to Tactical RMM", error=str(exc))
        return await _main()._render_modules_dashboard(
            request,
            current_user,
            error_message=str(exc),
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    task_id = uuid4().hex

    async def _on_success(summary: Mapping[str, Any]) -> None:
        log_info(
            "Tactical RMM tray token sync completed",
            task_id=task_id,
            processed=summary.get("processed"),
            updated=summary.get("updated"),
            skipped=len(summary.get("skipped") or []),
            errors=len(summary.get("errors") or []),
        )

    async def _on_error(exc: Exception) -> None:
        log_error(
            "Tactical RMM tray token sync failed", task_id=task_id, error=str(exc)
        )

    user_id = int(current_user["id"]) if current_user.get("id") is not None else None
    background_tasks.queue_background_task(
        lambda: tray_service.sync_all_company_trmm_tray_tokens(
            created_by_user_id=user_id
        ),
        task_id=task_id,
        description="tacticalrmm-tray-token-sync",
        on_complete=_on_success,
        on_error=_on_error,
    )

    success_message = f"Tactical RMM tray token sync queued. Task ID: {task_id[:8]}"
    return RedirectResponse(
        url=f"/admin/modules?success={quote(success_message)}",
        status_code=status.HTTP_303_SEE_OTHER,
    )
