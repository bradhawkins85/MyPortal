"""Admin M365 mailbox routes for the ``m365_mail`` feature pack."""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

from fastapi import APIRouter, Query, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse

from app.core.logging import log_error
from app.security.flash import flash_redirect
from app.repositories import companies as company_repo
from app.services import m365 as m365_service
from app.services import m365_mail as m365_mail_service

__all__ = ["router"]

router = APIRouter(tags=["Office365 Mail"])


def _main():
    from app import main as main_module

    return main_module


def _form_bool(form: Any, key: str) -> bool:
    value = form.get(key)
    if value is None:
        return False
    if isinstance(value, str):
        return value.strip().lower() not in {"", "0", "false", "off"}
    return bool(value)


async def _render_m365_mail_dashboard(
    request: Request,
    user: dict[str, Any],
    *,
    editing_account_id: int | None = None,
    success_message: str | None = None,
    error_message: str | None = None,
    sync_result: dict[str, Any] | None = None,
    status_code: int = status.HTTP_200_OK,
) -> HTMLResponse:
    main_module = _main()
    accounts = await m365_mail_service.list_accounts()
    editing_account = None
    if editing_account_id is not None:
        for account in accounts:
            if account.get("id") == editing_account_id:
                editing_account = account
                break
        if not editing_account:
            editing_account = await m365_mail_service.get_account(editing_account_id)
    companies = await company_repo.list_companies()
    extra = {
        "title": "Office 365 mailboxes",
        "accounts": accounts,
        "editing_account": editing_account,
        "companies": companies,
        "success_message": success_message,
        "error_message": error_message,
        "sync_result": sync_result,
    }
    response = await main_module._render_template("admin/m365_mail.html", request, user, extra=extra)
    response.status_code = status_code
    return response


@router.get("/admin/modules/m365-mail", response_class=HTMLResponse)
async def admin_m365_mail_accounts_page(
    request: Request,
    account_id: int | None = Query(default=None, alias="accountId"),
):
    main_module = _main()
    current_user, redirect = await main_module._require_super_admin_page(request)
    if redirect:
        return redirect
    return await _render_m365_mail_dashboard(
        request,
        current_user,
        editing_account_id=account_id,
    )


