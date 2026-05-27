"""Company admin handlers for the ``companies`` feature pack."""

from __future__ import annotations

import asyncio
import json
import secrets
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlencode

from fastapi import HTTPException, Query, Request, status
from fastapi.responses import JSONResponse, RedirectResponse


def _main():
    from app import main as main_module

    return main_module


def _parse_custom_field_options(options_text: str) -> list[dict[str, str]]:
    options: list[dict[str, str]] = []
    for part in (options_text or "").split(","):
        item = part.strip()
        if not item:
            continue
        if ":" in item:
            value_part, label_part = item.split(":", 1)
            value = value_part.strip()
            label = label_part.strip() or value
        else:
            value = item
            label = item
        if not value:
            continue
        options.append({"value": value, "label": label})
    return options


def _parse_staff_custom_field_condition(
    *,
    parent_name_value: str,
    operator_value: str,
    condition_value: str,
) -> tuple[str | None, str | None, str | None]:
    parent_name = str(parent_name_value or "").strip().lower().replace(" ", "_")
    operator = str(operator_value or "").strip().lower()
    normalized_condition_value = str(condition_value or "").strip()
    if not parent_name:
        return None, None, None
    if operator not in {
        "equals",
        "not_equals",
        "one_of",
        "is_checked",
        "is_not_checked",
        "select_map",
    }:
        operator = "equals"
    if operator == "select_map":
        if normalized_condition_value.startswith("{"):
            try:
                parsed_map = json.loads(normalized_condition_value)
            except (TypeError, ValueError):
                return parent_name, operator, normalized_condition_value or None
            if isinstance(parsed_map, dict):
                return parent_name, operator, json.dumps(parsed_map, separators=(",", ":"))
        return parent_name, operator, normalized_condition_value or None
    if operator in {"is_checked", "is_not_checked"}:
        normalized_condition_value = None
    if operator in {"equals", "not_equals"} and not normalized_condition_value:
        fallback_operator = "is_checked" if operator == "equals" else "is_not_checked"
        return parent_name, fallback_operator, None
    return parent_name, operator, normalized_condition_value or None


async def _ensure_company_permission(
    request: Request,
    user: dict[str, Any],
    company_id: int,
    *,
    require_admin: bool = False,
    require_staff_manager: bool = False,
) -> None:
    is_super_admin, _, membership_lookup = await _main()._get_company_management_scope(
        request, user
    )
    if is_super_admin:
        return
    membership = membership_lookup.get(company_id)
    if not membership:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
    staff_permission = int(membership.get("staff_permission") or 0)
    if require_admin and not bool(membership.get("is_admin")):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
    if (
        require_staff_manager
        and staff_permission < 3
        and not bool(membership.get("can_manage_staff"))
    ):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")


async def admin_companies_page(
    request: Request,
    company_id: int | None = Query(default=None),
    success: str | None = Query(default=None),
    error: str | None = Query(default=None),
    show_archived: bool = Query(default=False),
):
    current_user, redirect = await _main()._require_authenticated_user(request)
    if redirect:
        return redirect
    return await _main()._render_companies_dashboard(
        request,
        current_user,
        selected_company_id=company_id,
        success_message=_main()._sanitize_message(success),
        error_message=_main()._sanitize_message(error),
        include_archived=show_archived,
    )


async def admin_company_edit_page(
    company_id: int,
    request: Request,
    success: str | None = Query(default=None),
    error: str | None = Query(default=None),
    show_inactive: bool = Query(default=False),
):
    current_user, redirect = await _main()._require_super_admin_page(request)
    if redirect:
        return redirect
    return await _main()._render_company_edit_page(
        request,
        current_user,
        company_id=company_id,
        success_message=_main()._sanitize_message(success),
        error_message=_main()._sanitize_message(error),
        show_inactive_tasks=show_inactive,
    )


