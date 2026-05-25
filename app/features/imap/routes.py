"""Admin IMAP mailbox routes for the ``imap`` feature pack."""

from __future__ import annotations

from functools import lru_cache
from typing import Any
from urllib.parse import quote

from fastapi import APIRouter, Query, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse

from app.core.logging import log_error
from app.repositories import companies as company_repo
from app.services import imap as imap_service

router = APIRouter(tags=["IMAP"])


@lru_cache(maxsize=1)
def _main():
    from app import main as main_module

    return main_module


async def _render_imap_dashboard(
    request: Request,
    user: dict[str, Any],
    *,
    editing_account_id: int | None = None,
    success_message: str | None = None,
    error_message: str | None = None,
    status_code: int = status.HTTP_200_OK,
) -> HTMLResponse:
    main_module = _main()
    accounts = await imap_service.list_accounts()
    editing_account = None
    if editing_account_id is not None:
        for account in accounts:
            if account.get("id") == editing_account_id:
                editing_account = account
                break
        if not editing_account:
            editing_account = await imap_service.get_account(editing_account_id)
    companies = await company_repo.list_companies()
    extra = {
        "title": "IMAP mailboxes",
        "accounts": accounts,
        "editing_account": editing_account,
        "companies": companies,
        "success_message": success_message,
        "error_message": error_message,
    }
    response = await main_module._render_template("admin/imap.html", request, user, extra=extra)
    response.status_code = status_code
    return response


@router.get("/admin/modules/imap", response_class=HTMLResponse)
async def admin_imap_accounts_page(
    request: Request,
    account_id: int | None = Query(default=None, alias="accountId"),
    success: str | None = Query(default=None),
    error: str | None = Query(default=None),
):
    main_module = _main()
    current_user, redirect = await main_module._require_super_admin_page(request)
    if redirect:
        return redirect
    return await _render_imap_dashboard(
        request,
        current_user,
        editing_account_id=account_id,
        success_message=main_module._sanitize_message(success),
        error_message=main_module._sanitize_message(error),
    )