@router.post("/admin/modules/m365-mail/accounts", response_class=HTMLResponse)
async def admin_create_m365_mail_account(request: Request):
    main_module = _main()
    current_user, redirect = await main_module._require_super_admin_page(request)
    if redirect:
        return redirect
    form = await request.form()
    data: dict[str, Any] = {
        "name": form.get("name", ""),
        "user_principal_name": form.get("userPrincipalName", ""),
        "mailbox_type": form.get("mailboxType", "user"),
        "folder": form.get("folder", ""),
        "schedule_cron": form.get("scheduleCron", ""),
        "filter_query": form.get("filterQuery"),
        "process_unread_only": _form_bool(form, "processUnreadOnly"),
        "mark_as_read": _form_bool(form, "markAsRead"),
        "sync_known_only": _form_bool(form, "syncKnownOnly"),
        "active": _form_bool(form, "active"),
    }
    priority_value = form.get("priority")
    if priority_value not in (None, ""):
        try:
            data["priority"] = int(priority_value)
        except (TypeError, ValueError):
            return await _render_m365_mail_dashboard(
                request,
                current_user,
                success_message=None,
                error_message="Priority must be a whole number.",
                status_code=status.HTTP_400_BAD_REQUEST,
            )
    company_id = form.get("companyId")
    if company_id not in (None, ""):
        try:
            data["company_id"] = int(company_id)
        except (TypeError, ValueError):
            return await _render_m365_mail_dashboard(
                request,
                current_user,
                success_message=None,
                error_message="Company selection is invalid.",
                status_code=status.HTTP_400_BAD_REQUEST,
            )
    try:
        account = await m365_mail_service.create_account(data)
    except ValueError as exc:
        return await _render_m365_mail_dashboard(
            request,
            current_user,
            success_message=None,
            error_message=str(exc),
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    except Exception as exc:  # pragma: no cover - defensive logging
        log_error("Failed to create M365 mail account", error=str(exc))
        return await _render_m365_mail_dashboard(
            request,
            current_user,
            success_message=None,
            error_message="Unable to create the Office 365 mail account. Please verify the configuration and try again.",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
    message = f"Mailbox {account.get('name') or account.get('user_principal_name') or 'created'} added."
    return flash_redirect("/admin/modules/m365-mail", message, "success")


@router.post("/admin/modules/m365-mail/accounts/{account_id}", response_class=HTMLResponse)
async def admin_update_m365_mail_account(account_id: int, request: Request):
    current_user, redirect = await _main()._require_super_admin_page(request)
    if redirect:
        return redirect
    form = await request.form()
    updates: dict[str, Any] = {}
    for field in ("name", "userPrincipalName", "mailboxType"):
        if field in form:
            value = form.get(field)
            if value is None:
                continue
            if field == "userPrincipalName":
                updates["user_principal_name"] = value
            elif field == "mailboxType":
                updates["mailbox_type"] = value
            else:
                updates[field] = value
    if "folder" in form:
        updates["folder"] = form.get("folder")
    if "scheduleCron" in form:
        updates["schedule_cron"] = form.get("scheduleCron")
    if "filterQuery" in form:
        updates["filter_query"] = form.get("filterQuery")
    updates["process_unread_only"] = _form_bool(form, "processUnreadOnly")
    updates["mark_as_read"] = _form_bool(form, "markAsRead")
    updates["sync_known_only"] = _form_bool(form, "syncKnownOnly")
    updates["active"] = _form_bool(form, "active")
    priority_value = form.get("priority")
    if priority_value not in (None, ""):
        try:
            updates["priority"] = int(priority_value)
        except (TypeError, ValueError):
            return await _render_m365_mail_dashboard(
                request,
                current_user,
                editing_account_id=account_id,
                success_message=None,
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
            return await _render_m365_mail_dashboard(
                request,
                current_user,
                editing_account_id=account_id,
                success_message=None,
                error_message="Company selection is invalid.",
                status_code=status.HTTP_400_BAD_REQUEST,
            )
    try:
        account = await m365_mail_service.update_account(account_id, updates)
    except ValueError as exc:
        return await _render_m365_mail_dashboard(
            request,
            current_user,
            editing_account_id=account_id,
            success_message=None,
            error_message=str(exc),
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    except Exception as exc:  # pragma: no cover - defensive logging
        log_error("Failed to update M365 mail account", account_id=account_id, error=str(exc))
        return await _render_m365_mail_dashboard(
            request,
            current_user,
            editing_account_id=account_id,
            success_message=None,
            error_message="Unable to update the Office 365 mail account. Please review the settings and try again.",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
    message = f"Mailbox {account.get('name') or account.get('user_principal_name') or account_id} updated."
    return flash_redirect("/admin/modules/m365-mail", message, "success")


@router.post("/admin/modules/m365-mail/accounts/{account_id}/clone", response_class=HTMLResponse)
async def admin_clone_m365_mail_account(account_id: int, request: Request):
    current_user, redirect = await _main()._require_super_admin_page(request)
    if redirect:
        return redirect
    try:
        account = await m365_mail_service.clone_account(account_id)
    except LookupError as exc:
        return await _render_m365_mail_dashboard(
            request,
            current_user,
            success_message=None,
            error_message=str(exc),
            status_code=status.HTTP_404_NOT_FOUND,
        )
    except ValueError as exc:
        return await _render_m365_mail_dashboard(
            request,
            current_user,
            success_message=None,
            error_message=str(exc),
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    except Exception as exc:  # pragma: no cover - defensive logging
        log_error("Failed to clone M365 mail account", account_id=account_id, error=str(exc))
        return await _render_m365_mail_dashboard(
            request,
            current_user,
            success_message=None,
            error_message="Unable to clone the Office 365 mail account at this time.",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
    label = account.get("name") or f"Mailbox {account_id} copy"
    message = f"Mailbox {label} cloned."
    return flash_redirect("/admin/modules/m365-mail", message, "success")


@router.post("/admin/modules/m365-mail/accounts/{account_id}/delete", response_class=HTMLResponse)
async def admin_delete_m365_mail_account(account_id: int, request: Request):
    current_user, redirect = await _main()._require_super_admin_page(request)
    if redirect:
        return redirect
    account = await m365_mail_service.get_account(account_id)
    try:
        await m365_mail_service.delete_account(account_id)
    except Exception as exc:  # pragma: no cover - defensive logging
        log_error("Failed to delete M365 mail account", account_id=account_id, error=str(exc))
        return await _render_m365_mail_dashboard(
            request,
            current_user,
            success_message=None,
            error_message="Unable to delete the Office 365 mail account at this time.",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
    label = account.get("name") if account else f"#{account_id}"
    message = f"Mailbox {label} deleted."
    return flash_redirect("/admin/modules/m365-mail", message, "success")


@router.post("/admin/modules/m365-mail/accounts/{account_id}/sync", response_class=HTMLResponse)
async def admin_sync_m365_mail_account(account_id: int, request: Request):
    current_user, redirect = await _main()._require_super_admin_page(request)
    if redirect:
        return redirect
    result = await m365_mail_service.sync_account(account_id)
    status_value = str(result.get("status") or "").lower()
    processed = int(result.get("processed") or 0)
    error_count = len(result.get("errors") or [])
    message_actions = result.get("message_actions") or []
    created_count = sum(1 for action in message_actions if action.get("outcome") == "created_new_ticket")
    attached_count = sum(1 for action in message_actions if action.get("outcome") == "attached_to_existing_ticket")
    ignored_count = sum(1 for action in message_actions if action.get("outcome") == "ignored")
    detail_summary = (
        f" Created {created_count}, attached {attached_count}, ignored {ignored_count}."
        if message_actions
        else ""
    )
    if status_value in {"error"}:
        message = result.get("error") or "Office 365 mail synchronisation failed."
        return await _render_m365_mail_dashboard(
            request,
            current_user,
            error_message=message,
            sync_result=result,
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
    if status_value == "skipped":
        message = result.get("reason") or "Office 365 mail synchronisation skipped."
        return await _render_m365_mail_dashboard(
            request,
            current_user,
            error_message=message,
            sync_result=result,
        )
    if status_value == "completed_with_errors" and error_count:
        first_error = (
            (result.get("errors") or [{}])[0].get("error")
            or "Office 365 mail sync completed with errors."
        )
        if processed:
            first_error = f"{first_error} ({processed} message{'s' if processed != 1 else ''} imported.)"
        first_error = f"{first_error}{detail_summary}"
        return await _render_m365_mail_dashboard(
            request,
            current_user,
            error_message=first_error,
            sync_result=result,
        )
    message = f"Office 365 mail sync imported {processed} message{'s' if processed != 1 else ''}.{detail_summary}"
    return await _render_m365_mail_dashboard(
        request,
        current_user,
        success_message=message,
        sync_result=result,
    )


@router.get("/admin/modules/m365-mail/accounts/{account_id}/authorize")
async def admin_m365_mail_authorize(account_id: int, request: Request):
    """Start an OAuth2 PKCE sign-in for a specific mail account."""
    main_module = _main()
    current_user, redirect = await main_module._require_super_admin_page(request)
    if redirect:
        return redirect
    account = await m365_mail_service.get_account(account_id)
    if not account:
        return flash_redirect("/admin/modules/m365-mail", "Account not found.", "error")
    company_id = account.get("company_id")
    redirect_uri = main_module._build_m365_redirect_uri(request)
    code_verifier, code_challenge = m365_service.generate_pkce_pair()
    state = main_module.oauth_state_serializer.dumps({
        "user_id": current_user.get("id"),
        "flow": "m365_mail_auth",
        "account_id": account_id,
        "company_id": company_id,
        "code_verifier": code_verifier,
    })
    params = {
        "client_id": await m365_service.get_effective_pkce_client_id_for_company(
            company_id, redirect_uri=redirect_uri
        )
        if company_id
        else await m365_service.get_effective_pkce_client_id(redirect_uri=redirect_uri),
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "response_mode": "query",
        "scope": m365_mail_service.DELEGATED_MAIL_SCOPE,
        "state": state,
        "prompt": "select_account",
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    authorize_url = (
        "https://login.microsoftonline.com/organizations/oauth2/v2.0/authorize"
        f"?{urlencode(params)}"
    )
    return RedirectResponse(url=authorize_url, status_code=status.HTTP_303_SEE_OTHER)


@router.post("/admin/modules/m365-mail/accounts/{account_id}/disconnect", response_class=HTMLResponse)
async def admin_m365_mail_disconnect(account_id: int, request: Request):
    """Remove the per-account delegated tokens and revert to company credentials."""
    current_user, redirect = await _main()._require_super_admin_page(request)
    if redirect:
        return redirect
    account = await m365_mail_service.get_account(account_id)
    if not account:
        return flash_redirect("/admin/modules/m365-mail", "Account not found.", "error")
    await m365_mail_service.clear_delegated_tokens(account_id)
    label = account.get("name") or f"#{account_id}"
    message = f"Disconnected user sign-in for {label}."
    return flash_redirect("/admin/modules/m365-mail", message, "success")