async def admin_create_company(request: Request):
    from app.core.logging import log_error
    from app.repositories import companies as company_repo
    from app.services import company_domains

    current_user, redirect = await _main()._require_super_admin_page(request)
    if redirect:
        return redirect
    form = await request.form()
    name = str(form.get("name", "")).strip()
    syncro_company_id = str(form.get("syncroCompanyId", "")).strip() or None
    tactical_client_id = str(form.get("tacticalClientId", "")).strip() or None
    xero_id = str(form.get("xeroId", "")).strip() or None
    hudu_id = str(form.get("huduId", "")).strip() or None
    huntress_organization_id = (
        str(form.get("huntressOrganizationId", "")).strip() or None
    )
    is_vip = _main()._parse_bool(form.get("isVip"))
    raw_email_domains = form.get("emailDomains")
    try:
        email_domains = company_domains.parse_email_domain_text(
            str(raw_email_domains) if raw_email_domains is not None else ""
        )
    except company_domains.EmailDomainError as exc:
        return await _main()._render_companies_dashboard(
            request,
            current_user,
            error_message=str(exc),
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    if not name:
        return await _main()._render_companies_dashboard(
            request,
            current_user,
            error_message="Enter a company name.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    payload: dict[str, Any] = {
        "name": name,
        "is_vip": 1 if is_vip else 0,
        "email_domains": email_domains,
    }
    if syncro_company_id:
        payload["syncro_company_id"] = syncro_company_id
    if xero_id:
        payload["xero_id"] = xero_id
    if tactical_client_id:
        payload["tacticalrmm_client_id"] = tactical_client_id
    if hudu_id:
        payload["hudu_id"] = hudu_id
    if huntress_organization_id:
        payload["huntress_organization_id"] = huntress_organization_id
    try:
        created = await company_repo.create_company(**payload)
    except Exception as exc:  # pragma: no cover - defensive logging
        log_error("Failed to create company", error=str(exc))
        return await _main()._render_companies_dashboard(
            request,
            current_user,
            error_message="Unable to create company. Please try again.",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
    return _main()._companies_redirect(
        company_id=created.get("id"),
        success=f"Company {created.get('name')} created.",
    )


async def admin_assign_user_to_company(request: Request):
    from app.repositories import companies as company_repo
    from app.repositories import company_memberships as membership_repo
    from app.repositories import pending_staff_access as pending_staff_access_repo
    from app.repositories import roles as role_repo
    from app.repositories import staff as staff_repo
    from app.repositories import user_companies as user_company_repo
    from app.repositories import users as user_repo

    current_user, redirect = await _main()._require_super_admin_page(request)
    if redirect:
        return redirect
    form = await request.form()
    form_keys = set(form.keys())
    user_id_raw = form.get("userId") or form.get("user_id")
    company_id_raw = form.get("companyId") or form.get("company_id")
    source_company_raw = form.get("sourceCompanyId") or form.get("source_company_id")
    role_raw = form.get("roleId") or form.get("role_id")
    staff_permission_raw = form.get("staffPermission") or form.get("staff_permission")

    assign_form_state: dict[str, Any] = {
        "company_id": source_company_raw or company_id_raw,
        "user_value": user_id_raw,
        "user_id": None,
        "role_id": role_raw,
        "staff_permission": staff_permission_raw,
        "can_manage_staff": "can_manage_staff" in form_keys,
    }
    for column in _main()._COMPANY_PERMISSION_COLUMNS:
        field = column.get("field")
        if field:
            assign_form_state[field] = field in form_keys

    resolved_company_id: int | None = None
    for raw_value in (source_company_raw, company_id_raw):
        if raw_value is None:
            continue
        try:
            resolved_company_id = int(raw_value)
            break
        except (TypeError, ValueError):
            continue

    async def _assign_error(message: str, status_code: int):
        if resolved_company_id is None:
            return _main()._companies_redirect(error=message)
        return await _main()._render_company_edit_page(
            request,
            current_user,
            company_id=resolved_company_id,
            assign_form_values=assign_form_state,
            error_message=message,
            status_code=status_code,
        )

    if resolved_company_id is None:
        return await _assign_error(
            "Select both a user and a company.", status.HTTP_400_BAD_REQUEST
        )

    company_id = resolved_company_id
    assign_form_state["company_id"] = company_id

    user_identifier = (user_id_raw or "").strip()
    parsed_user_id: int | None = None
    user_record: dict[str, Any] | None = None
    staff_record: dict[str, Any] | None = None
    existing_assignment: dict[str, Any] | None = None
    staff_selection_id = _main()._parse_staff_selection(user_identifier)
    if staff_selection_id is not None:
        staff_record = await staff_repo.get_staff_by_id(staff_selection_id)
        if not staff_record:
            return await _assign_error(
                "Selected staff member could not be found.",
                status.HTTP_404_NOT_FOUND,
            )
        email = (staff_record.get("email") or "").strip()
        if not email:
            return await _assign_error(
                "Selected staff member does not have an email address.",
                status.HTTP_400_BAD_REQUEST,
            )
        staff_company_raw = staff_record.get("company_id")
        try:
            staff_company_id = int(staff_company_raw)
        except (TypeError, ValueError):
            staff_company_id = None
        if staff_company_id is not None and staff_company_id != company_id:
            return await _assign_error(
                "Selected staff member belongs to a different company.",
                status.HTTP_400_BAD_REQUEST,
            )

        user_record = await user_repo.get_user_by_email(email)
        if user_record and int(user_record.get("company_id") or 0) == company_id:
            parsed_user_id = int(user_record.get("id"))
            existing_assignment = await user_company_repo.get_user_company(
                parsed_user_id, company_id
            )
        else:
            try:
                staff_permission = (
                    int(staff_permission_raw) if staff_permission_raw is not None else 0
                )
            except (TypeError, ValueError):
                return await _assign_error(
                    "Select a valid staff permission level.",
                    status.HTTP_400_BAD_REQUEST,
                )
            if staff_permission < 0:
                staff_permission = 0
            if staff_permission > 3:
                staff_permission = 3

            permission_values: dict[str, bool] = {}
            for column in _main()._COMPANY_PERMISSION_COLUMNS:
                field = column.get("field")
                if not field:
                    continue
                permission_values[field] = (
                    _main()._parse_bool(form.get(field)) if field in form_keys else False
                )

            if "can_manage_staff" in form_keys:
                can_manage_staff_value = _main()._parse_bool(form.get("can_manage_staff"))
            else:
                can_manage_staff_value = False

            role_id_value: int | None = None
            if role_raw:
                try:
                    role_id_value = int(role_raw)
                except (TypeError, ValueError):
                    return await _assign_error(
                        "Select a valid role for the membership.",
                        status.HTTP_400_BAD_REQUEST,
                    )
                role_record = await role_repo.get_role_by_id(role_id_value)
                if not role_record:
                    return await _assign_error(
                        "Selected role could not be found.",
                        status.HTTP_404_NOT_FOUND,
                    )

            await pending_staff_access_repo.upsert_assignment(
                staff_id=int(staff_record.get("id")),
                company_id=company_id,
                staff_permission=staff_permission,
                can_manage_staff=can_manage_staff_value,
                can_manage_licenses=permission_values.get("can_manage_licenses", False),
                can_manage_assets=permission_values.get("can_manage_assets", False),
                can_manage_invoices=permission_values.get("can_manage_invoices", False),
                can_manage_office_groups=permission_values.get(
                    "can_manage_office_groups", False
                ),
                can_manage_issues=permission_values.get("can_manage_issues", False),
                can_order_licenses=permission_values.get("can_order_licenses", False),
                can_access_shop=permission_values.get("can_access_shop", False),
                can_access_cart=permission_values.get("can_access_cart", False),
                can_access_orders=permission_values.get("can_access_orders", False),
                can_access_quotes=permission_values.get("can_access_quotes", False),
                can_access_forms=permission_values.get("can_access_forms", False),
                is_admin=permission_values.get("is_admin", False),
                role_id=role_id_value,
            )

            success_message = (
                f"Saved pending access for {email}. Permissions will activate after sign-up."
            )
            return _main()._company_edit_redirect(
                company_id=company_id,
                success=success_message,
            )
    else:
        try:
            parsed_user_id = int(user_identifier)
        except (TypeError, ValueError):
            return await _assign_error(
                "Select both a user and a company.", status.HTTP_400_BAD_REQUEST
            )

    assign_form_state["user_id"] = parsed_user_id
    if not assign_form_state.get("user_value"):
        assign_form_state["user_value"] = user_identifier

    if user_record is None and parsed_user_id is not None:
        user_record = await user_repo.get_user_by_id(parsed_user_id)
    company_record = await company_repo.get_company_by_id(company_id)
    if not user_record or not company_record:
        return await _assign_error(
            "User or company not found.", status.HTTP_404_NOT_FOUND
        )

    if existing_assignment is None and parsed_user_id is not None:
        existing_assignment = await user_company_repo.get_user_company(
            parsed_user_id, company_id
        )

    try:
        staff_permission = int(staff_permission_raw) if staff_permission_raw is not None else 0
    except (TypeError, ValueError):
        return await _assign_error(
            "Select a valid staff permission level.", status.HTTP_400_BAD_REQUEST
        )
    if staff_permission < 0:
        staff_permission = 0
    if staff_permission > 3:
        staff_permission = 3
    assign_form_state["staff_permission"] = staff_permission

    permission_values: dict[str, bool] = {}
    for column in _main()._COMPANY_PERMISSION_COLUMNS:
        field = column.get("field")
        if not field:
            continue
        if field in form_keys:
            permission_values[field] = _main()._parse_bool(form.get(field))
        elif existing_assignment is not None:
            permission_values[field] = bool(existing_assignment.get(field, False))
        else:
            permission_values[field] = False
        assign_form_state[field] = permission_values[field]

    if "can_manage_staff" in form_keys:
        can_manage_staff = _main()._parse_bool(form.get("can_manage_staff"))
    elif existing_assignment is not None:
        can_manage_staff = bool(existing_assignment.get("can_manage_staff", False))
    else:
        can_manage_staff = False
    assign_form_state["can_manage_staff"] = can_manage_staff

    assign_kwargs: dict[str, Any] = {
        "user_id": parsed_user_id,
        "company_id": company_id,
        "staff_permission": staff_permission,
        "can_manage_staff": can_manage_staff,
    }
    for field, value in permission_values.items():
        assign_kwargs[field] = value

    await user_company_repo.assign_user_to_company(**assign_kwargs)

    if staff_record and staff_record.get("id") is not None:
        try:
            staff_id_int = int(staff_record.get("id"))
        except (TypeError, ValueError):
            staff_id_int = None
        if staff_id_int is not None:
            await pending_staff_access_repo.delete_assignment(
                staff_id=staff_id_int, company_id=company_id
            )

    if role_raw:
        try:
            role_id = int(role_raw)
        except (TypeError, ValueError):
            return await _assign_error(
                "Select a valid role for the membership.",
                status.HTTP_400_BAD_REQUEST,
            )
        assign_form_state["role_id"] = role_id
        role_record = await role_repo.get_role_by_id(role_id)
        if not role_record:
            return await _assign_error(
                "Selected role could not be found.",
                status.HTTP_404_NOT_FOUND,
            )
        membership = await membership_repo.get_membership_by_company_user(
            company_id, parsed_user_id
        )
        if membership:
            membership_id = membership.get("id")
            if membership_id is not None and membership.get("role_id") != role_id:
                await membership_repo.update_membership(int(membership_id), role_id=role_id)

    return _main()._company_edit_redirect(
        company_id=company_id,
        success=(
            f"Updated access for {user_record.get('email')} at {company_record.get('name')}"
        ),
    )


async def admin_update_company(company_id: int, request: Request):
    from app.core.logging import log_error, log_info
    from app.repositories import companies as company_repo
    from app.repositories import scheduled_tasks as scheduled_tasks_repo
    from app.services import company_domains
    from app.services.scheduler import scheduler_service

    current_user, redirect = await _main()._require_super_admin_page(request)
    if redirect:
        return redirect
    form = await request.form()
    name = str(form.get("name", "")).strip()
    syncro_company_raw = str(form.get("syncroCompanyId", "")).strip()
    tactical_client_raw = str(form.get("tacticalClientId", "")).strip()
    xero_id_raw = str(form.get("xeroId", "")).strip()
    hudu_id_raw = str(form.get("huduId", "")).strip()
    huntress_organization_id_raw = str(form.get("huntressOrganizationId", "")).strip()
    trello_board_id_raw = str(form.get("trelloBoardId", "")).strip()
    trello_api_key_raw = str(form.get("trelloApiKey", "")).strip()
    trello_token_raw = str(form.get("trelloToken", "")).strip()
    is_vip = _main()._parse_bool(form.get("isVip"))
    invoice_prepay_enabled = bool(form.get("invoicePrepay"))
    invoice_postpay_enabled = bool(form.get("invoicePostpay"))
    stripe_enabled = bool(form.get("stripeEnabled"))
    require_po = bool(form.get("requirePo"))
    offboarding_email_forwarding_enabled = bool(
        form.get("offboardingEmailForwardingEnabled")
    )
    _selected_methods = [
        m
        for m, enabled in [
            ("invoice_prepay", invoice_prepay_enabled),
            ("invoice_postpay", invoice_postpay_enabled),
            ("stripe", stripe_enabled),
        ]
        if enabled
    ]
    payment_method = ",".join(_selected_methods) if _selected_methods else "invoice_prepay"
    raw_email_domains = form.get("emailDomains")
    email_domains_text = str(raw_email_domains) if raw_email_domains is not None else ""
    existing = await company_repo.get_company_by_id(company_id)
    if not existing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Company not found"
        )
    form_values = {
        "name": name,
        "syncro_company_id": syncro_company_raw,
        "tacticalrmm_client_id": tactical_client_raw,
        "xero_id": xero_id_raw,
        "hudu_id": hudu_id_raw,
        "huntress_organization_id": huntress_organization_id_raw,
        "trello_board_id": trello_board_id_raw,
        "trello_api_key": trello_api_key_raw or existing.get("trello_api_key"),
        "trello_token": trello_token_raw or existing.get("trello_token"),
        "email_domains": email_domains_text,
        "is_vip": is_vip,
        "payment_method": payment_method,
        "require_po": require_po,
        "offboarding_email_forwarding_enabled": offboarding_email_forwarding_enabled,
    }
    try:
        email_domains = company_domains.parse_email_domain_text(email_domains_text)
    except company_domains.EmailDomainError as exc:
        return await _main()._render_company_edit_page(
            request,
            current_user,
            company_id=company_id,
            form_values=form_values,
            error_message=str(exc),
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    if not name:
        return await _main()._render_company_edit_page(
            request,
            current_user,
            company_id=company_id,
            form_values=form_values,
            error_message="Enter a company name.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    syncro_company_id = syncro_company_raw or None
    tactical_client_id = tactical_client_raw or None
    xero_id = xero_id_raw or None
    hudu_id = hudu_id_raw or None
    huntress_organization_id = huntress_organization_id_raw or None
    trello_board_id = trello_board_id_raw or None
    trello_api_key: str | None = (
        trello_api_key_raw if trello_api_key_raw else (existing.get("trello_api_key") or None)
    )
    trello_token: str | None = (
        trello_token_raw if trello_token_raw else (existing.get("trello_token") or None)
    )
    updates: dict[str, Any] = {
        "name": name,
        "is_vip": 1 if is_vip else 0,
        "syncro_company_id": syncro_company_id,
        "tacticalrmm_client_id": tactical_client_id,
        "xero_id": xero_id,
        "hudu_id": hudu_id,
        "huntress_organization_id": huntress_organization_id,
        "trello_board_id": trello_board_id,
        "trello_api_key": trello_api_key,
        "trello_token": trello_token,
        "email_domains": email_domains,
        "payment_method": payment_method,
        "require_po": 1 if require_po else 0,
        "offboarding_email_forwarding_enabled": 1
        if offboarding_email_forwarding_enabled
        else 0,
    }
    try:
        await company_repo.update_company(company_id, **updates)
    except Exception as exc:  # pragma: no cover - defensive logging
        log_error("Failed to update company", company_id=company_id, error=str(exc))
        return await _main()._render_company_edit_page(
            request,
            current_user,
            company_id=company_id,
            form_values=form_values,
            error_message="Unable to update company. Please try again.",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
    if huntress_organization_id:
        existing_commands = await scheduled_tasks_repo.get_commands_for_company(company_id)
        if "sync_huntress" not in existing_commands:
            huntress_task_name = (
                f"{name} - Sync Huntress data" if name else "Sync Huntress data"
            )
            await scheduled_tasks_repo.create_task(
                name=huntress_task_name,
                command="sync_huntress",
                cron=_main()._random_daily_cron(),
                company_id=company_id,
                active=True,
            )
            log_info(
                "Auto-created scheduled task after Huntress organization ID was set",
                command="sync_huntress",
                company_id=company_id,
            )
            asyncio.create_task(scheduler_service.refresh())
    return _main()._company_edit_redirect(
        company_id=company_id,
        success=f"Company {name} updated.",
    )


async def admin_update_company_staff_fields(company_id: int, request: Request):
    from app.repositories import companies as company_repo
    from app.services import staff_field_config as staff_field_config_service

    current_user, redirect = await _main()._require_super_admin_page(request)
    if redirect:
        return redirect
    existing = await company_repo.get_company_by_id(company_id)
    if not existing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Company not found"
        )
    form = await request.form()
    form_data = {str(key): form.get(key) for key in form.keys()}
    await staff_field_config_service.save_company_staff_field_admin_config(
        company_id, form_data
    )
    return _main()._company_edit_redirect(
        company_id=company_id,
        success="Staff intake field configuration updated.",
    )


async def admin_create_company_staff_custom_field(company_id: int, request: Request):
    from app.repositories import companies as company_repo
    from app.repositories import staff_custom_fields as staff_custom_fields_repo

    current_user, redirect = await _main()._require_super_admin_page(request)
    if redirect:
        return redirect
    existing = await company_repo.get_company_by_id(company_id)
    if not existing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Company not found"
        )
    form = await request.form()
    name = str(form.get("name") or "").strip().lower().replace(" ", "_")
    display_name = str(form.get("display_name") or "").strip() or None
    help_text = str(form.get("help_text") or "").strip() or None
    field_type = str(form.get("field_type") or "text").strip().lower()
    field_group = str(form.get("field_group") or "").strip() or None
    try:
        display_order = int(str(form.get("display_order") or "0").strip())
    except ValueError:
        display_order = 0
    condition_parent_name, condition_operator, condition_value = (
        _parse_staff_custom_field_condition(
            parent_name_value=str(form.get("condition_parent_name") or ""),
            operator_value=str(form.get("condition_operator") or ""),
            condition_value=str(form.get("condition_value") or ""),
        )
    )
    options = _parse_custom_field_options(str(form.get("options") or ""))
    if not name:
        return _main()._company_edit_redirect(
            company_id=company_id, error="Custom field name is required."
        )
    if field_type not in {"text", "checkbox", "date", "select", "multiselect"}:
        return _main()._company_edit_redirect(
            company_id=company_id, error="Invalid custom field type."
        )
    await staff_custom_fields_repo.create_company_definition(
        company_id=company_id,
        name=name,
        display_name=display_name,
        help_text=help_text,
        field_type=field_type,
        field_group=field_group,
        display_order=display_order,
        condition_parent_name=condition_parent_name,
        condition_operator=condition_operator,
        condition_value=condition_value,
        options=options,
    )
    return _main()._company_edit_redirect(
        company_id=company_id, success="Staff custom field created."
    )


async def admin_update_company_staff_custom_field(
    company_id: int, definition_id: int, request: Request
):
    from app.repositories import companies as company_repo
    from app.repositories import staff_custom_fields as staff_custom_fields_repo

    current_user, redirect = await _main()._require_super_admin_page(request)
    if redirect:
        return redirect
    existing = await company_repo.get_company_by_id(company_id)
    if not existing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Company not found"
        )
    form = await request.form()
    display_name = str(form.get("display_name") or "").strip() or None
    help_text = str(form.get("help_text") or "").strip() or None
    field_type = str(form.get("field_type") or "text").strip().lower()
    field_group = str(form.get("field_group") or "").strip() or None
    try:
        display_order = int(str(form.get("display_order") or "0").strip())
    except ValueError:
        display_order = 0
    is_active = str(form.get("is_active") or "").lower() in {"1", "true", "on", "yes"}
    condition_parent_name, condition_operator, condition_value = (
        _parse_staff_custom_field_condition(
            parent_name_value=str(form.get("condition_parent_name") or ""),
            operator_value=str(form.get("condition_operator") or ""),
            condition_value=str(form.get("condition_value") or ""),
        )
    )
    options = _parse_custom_field_options(str(form.get("options") or ""))
    await staff_custom_fields_repo.update_company_definition(
        definition_id,
        company_id=company_id,
        display_name=display_name,
        help_text=help_text,
        field_type=field_type,
        field_group=field_group,
        display_order=display_order,
        is_active=is_active,
        condition_parent_name=condition_parent_name,
        condition_operator=condition_operator,
        condition_value=condition_value,
        options=options,
    )
    return _main()._company_edit_redirect(
        company_id=company_id, success="Staff custom field updated."
    )


async def admin_delete_company_staff_custom_field(
    company_id: int, definition_id: int, request: Request
):
    from app.repositories import staff_custom_fields as staff_custom_fields_repo

    current_user, redirect = await _main()._require_super_admin_page(request)
    if redirect:
        return redirect
    await staff_custom_fields_repo.delete_company_definition(definition_id, company_id)
    return _main()._company_edit_redirect(
        company_id=company_id, success="Staff custom field deleted."
    )


async def admin_create_company_user(request: Request):
    from app.repositories import user_companies as user_company_repo
    from app.repositories import users as user_repo
    from app.services import staff_access as staff_access_service

    current_user, redirect = await _main()._require_authenticated_user(request)
    if redirect:
        return redirect
    form = await request.form()
    company_id_raw = form.get("companyId")
    email = str(form.get("email", "")).strip().lower()
    password = str(form.get("password", ""))
    first_name = str(form.get("firstName", "")).strip() or None
    last_name = str(form.get("lastName", "")).strip() or None
    try:
        company_id = int(company_id_raw)
    except (TypeError, ValueError):
        return await _main()._render_companies_dashboard(
            request,
            current_user,
            error_message="Select a company.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    await _ensure_company_permission(
        request,
        current_user,
        company_id,
        require_admin=True,
        require_staff_manager=True,
    )
    if not email:
        return await _main()._render_companies_dashboard(
            request,
            current_user,
            selected_company_id=company_id,
            error_message="Enter an email address.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    if len(password) < 8:
        return await _main()._render_companies_dashboard(
            request,
            current_user,
            selected_company_id=company_id,
            error_message="Enter a password of at least 8 characters.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    existing_user = await user_repo.get_user_by_email(email)
    if existing_user:
        return await _main()._render_companies_dashboard(
            request,
            current_user,
            selected_company_id=company_id,
            error_message="A user with that email already exists.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    created_user = await user_repo.create_user(
        email=email,
        password=password,
        first_name=first_name,
        last_name=last_name,
        company_id=company_id,
    )
    await staff_access_service.apply_pending_access_for_user(created_user)
    await user_repo.update_user(created_user["id"], force_password_change=1)
    await user_company_repo.assign_user_to_company(
        user_id=created_user["id"],
        company_id=company_id,
    )
    return _main()._companies_redirect(
        company_id=company_id,
        success=f"User {email} created.",
    )


async def admin_invite_company_user(request: Request):
    from app.repositories import user_companies as user_company_repo
    from app.repositories import users as user_repo
    from app.services import staff_access as staff_access_service

    current_user, redirect = await _main()._require_authenticated_user(request)
    if redirect:
        return redirect
    form = await request.form()
    company_id_raw = form.get("companyId")
    email = str(form.get("email", "")).strip().lower()
    first_name = str(form.get("firstName", "")).strip() or None
    last_name = str(form.get("lastName", "")).strip() or None
    try:
        company_id = int(company_id_raw)
    except (TypeError, ValueError):
        return await _main()._render_companies_dashboard(
            request,
            current_user,
            error_message="Select a company.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    await _ensure_company_permission(
        request,
        current_user,
        company_id,
        require_admin=True,
        require_staff_manager=True,
    )
    if not email:
        return await _main()._render_companies_dashboard(
            request,
            current_user,
            selected_company_id=company_id,
            error_message="Enter an email address.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    existing_user = await user_repo.get_user_by_email(email)
    if existing_user:
        return await _main()._render_companies_dashboard(
            request,
            current_user,
            selected_company_id=company_id,
            error_message="A user with that email already exists.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    temporary_password = secrets.token_urlsafe(12)
    created_user = await user_repo.create_user(
        email=email,
        password=temporary_password,
        first_name=first_name,
        last_name=last_name,
        company_id=company_id,
    )
    await staff_access_service.apply_pending_access_for_user(created_user)
    await user_repo.update_user(created_user["id"], force_password_change=1)
    await user_company_repo.assign_user_to_company(
        user_id=created_user["id"],
        company_id=company_id,
    )
    return await _main()._render_companies_dashboard(
        request,
        current_user,
        selected_company_id=company_id,
        success_message=f"Invitation generated for {email}.",
        temporary_password=temporary_password,
        invited_email=email,
    )


async def admin_update_company_permission(company_id: int, user_id: int, request: Request):
    from app.repositories import user_companies as user_company_repo

    current_user, redirect = await _main()._require_authenticated_user(request)
    if redirect:
        return redirect
    await _ensure_company_permission(
        request,
        current_user,
        company_id,
        require_admin=True,
    )
    form = await request.form()
    field = str(form.get("field", "")).strip()
    value = _main()._parse_bool(form.get("value"))
    try:
        await user_company_repo.update_permission(
            user_id=user_id,
            company_id=company_id,
            field=field,
            value=value,
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    return JSONResponse({"success": True})


async def admin_update_staff_permission(company_id: int, user_id: int, request: Request):
    from app.repositories import user_companies as user_company_repo

    current_user, redirect = await _main()._require_authenticated_user(request)
    if redirect:
        return redirect
    await _ensure_company_permission(
        request,
        current_user,
        company_id,
        require_staff_manager=True,
    )
    form = await request.form()
    permission_raw = form.get("permission")
    try:
        permission_value = int(permission_raw)
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid permission value"
        )
    await user_company_repo.update_staff_permission(
        user_id=user_id,
        company_id=company_id,
        permission=permission_value,
    )
    return JSONResponse({"success": True})


async def admin_update_membership_role(company_id: int, user_id: int, request: Request):
    from app.repositories import company_memberships as membership_repo
    from app.repositories import roles as role_repo
    from app.services import audit as audit_service

    current_user, redirect = await _main()._require_authenticated_user(request)
    if redirect:
        return redirect
    await _ensure_company_permission(
        request,
        current_user,
        company_id,
        require_admin=True,
    )
    form = await request.form()
    role_raw = form.get("roleId") or form.get("role_id")
    try:
        role_id = int(role_raw) if role_raw is not None else None
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid role selection"
        )
    if role_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Select a role for the membership",
        )

    membership = await membership_repo.get_membership_by_company_user(company_id, user_id)
    if not membership:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Membership not found"
        )
    membership_id = membership.get("id")
    if membership_id is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Membership identifier missing",
        )

    existing_role_id = membership.get("role_id")
    if existing_role_id == role_id:
        return JSONResponse({"success": True})

    role_record = await role_repo.get_role_by_id(role_id)
    if not role_record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Role not found")

    updated = await membership_repo.update_membership(int(membership_id), role_id=role_id)

    await audit_service.log_action(
        action="membership.role_changed",
        user_id=current_user.get("id"),
        entity_type="company_membership",
        entity_id=int(membership_id),
        previous_value={"role_id": existing_role_id},
        new_value={"role_id": role_id},
        request=request,
    )

    return JSONResponse(
        {
            "success": True,
            "role_id": role_id,
            "role_name": updated.get("role_name"),
        }
    )


async def admin_remove_pending_company_assignment(
    company_id: int, staff_id: int, request: Request
):
    from app.repositories import pending_staff_access as pending_staff_access_repo
    from app.services import audit as audit_service

    current_user, redirect = await _main()._require_super_admin_page(request)
    if redirect:
        return redirect

    pending_assignment = await pending_staff_access_repo.get_assignment(
        staff_id=staff_id, company_id=company_id
    )
    if not pending_assignment:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Pending staff access not found",
        )

    await pending_staff_access_repo.delete_assignment(
        staff_id=staff_id, company_id=company_id
    )

    await audit_service.log_action(
        action="pending_staff_access.removed",
        user_id=current_user.get("id"),
        entity_type="pending_staff_access",
        entity_id=staff_id,
        previous_value=pending_assignment,
        new_value=None,
        request=request,
    )

    return JSONResponse({"success": True})