@router.post("/admin/modules/imap/accounts", response_class=HTMLResponse)
async def admin_create_imap_account(request: Request):
    main_module = _main()
    current_user, redirect = await main_module._require_super_admin_page(request)
    if redirect:
        return redirect
    form = await request.form()
    data: dict[str, Any] = {
        "name": form.get("name", ""),
        "host": form.get("host", ""),
        "port": form.get("port", ""),
        "username": form.get("username", ""),
        "password": form.get("password", ""),
        "folder": form.get("folder", ""),
        "schedule_cron": form.get("scheduleCron", ""),
        "filter_query": form.get("filterQuery"),
        "process_unread_only": main_module._form_bool(form, "processUnreadOnly"),
        "mark_as_read": main_module._form_bool(form, "markAsRead"),
        "active": main_module._form_bool(form, "active"),
    }
    priority_value = form.get("priority")
    if priority_value not in (None, ""):
        try:
            data["priority"] = int(priority_value)
        except (TypeError, ValueError):
            return await _render_imap_dashboard(
                request,
                current_user,
                error_message="Priority must be a whole number.",
                status_code=status.HTTP_400_BAD_REQUEST,
            )
    company_id = form.get("companyId")
    if company_id not in (None, ""):
        try:
            data["company_id"] = int(company_id)
        except (TypeError, ValueError):
            return await _render_imap_dashboard(
                request,
                current_user,
                error_message="Company selection is invalid.",
                status_code=status.HTTP_400_BAD_REQUEST,
            )
    try:
        account = await imap_service.create_account(data)
    except ValueError as exc:
        return await _render_imap_dashboard(
            request,
            current_user,
            error_message=str(exc),
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    except Exception as exc:  # pragma: no cover - defensive logging
        log_error("Failed to create IMAP account", error=str(exc))
        return await _render_imap_dashboard(
            request,
            current_user,
            error_message="Unable to create the IMAP account. Please verify the configuration and try again.",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
    message = quote(f"Mailbox {account.get('name') or account.get('username') or 'created'} added.")
    return RedirectResponse(
        url=f"/admin/modules/imap?success={message}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/admin/modules/imap/accounts/{account_id}", response_class=HTMLResponse)
async def admin_update_imap_account(account_id: int, request: Request):
    main_module = _main()
    current_user, redirect = await main_module._require_super_admin_page(request)
    if redirect:
        return redirect
    form = await request.form()
    updates: dict[str, Any] = {}
    for field in ("name", "host", "port", "username"):
        if field in form:
            value = form.get(field)
            if value is None:
                continue
            if field == "port" and value == "":
                continue
            updates[field] = value
    password_value = form.get("password")
    if password_value:
        updates["password"] = password_value
    if "folder" in form:
        updates["folder"] = form.get("folder")
    if "scheduleCron" in form:
        updates["schedule_cron"] = form.get("scheduleCron")
    if "filterQuery" in form:
        updates["filter_query"] = form.get("filterQuery")
    updates["process_unread_only"] = main_module._form_bool(form, "processUnreadOnly")
    updates["mark_as_read"] = main_module._form_bool(form, "markAsRead")
    updates["active"] = main_module._form_bool(form, "active")
    priority_value = form.get("priority")
    if priority_value not in (None, ""):
        try:
            updates["priority"] = int(priority_value)
        except (TypeError, ValueError):
            return await _render_imap_dashboard(
                request,
                current_user,
                editing_account_id=account_id,
                error_message="Priority must be a whole number.",
                status_code=status.HTTP_400_BAD_REQUEST,
            )
    company_id = form.get("companyId")
    if company_id in (None, ""):
        updates["company_id"] = None
    else:
        try:
            updates["company_id"] = int(company_id)
        except (TypeError, ValueError):
            return await _render_imap_dashboard(
                request,
                current_user,
                editing_account_id=account_id,
                error_message="Company selection is invalid.",
                status_code=status.HTTP_400_BAD_REQUEST,
            )
    try:
        account = await imap_service.update_account(account_id, updates)
    except ValueError as exc:
        return await _render_imap_dashboard(
            request,
            current_user,
            editing_account_id=account_id,
            error_message=str(exc),
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    except Exception as exc:  # pragma: no cover - defensive logging
        log_error("Failed to update IMAP account", account_id=account_id, error=str(exc))
        return await _render_imap_dashboard(
            request,
            current_user,
            editing_account_id=account_id,
            error_message="Unable to update the IMAP account. Please review the settings and try again.",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
    message = quote(f"Mailbox {account.get('name') or account.get('username') or account_id} updated.")
    return RedirectResponse(
        url=f"/admin/modules/imap?success={message}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/admin/modules/imap/accounts/{account_id}/clone", response_class=HTMLResponse)
async def admin_clone_imap_account(account_id: int, request: Request):
    main_module = _main()
    current_user, redirect = await main_module._require_super_admin_page(request)
    if redirect:
        return redirect
    try:
        account = await imap_service.clone_account(account_id)
    except LookupError as exc:
        return await _render_imap_dashboard(
            request,
            current_user,
            error_message=str(exc),
            status_code=status.HTTP_404_NOT_FOUND,
        )
    except ValueError as exc:
        return await _render_imap_dashboard(
            request,
            current_user,
            error_message=str(exc),
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    except Exception as exc:  # pragma: no cover - defensive logging
        log_error("Failed to clone IMAP account", account_id=account_id, error=str(exc))
        return await _render_imap_dashboard(
            request,
            current_user,
            error_message="Unable to clone the IMAP account at this time.",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
    label = account.get("name") or f"Mailbox {account_id} copy"
    message = quote(f"Mailbox {label} cloned.")
    return RedirectResponse(
        url=f"/admin/modules/imap?success={message}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/admin/modules/imap/accounts/{account_id}/delete", response_class=HTMLResponse)
async def admin_delete_imap_account(account_id: int, request: Request):
    main_module = _main()
    current_user, redirect = await main_module._require_super_admin_page(request)
    if redirect:
        return redirect
    account = await imap_service.get_account(account_id)
    try:
        await imap_service.delete_account(account_id)
    except Exception as exc:  # pragma: no cover - defensive logging
        log_error("Failed to delete IMAP account", account_id=account_id, error=str(exc))
        return await _render_imap_dashboard(
            request,
            current_user,
            error_message="Unable to delete the IMAP account at this time.",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
    label = account.get("name") if account else f"#{account_id}"
    message = quote(f"Mailbox {label} deleted.")
    return RedirectResponse(
        url=f"/admin/modules/imap?success={message}",
        status_code=status.HTTP_303_SEE_OTHER,
    )


@router.post("/admin/modules/imap/accounts/{account_id}/sync", response_class=HTMLResponse)
async def admin_sync_imap_account(account_id: int, request: Request):
    main_module = _main()
    current_user, redirect = await main_module._require_super_admin_page(request)
    if redirect:
        return redirect
    result = await imap_service.sync_account(account_id)
    status_value = str(result.get("status") or "").lower()
    processed = int(result.get("processed") or 0)
    error_count = len(result.get("errors") or [])
    if status_value in {"error"}:
        message = result.get("error") or "IMAP synchronisation failed."
        return RedirectResponse(
            url=f"/admin/modules/imap?error={quote(message)}",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    if status_value == "skipped":
        message = result.get("reason") or "IMAP synchronisation skipped."
        return RedirectResponse(
            url=f"/admin/modules/imap?error={quote(message)}",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    if status_value == "completed_with_errors" and error_count:
        message = quote(
            f"IMAP sync completed with {error_count} issue{'s' if error_count != 1 else ''}. Imported {processed} messages."
        )
        return RedirectResponse(
            url=f"/admin/modules/imap?success={message}",
            status_code=status.HTTP_303_SEE_OTHER,
        )
    message = quote(f"IMAP sync imported {processed} message{'s' if processed != 1 else ''}.")
    return RedirectResponse(
        url=f"/admin/modules/imap?success={message}",
        status_code=status.HTTP_303_SEE_OTHER,
    )
__all__ = ["router"]