async def admin_remove_company_assignment(company_id: int, user_id: int, request: Request):
    from app.repositories import user_companies as user_company_repo

    current_user, redirect = await _main()._require_super_admin_page(request)
    if redirect:
        return redirect
    await user_company_repo.remove_assignment(user_id=user_id, company_id=company_id)
    return JSONResponse({"success": True})


async def admin_add_billing_contact(company_id: int, request: Request):
    """Add a staff member as a billing contact for a company."""
    from app.repositories import billing_contacts as billing_contacts_repo
    from app.repositories import companies as company_repo
    from app.repositories import staff as staff_repo

    current_user, redirect = await _main()._require_super_admin_page(request)
    if redirect:
        return redirect

    payload = await request.json()
    staff_id = payload.get("staff_id") or payload.get("staffId")

    if not staff_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="staff_id required"
        )

    try:
        staff_id_int = int(staff_id)
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid staff_id"
        )

    company = await company_repo.get_company_by_id(company_id)
    if not company:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Company not found"
        )

    staff = await staff_repo.get_staff_by_id(staff_id_int)
    if not staff:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Staff member not found",
        )
    if staff.get("company_id") != company_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Staff member must belong to the company",
        )

    contact = await billing_contacts_repo.add_billing_contact(company_id, staff_id_int)
    return JSONResponse(
        {
            "success": True,
            "contact": {
                "staff_id": contact.get("staff_id"),
                "email": contact.get("email"),
                "first_name": contact.get("first_name"),
                "last_name": contact.get("last_name"),
            },
        }
    )


async def admin_remove_billing_contact(company_id: int, staff_id: int, request: Request):
    """Remove a staff member as a billing contact for a company."""
    from app.repositories import billing_contacts as billing_contacts_repo

    current_user, redirect = await _main()._require_super_admin_page(request)
    if redirect:
        return redirect

    await billing_contacts_repo.remove_billing_contact(company_id, staff_id)
    return JSONResponse({"success": True})


async def admin_company_m365_provision(
    company_id: int, request: Request, tenant_id: str = Query(...)
):
    """Start admin-consent OAuth flow to auto-provision an enterprise app for a company."""
    from app.services import m365 as m365_service

    current_user, redirect = await _main()._require_super_admin_page(request)
    if redirect:
        return redirect
    tenant_id = tenant_id.strip()
    if not tenant_id:
        return _main()._company_edit_redirect(
            company_id=company_id,
            error="Tenant ID is required to auto-provision.",
        )
    redirect_uri = _main()._build_m365_redirect_uri(request)
    code_verifier, code_challenge = m365_service.generate_pkce_pair()
    verifier_id = await _main()._store_m365_provision_code_verifier(code_verifier)
    state = _main().oauth_state_serializer.dumps(
        {
            "company_id": company_id,
            "user_id": current_user.get("id"),
            "tenant_id": tenant_id,
            "flow": "provision",
            "return_to": "company_edit",
            "verifier_id": verifier_id,
        }
    )
    oauth_client_id = await m365_service.get_effective_pkce_client_id_for_company(
        company_id, redirect_uri=redirect_uri
    )
    params = {
        "client_id": oauth_client_id,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "response_mode": "query",
        "scope": m365_service.PROVISION_SCOPE,
        "state": state,
        "prompt": "consent",
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "domain_hint": tenant_id,
    }
    authorize_url = (
        "https://login.microsoftonline.com/organizations/oauth2/v2.0/authorize"
        f"?{urlencode(params)}"
    )
    return RedirectResponse(url=authorize_url, status_code=status.HTTP_303_SEE_OTHER)


async def admin_company_m365_discover(company_id: int, request: Request):
    """Sign in as Global Admin to discover the tenant ID for a company."""
    from app.services import m365 as m365_service

    current_user, redirect = await _main()._require_super_admin_page(request)
    if redirect:
        return redirect
    redirect_uri = _main()._build_m365_redirect_uri(request)
    code_verifier, code_challenge = m365_service.generate_pkce_pair()
    oauth_client_id = await m365_service.get_effective_pkce_client_id_for_company(
        company_id, redirect_uri=redirect_uri
    )

    state_payload: dict = {
        "company_id": company_id,
        "user_id": current_user.get("id"),
        "flow": "discover",
        "return_to": "company_edit",
        "code_verifier": code_verifier,
    }

    state = _main().oauth_state_serializer.dumps(state_payload)
    params: dict = {
        "client_id": oauth_client_id,
        "response_type": "code",
        "redirect_uri": redirect_uri,
        "response_mode": "query",
        "scope": m365_service.DISCOVER_SCOPE,
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


async def admin_save_company_m365_credentials(company_id: int, request: Request):
    from app.core.logging import log_info
    from app.repositories import companies as company_repo
    from app.repositories import m365 as m365_repo
    from app.services import m365 as m365_service

    current_user, redirect = await _main()._require_super_admin_page(request)
    if redirect:
        return redirect
    existing_company = await company_repo.get_company_by_id(company_id)
    if not existing_company:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Company not found"
        )
    form = await request.form()
    tenant_id = str(form.get("tenantId", "")).strip()
    client_id = str(form.get("clientId", "")).strip()
    client_secret = str(form.get("clientSecret", "")).strip()
    if not tenant_id or not client_id:
        return _main()._company_edit_redirect(
            company_id=company_id,
            error="Tenant ID and Client ID are required.",
        )
    existing_creds = await m365_repo.get_credentials(company_id)
    if not client_secret and not existing_creds:
        return _main()._company_edit_redirect(
            company_id=company_id,
            error="Client secret is required when adding Microsoft 365 credentials for the first time.",
        )
    if client_secret:
        await m365_service.upsert_credentials(
            company_id=company_id,
            tenant_id=tenant_id,
            client_id=client_id,
            client_secret=client_secret,
        )
    else:
        existing_secret = existing_creds.get("client_secret")
        if not existing_secret:
            return _main()._company_edit_redirect(
                company_id=company_id,
                error="Existing client secret is missing. Please provide a new client secret.",
            )
        await m365_repo.upsert_credentials(
            company_id=company_id,
            tenant_id=tenant_id,
            client_id=client_id,
            client_secret=existing_secret,
            refresh_token=existing_creds.get("refresh_token"),
            access_token=existing_creds.get("access_token"),
            token_expires_at=existing_creds.get("token_expires_at"),
        )
    log_info(
        "Microsoft 365 credentials updated via admin company edit",
        company_id=company_id,
        user_id=current_user.get("id"),
    )
    return _main()._company_edit_redirect(
        company_id=company_id,
        success="Microsoft 365 credentials saved.",
    )


async def admin_delete_company_m365_credentials(company_id: int, request: Request):
    from app.core.logging import log_info
    from app.repositories import companies as company_repo
    from app.services import m365 as m365_service

    current_user, redirect = await _main()._require_super_admin_page(request)
    if redirect:
        return redirect
    existing_company = await company_repo.get_company_by_id(company_id)
    if not existing_company:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Company not found"
        )
    await m365_service.delete_credentials(company_id)
    log_info(
        "Microsoft 365 credentials deleted via admin company edit",
        company_id=company_id,
        user_id=current_user.get("id"),
    )
    return _main()._company_edit_redirect(
        company_id=company_id,
        success="Microsoft 365 credentials removed.",
    )


async def admin_company_tray_settings_page(
    company_id: int,
    request: Request,
    new_token: str | None = None,
    success: str | None = Query(default=None),
    error: str | None = Query(default=None),
):
    from app.repositories import companies as companies_repo
    from app.repositories import tray as tray_repo

    current_user, redirect = await _main()._require_super_admin_page(request)
    if redirect:
        return redirect

    company = await companies_repo.get_company_by_id(company_id)
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")

    tokens = await tray_repo.list_install_tokens(company_id=company_id)
    extra = {
        "title": f"Tray settings — {company.get('name') or 'company'}",
        "company": company,
        "tokens": tokens,
        "new_token": new_token,
        "now_iso": datetime.now(timezone.utc).isoformat(),
        "portal_url": str(request.base_url).rstrip("/"),
        "success_message": _main()._sanitize_message(success),
        "error_message": _main()._sanitize_message(error),
    }
    return await _main()._render_template(
        "admin/tray/company_settings.html", request, current_user, extra=extra
    )


async def admin_company_tray_settings_save(company_id: int, request: Request):
    from app.repositories import companies as companies_repo

    current_user, redirect = await _main()._require_super_admin_page(request)
    if redirect:
        return redirect

    form = await request.form()

    company = await companies_repo.get_company_by_id(company_id)
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")

    tray_chat_enabled = 1 if form.get("tray_chat_enabled") else 0
    tray_notifications_enabled = 1 if form.get("tray_notifications_enabled") else 0

    await companies_repo.update_company(
        company_id,
        tray_chat_enabled=tray_chat_enabled,
        tray_notifications_enabled=tray_notifications_enabled,
    )
    cid = int(company_id)
    return RedirectResponse(
        url=f"/admin/companies/{cid}/tray?"
        + urlencode({"success": "Tray settings saved."}),
        status_code=303,
    )


async def admin_company_tray_create_token(company_id: int, request: Request):
    from app.repositories import companies as companies_repo
    from app.repositories import tray as tray_repo
    from app.services import tray as tray_service

    current_user, redirect = await _main()._require_super_admin_page(request)
    if redirect:
        return redirect

    form = await request.form()

    company = await companies_repo.get_company_by_id(company_id)
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")

    label = (
        str(form.get("label", "")).strip() or f"{company.get('name') or 'Company'} token"
    )[:150]
    raw_token = tray_service.generate_install_token()
    await tray_repo.create_install_token(
        label=label,
        company_id=company_id,
        token_hash=tray_service.hash_token(raw_token),
        token_prefix=tray_service.token_prefix(raw_token),
        created_by_user_id=int(current_user["id"]),
    )
    cid = int(company_id)
    return RedirectResponse(
        url=f"/admin/companies/{cid}/tray?" + urlencode({"new_token": raw_token}),
        status_code=303,
    )


async def admin_company_tray_revoke_token(
    company_id: int, token_id: int, request: Request
):
    from app.repositories import tray as tray_repo

    current_user, redirect = await _main()._require_super_admin_page(request)
    if redirect:
        return redirect

    await tray_repo.revoke_install_token(token_id)
    cid = int(company_id)
    return RedirectResponse(
        url=f"/admin/companies/{cid}/tray?"
        + urlencode({"success": "Token revoked."}),
        status_code=303,
    )
