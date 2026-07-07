"""Company admin handlers for the ``companies`` feature pack."""

from __future__ import annotations

import asyncio
import json
import re
import secrets
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlencode

from fastapi import HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

import aiomysql


def _main():
    from app import main as main_module

    return main_module


_COMPANY_PERMISSION_COLUMNS: list[dict[str, str]] = [
    {"field": "can_access_shop", "label": "Shop"},
    {"field": "can_access_cart", "label": "Cart"},
    {"field": "can_access_orders", "label": "Orders"},
    {"field": "can_access_quotes", "label": "Quotes"},
    {"field": "can_access_forms", "label": "Forms"},
    {"field": "can_manage_assets", "label": "Assets"},
    {"field": "can_manage_licenses", "label": "Licenses"},
    {"field": "can_manage_invoices", "label": "Invoices"},
    {"field": "can_manage_office_groups", "label": "Office groups"},
    {"field": "can_manage_issues", "label": "Issue tracker"},
    {"field": "can_order_licenses", "label": "Order licenses"},
    {"field": "is_admin", "label": "Company admin"},
]

_STAFF_PERMISSION_NONE = 0
_STAFF_PERMISSION_DEPARTMENT = 1
_STAFF_PERMISSION_ALL = 3

_STAFF_PERMISSION_OPTIONS: list[dict[str, Any]] = [
    {"value": _STAFF_PERMISSION_NONE, "label": "None"},
    {"value": _STAFF_PERMISSION_DEPARTMENT, "label": "Department"},
    {"value": _STAFF_PERMISSION_ALL, "label": "All"},
]


def _normalize_staff_access_scope(value: Any) -> int:
    try:
        permission_value = int(value or 0)
    except (TypeError, ValueError):
        return _STAFF_PERMISSION_NONE
    if permission_value <= 0:
        return _STAFF_PERMISSION_NONE
    if permission_value >= _STAFF_PERMISSION_ALL:
        return _STAFF_PERMISSION_ALL
    return _STAFF_PERMISSION_DEPARTMENT


async def _get_company_management_scope(
    request: Request,
    user: dict[str, Any],
    include_archived: bool = False,
) -> tuple[bool, list[dict[str, Any]], dict[int, dict[str, Any]]]:
    from app.repositories import companies as company_repo
    from app.repositories import user_companies as user_company_repo

    is_super_admin = bool(user.get("is_super_admin"))
    if is_super_admin:
        companies = await company_repo.list_companies(include_archived=include_archived)
        companies.sort(key=lambda item: (item.get("name") or "").lower())
        return True, companies, {}

    memberships = await user_company_repo.list_companies_for_user(user["id"])
    membership_lookup: dict[int, dict[str, Any]] = {}
    for record in memberships:
        raw_company_id = record.get("company_id")
        try:
            company_id = int(raw_company_id)
        except (TypeError, ValueError):
            continue
        if _main()._membership_menu_can(user, record, "menu.admin.company"):
            membership_lookup[company_id] = record

    if not membership_lookup:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Access denied"
        )

    companies: list[dict[str, Any]] = []
    for company_id in sorted(membership_lookup.keys()):
        company = await company_repo.get_company_by_id(company_id)
        if company:
            # Filter archived companies for non-super admins unless explicitly requested
            if include_archived or not company.get("archived"):
                companies.append(company)

    if not companies:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Access denied"
        )

    return False, companies, membership_lookup


async def _render_companies_dashboard(
    request: Request,
    user: dict[str, Any],
    *,
    selected_company_id: int | None = None,
    success_message: str | None = None,
    error_message: str | None = None,
    temporary_password: str | None = None,
    invited_email: str | None = None,
    include_archived: bool = False,
    status_code: int = status.HTTP_200_OK,
) -> HTMLResponse:
    from app.repositories import m365 as m365_repo
    from app.repositories import roles as role_repo
    from app.repositories import user_companies as user_company_repo

    is_super_admin, managed_companies, membership_lookup = (
        await _get_company_management_scope(
            request, user, include_archived=include_archived
        )
    )

    company_ids_with_rows: list[tuple[int, dict[str, Any]]] = []
    for company in managed_companies:
        raw_company_id = company.get("id")
        if raw_company_id is None:
            continue
        try:
            company_id = int(raw_company_id)
        except (TypeError, ValueError):
            continue
        company_ids_with_rows.append((company_id, company))

    if company_ids_with_rows:
        credentials_rows = await asyncio.gather(
            *(
                m365_repo.get_credentials(company_id)
                for company_id, _ in company_ids_with_rows
            )
        )
        for (_, company), credentials in zip(company_ids_with_rows, credentials_rows):
            company["m365_tenant_id"] = (credentials or {}).get("tenant_id", "").strip()

    ordered_company_ids: list[int] = [
        int(company["id"])
        for company in managed_companies
        if company.get("id") is not None
    ]

    effective_company_id = selected_company_id
    if (
        effective_company_id is not None
        and effective_company_id not in ordered_company_ids
    ):
        effective_company_id = ordered_company_ids[0] if ordered_company_ids else None

    if effective_company_id is None and not is_super_admin and ordered_company_ids:
        active_company_raw = getattr(request.state, "active_company_id", None)
        try:
            active_company_candidate = int(active_company_raw)
        except (TypeError, ValueError):
            active_company_candidate = None
        if active_company_candidate in ordered_company_ids:
            effective_company_id = active_company_candidate
        else:
            effective_company_id = ordered_company_ids[0]

    if is_super_admin and effective_company_id is None:
        assignments = await user_company_repo.list_assignments()
    elif effective_company_id is not None:
        assignments = await user_company_repo.list_assignments(effective_company_id)
    else:
        assignments = []

    role_rows = await role_repo.list_roles()
    role_options: list[dict[str, Any]] = []
    for record in role_rows:
        role_id = record.get("id")
        name = (record.get("name") or "").strip()
        if role_id is None or not name:
            continue
        role_options.append(
            {
                "id": int(role_id),
                "name": name,
                "description": (record.get("description") or "").strip(),
                "is_system": bool(record.get("is_system")),
            }
        )

    extra = {
        "title": "Company administration",
        "managed_companies": managed_companies,
        "selected_company_id": effective_company_id,
        "assignments": assignments,
        "permission_columns": _COMPANY_PERMISSION_COLUMNS,
        "staff_permission_options": _STAFF_PERMISSION_OPTIONS,
        "role_options": role_options,
        "is_super_admin": is_super_admin,
        "success_message": success_message,
        "error_message": error_message,
        "temporary_password": temporary_password,
        "invited_email": invited_email,
        "show_archived": include_archived,
        "admin_credentials_configured": bool(
            all(await _main()._get_m365_admin_credentials())
        ),
    }

    response = await _main()._render_template(
        "admin/companies.html", request, user, extra=extra
    )
    response.status_code = status_code
    return response


async def _render_company_edit_page(
    request: Request,
    user: dict[str, Any],
    *,
    company_id: int,
    form_values: Mapping[str, Any] | None = None,
    assign_form_values: Mapping[str, Any] | None = None,
    success_message: str | None = None,
    error_message: str | None = None,
    status_code: int = status.HTTP_200_OK,
    show_inactive_tasks: bool = False,
) -> HTMLResponse:
    from app.repositories import billing_contacts as billing_contacts_repo
    from app.repositories import companies as company_repo
    from app.repositories import pending_staff_access as pending_staff_access_repo
    from app.repositories import company_recurring_invoice_items as recurring_items_repo
    from app.repositories import roles as role_repo
    from app.repositories import scheduled_tasks as scheduled_tasks_repo
    from app.repositories import staff as staff_repo
    from app.repositories import staff_custom_fields as staff_custom_fields_repo
    from app.repositories import user_companies as user_company_repo
    from app.services import m365 as m365_service
    from app.services import modules as modules_service
    from app.services import staff_field_config as staff_field_config_service
    from app.services.scheduler import COMMANDS_BY_MODULE

    company_record = await company_repo.get_company_by_id(company_id)
    if not company_record:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Company not found"
        )

    is_super_admin, managed_companies, _ = await _get_company_management_scope(
        request, user
    )

    assignments: list[dict[str, Any]] = []
    role_options: list[dict[str, Any]] = []
    company_user_options: dict[int, list[dict[str, Any]]] = {}
    if is_super_admin:
        assignments = await user_company_repo.list_assignments(company_id)
        for entry in assignments:
            entry["is_pending"] = False
            entry["pending_requires_account"] = False

        role_rows = await role_repo.list_roles()
        role_lookup: dict[int, str] = {}
        for record in role_rows:
            role_id = record.get("id")
            name = (record.get("name") or "").strip()
            if role_id is None or not name:
                continue
            try:
                role_id_int = int(role_id)
            except (TypeError, ValueError):
                continue
            role_lookup[role_id_int] = name
            role_options.append(
                {
                    "id": role_id_int,
                    "name": name,
                    "description": (record.get("description") or "").strip(),
                    "is_system": bool(record.get("is_system")),
                }
            )

        staff_directory: dict[int, list[dict[str, Any]]] = {}
        pending_assignments_map: dict[int, list[dict[str, Any]]] = {}
        for managed in managed_companies:
            raw_id = managed.get("id")
            try:
                managed_company_id = int(raw_id)
            except (TypeError, ValueError):
                continue
            staff_rows = await staff_repo.list_staff_with_users(managed_company_id)
            staff_directory[managed_company_id] = staff_rows
            pending_assignments = (
                await pending_staff_access_repo.list_assignments_for_company(
                    managed_company_id
                )
            )
            pending_assignments_map[managed_company_id] = pending_assignments
            pending_lookup = {
                entry.get("staff_id"): entry
                for entry in pending_assignments
                if entry.get("staff_id") is not None
            }
            options: list[dict[str, Any]] = []
            for row in staff_rows:
                staff_id = row.get("staff_id")
                email = (row.get("email") or "").strip()
                if staff_id is None or not email:
                    continue
                first_name = (row.get("first_name") or "").strip()
                last_name = (row.get("last_name") or "").strip()
                name_parts = [part for part in (first_name, last_name) if part]
                has_name = bool(name_parts)
                label: str
                if has_name and email:
                    label = f"{' '.join(name_parts)} ({email})"
                elif has_name:
                    label = " ".join(name_parts)
                else:
                    label = email
                user_id_value = row.get("user_id")
                has_user = user_id_value is not None
                option_value: str
                if has_user:
                    try:
                        numeric_user_id = int(user_id_value)
                    except (TypeError, ValueError):
                        numeric_user_id = None
                else:
                    numeric_user_id = None
                if numeric_user_id is not None:
                    option_value = str(numeric_user_id)
                else:
                    option_value = f"staff:{int(staff_id)}"
                if not row.get("enabled", True):
                    label = f"{label} (inactive)"
                pending_assignment = pending_lookup.get(int(staff_id))
                if numeric_user_id is None:
                    if pending_assignment:
                        label = f"{label} – access pending sign-up"
                    else:
                        label = f"{label} – invite required"
                options.append(
                    {
                        "value": option_value,
                        "label": label,
                        "email": email,
                        "staff_id": int(staff_id),
                        "user_id": numeric_user_id,
                        "has_user": numeric_user_id is not None,
                        "pending_access": bool(pending_assignment),
                    }
                )
            options.sort(key=lambda item: item.get("label", "").lower())
            company_user_options[managed_company_id] = options

        permission_label_lookup: dict[int, str] = {}
        for option in _STAFF_PERMISSION_OPTIONS:
            value = option.get("value")
            label = option.get("label")
            if value is None or label is None:
                continue
            try:
                permission_label_lookup[int(value)] = str(label)
            except (TypeError, ValueError):
                continue

        pending_entries = pending_assignments_map.get(company_id)
        if pending_entries is None:
            pending_entries = (
                await pending_staff_access_repo.list_assignments_for_company(company_id)
            )
        staff_rows_current = staff_directory.get(company_id)
        if staff_rows_current is None:
            staff_rows_current = await staff_repo.list_staff_with_users(company_id)
        staff_lookup: dict[int, dict[str, Any]] = {}
        for staff_entry in staff_rows_current:
            staff_id = staff_entry.get("staff_id")
            if staff_id is None:
                continue
            try:
                staff_lookup[int(staff_id)] = staff_entry
            except (TypeError, ValueError):
                continue

        existing_user_ids: set[int] = set()
        for assignment in assignments:
            user_id = assignment.get("user_id")
            if user_id is None:
                continue
            try:
                existing_user_ids.add(int(user_id))
            except (TypeError, ValueError):
                continue

        for pending_entry in pending_entries or []:
            staff_id_raw = pending_entry.get("staff_id")
            if staff_id_raw is None:
                continue
            try:
                staff_id_int = int(staff_id_raw)
            except (TypeError, ValueError):
                continue

            staff_info = staff_lookup.get(staff_id_int, {})
            user_id_value: int | None = None
            if staff_info.get("user_id") is not None:
                try:
                    user_id_value = int(staff_info.get("user_id"))
                except (TypeError, ValueError):
                    user_id_value = None
            if user_id_value is not None and user_id_value in existing_user_ids:
                continue

            email = (staff_info.get("email") or "").strip()
            if not email:
                email = f"Staff #{staff_id_int}"
            first_name = (staff_info.get("first_name") or "").strip()
            last_name = (staff_info.get("last_name") or "").strip()

            role_id_raw = pending_entry.get("role_id")
            role_id_value: int | None = None
            if role_id_raw is not None:
                try:
                    role_id_value = int(role_id_raw)
                except (TypeError, ValueError):
                    role_id_value = None

            staff_permission_value = _normalize_staff_access_scope(
                pending_entry.get("staff_permission")
            )

            pending_record: dict[str, Any] = {
                "company_id": pending_entry.get("company_id") or company_id,
                "user_id": user_id_value,
                "staff_id": staff_id_int,
                "email": email,
                "first_name": first_name,
                "last_name": last_name,
                "membership_id": None,
                "membership_role_id": role_id_value,
                "membership_role_name": (
                    role_lookup.get(role_id_value)
                    if role_id_value is not None
                    else None
                ),
                "staff_permission": staff_permission_value,
                "staff_permission_label": permission_label_lookup.get(
                    staff_permission_value,
                    permission_label_lookup.get(0, "No staff access"),
                ),
                "can_manage_staff": bool(pending_entry.get("can_manage_staff", False)),
                "is_pending": True,
                "pending_requires_account": user_id_value is None,
            }

            for column in _COMPANY_PERMISSION_COLUMNS:
                field = column.get("field")
                if not field:
                    continue
                pending_record[field] = bool(pending_entry.get(field, False))

            assignments.append(pending_record)

        assignments.sort(
            key=lambda item: (
                (item.get("email") or "").lower(),
                1 if item.get("is_pending") else 0,
                item.get("user_id") or 0,
            )
        )

    def _string_value(key: str, default: str) -> str:
        if not form_values or key not in form_values:
            return default
        value = form_values.get(key)
        return str(value) if value is not None else ""

    def _bool_value(key: str, default: bool) -> bool:
        if not form_values or key not in form_values:
            return default
        value = form_values.get(key)
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return bool(value)

    default_email_domains = ", ".join(company_record.get("email_domains") or [])
    form_data = {
        "name": _string_value("name", (company_record.get("name") or "").strip()),
        "syncro_company_id": _string_value(
            "syncro_company_id", (company_record.get("syncro_company_id") or "").strip()
        ),
        "tacticalrmm_client_id": _string_value(
            "tacticalrmm_client_id",
            (company_record.get("tacticalrmm_client_id") or "").strip(),
        ),
        "xero_id": _string_value(
            "xero_id", (company_record.get("xero_id") or "").strip()
        ),
        "hudu_id": _string_value(
            "hudu_id", (company_record.get("hudu_id") or "").strip()
        ),
        "huntress_organization_id": _string_value(
            "huntress_organization_id",
            (company_record.get("huntress_organization_id") or "").strip(),
        ),
        "email_domains": _string_value("email_domains", default_email_domains),
        "is_vip": _bool_value("is_vip", bool(company_record.get("is_vip"))),
        "payment_method": _string_value(
            "payment_method",
            (company_record.get("payment_method") or "invoice_prepay").strip(),
        ),
        "require_po": _bool_value("require_po", bool(company_record.get("require_po"))),
        "offboarding_email_forwarding_enabled": _bool_value(
            "offboarding_email_forwarding_enabled",
            bool(
                int(company_record.get("offboarding_email_forwarding_enabled", 1) or 1)
            ),
        ),
        "onedrive_export_site_id": _string_value(
            "onedrive_export_site_id",
            (company_record.get("onedrive_export_site_id") or "").strip(),
        ),
        "onedrive_export_drive_id": _string_value(
            "onedrive_export_drive_id",
            (company_record.get("onedrive_export_drive_id") or "").strip(),
        ),
        "onedrive_export_site_name": _string_value(
            "onedrive_export_site_name",
            (company_record.get("onedrive_export_site_name") or "").strip(),
        ),
        "trello_board_id": _string_value(
            "trello_board_id", (company_record.get("trello_board_id") or "").strip()
        ),
        "trello_api_key": _string_value(
            "trello_api_key", (company_record.get("trello_api_key") or "").strip()
        ),
        "trello_token": _string_value(
            "trello_token", (company_record.get("trello_token") or "").strip()
        ),
    }

    form_email_text = form_data.get("email_domains", "")
    if form_values and "email_domains" in form_values:
        preview_domains = [
            domain.strip()
            for domain in form_email_text.replace("\n", ",").split(",")
            if domain.strip()
        ]
    else:
        preview_domains = list(company_record.get("email_domains") or [])

    assign_values = assign_form_values or {}

    def _assign_int(key: str, default: int | None = None) -> int | None:
        if key not in assign_values:
            return default
        value = assign_values.get(key)
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, (int, float)):
            try:
                return int(value)
            except (TypeError, ValueError):
                return default
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return default
            try:
                return int(text)
            except ValueError:
                return default
        return default

    def _assign_bool(key: str, default: bool = False) -> bool:
        if key not in assign_values:
            return default
        value = assign_values.get(key)
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return bool(value)
        if isinstance(value, str):
            candidate = value.strip().lower()
            if not candidate:
                return False
            return candidate in {"1", "true", "yes", "on"}
        return default

    assign_company_id = _assign_int("company_id", company_id) or company_id
    raw_assign_user_value: str = ""
    if "user_value" in assign_values:
        value = assign_values.get("user_value")
        raw_assign_user_value = str(value).strip() if value is not None else ""
    elif "user_id" in assign_values:
        value = assign_values.get("user_id")
        raw_assign_user_value = str(value).strip() if value is not None else ""
    assign_user_value = raw_assign_user_value
    assign_user_id: int | None = None
    if assign_user_value:
        try:
            assign_user_id = int(assign_user_value)
        except ValueError:
            assign_user_id = None
    assign_role_id = _assign_int("role_id")
    assign_staff_permission = _normalize_staff_access_scope(
        _assign_int("staff_permission", 0)
    )
    assign_can_manage_staff = _assign_bool("can_manage_staff", False)
    assign_permissions: dict[str, bool] = {}
    for column in _COMPANY_PERMISSION_COLUMNS:
        field = column.get("field")
        if not field:
            continue
        assign_permissions[field] = _assign_bool(field, False)

    assign_user_options = (
        company_user_options.get(assign_company_id, []) if is_super_admin else []
    )

    company_automation_tasks: list[dict[str, Any]] = []
    automation_command_options: list[dict[str, str]] = []
    automation_company_options: list[dict[str, str]] = []

    if is_super_admin:
        # Build the set of commands that belong to disabled modules so they can be excluded.
        try:
            all_modules = await modules_service.list_modules()
            disabled_module_slugs = {
                m["slug"] for m in all_modules if not m.get("enabled")
            }
        except Exception:  # pragma: no cover - defensive fallback
            disabled_module_slugs = set()
        disabled_commands: set[str] = set()
        for mod_slug, cmds in COMMANDS_BY_MODULE.items():
            if mod_slug in disabled_module_slugs:
                disabled_commands.update(cmds)

        automation_command_options = [
            {"value": "sync_staff", "label": "Sync staff directory"},
            {"value": "sync_m365_data", "label": "Sync Microsoft 365 data (legacy)"},
            {"value": "sync_m365_licenses", "label": "Sync Microsoft 365 licenses"},
            {"value": "sync_m365_contacts", "label": "Sync Microsoft 365 contacts"},
            {"value": "sync_m365_mailboxes", "label": "Sync Microsoft 365 mailboxes"},
            {"value": "sync_huntress", "label": "Sync Huntress data"},
            {"value": "sync_to_xero", "label": "Sync to Xero"},
            {"value": "sync_to_xero_auto_send", "label": "Sync to Xero (Auto Send)"},
            {"value": "generate_invoice", "label": "Generate Invoice"},
            {"value": "unbill_time_entries", "label": "Un-Bill Time Entries"},
            {"value": "create_scheduled_ticket", "label": "Create scheduled ticket"},
            {"value": "sync_recordings", "label": "Sync call recordings"},
            {
                "value": "sync_unifi_talk_recordings",
                "label": "Sync Unifi Talk recordings",
            },
            {"value": "queue_transcriptions", "label": "Queue transcriptions"},
            {"value": "process_transcription", "label": "Process transcription"},
        ]
        automation_command_options = [
            o for o in automation_command_options if o["value"] not in disabled_commands
        ]
        default_command_values = {
            option["value"] for option in automation_command_options
        }

        try:
            tasks = await scheduled_tasks_repo.list_tasks(
                include_inactive=show_inactive_tasks
            )
        except Exception:  # pragma: no cover - fallback to keep page rendering
            tasks = []

        existing_commands: set[str] = set()
        for task in tasks:
            command_value = task.get("command")
            if command_value:
                existing_commands.add(str(command_value))

            raw_company_id = task.get("company_id")
            try:
                task_company_id = (
                    int(raw_company_id) if raw_company_id is not None else None
                )
            except (TypeError, ValueError):
                task_company_id = None

            if task_company_id != company_id:
                continue

            serialised_task = _main()._serialise_mapping(task)
            serialised_task["last_run_iso"] = _main()._to_iso(task.get("last_run_at"))
            serialised_task["company_name"] = (
                company_record.get("name") or ""
            ).strip() or f"Company #{company_id}"
            company_automation_tasks.append(serialised_task)

        for command in sorted(existing_commands):
            if (
                command
                and command not in default_command_values
                and command not in disabled_commands
            ):
                automation_command_options.append({"value": command, "label": command})

        automation_company_options = [
            {
                "value": str(company_id),
                "label": (company_record.get("name") or "").strip()
                or f"Company #{company_id}",
            }
        ]

        company_automation_tasks.sort(key=lambda item: (item.get("name") or "").lower())

    # Fetch recurring invoice items for the company
    recurring_invoice_items = []
    if is_super_admin:
        try:
            items = await recurring_items_repo.list_company_recurring_invoice_items(
                company_id
            )
        except RuntimeError as exc:  # pragma: no cover - defensive guard for tests
            if "Database pool not initialised" in str(exc):
                items = []
            else:
                raise
        for item in items:
            recurring_invoice_items.append(_main()._serialise_mapping(item))

    # Fetch billing contacts for the company
    billing_contacts = []
    company_staff = []
    if is_super_admin:
        try:
            billing_contacts = (
                await billing_contacts_repo.list_billing_contacts_for_company(
                    company_id
                )
            )
        except RuntimeError as exc:  # pragma: no cover - defensive guard for tests
            if "Database pool not initialised" in str(exc):
                billing_contacts = []
            else:
                raise
        # Get all staff for this company for the dropdown
        try:
            company_staff = await staff_repo.list_staff(company_id)
        except RuntimeError as exc:  # pragma: no cover - defensive guard for tests
            if "Database pool not initialised" in str(exc):
                company_staff = []
            else:
                raise

    # Fetch Microsoft 365 credentials for the company
    m365_credential_view: dict[str, Any] | None = None
    if is_super_admin:
        try:
            m365_creds = await m365_service.get_credentials(company_id)
            if m365_creds:
                expires = m365_creds.get("token_expires_at")
                if isinstance(expires, datetime):
                    expires_display = expires.replace(tzinfo=timezone.utc).isoformat()
                elif expires:
                    expires_display = str(expires)
                else:
                    expires_display = None
                m365_credential_view = {
                    "tenant_id": m365_creds.get("tenant_id"),
                    "client_id": m365_creds.get("client_id"),
                    "token_expires_at": expires_display,
                }
        except RuntimeError as exc:  # pragma: no cover - defensive guard for tests
            if "Database pool not initialised" in str(exc):
                pass
            else:
                raise

    staff_field_config: list[dict[str, Any]] = []
    staff_custom_field_definitions: list[dict[str, Any]] = []
    if is_super_admin:
        staff_field_config = (
            await staff_field_config_service.load_effective_company_staff_fields(
                company_id
            )
        )
        staff_custom_field_definitions = (
            await staff_custom_fields_repo.list_company_owned_definitions(company_id)
        )

    # Fetch tray install tokens for this company
    tray_tokens: list[dict[str, Any]] = []
    if is_super_admin:
        try:
            from app.repositories import tray as tray_repo

            tray_tokens = await tray_repo.list_install_tokens(company_id=company_id)
        except RuntimeError as exc:  # pragma: no cover - defensive guard for tests
            if "Database pool not initialised" in str(exc):
                tray_tokens = []
            else:
                raise

    assign_form = {
        "company_id": assign_company_id,
        "user_id": assign_user_id,
        "user_value": assign_user_value,
        "role_id": assign_role_id,
        "staff_permission": assign_staff_permission,
        "can_manage_staff": assign_can_manage_staff,
        "permissions": assign_permissions,
    }

    extra = {
        "title": f"Edit {company_record.get('name') or 'company'}",
        "company": company_record,
        "form_data": form_data,
        "managed_companies": managed_companies,
        "is_super_admin": is_super_admin,
        "assignments": assignments,
        "permission_columns": _COMPANY_PERMISSION_COLUMNS,
        "staff_permission_options": _STAFF_PERMISSION_OPTIONS,
        "role_options": role_options,
        "success_message": success_message,
        "error_message": error_message,
        "email_domain_preview": preview_domains,
        "assign_form": assign_form,
        "company_user_options": company_user_options,
        "assign_user_options": assign_user_options,
        "company_automation_tasks": company_automation_tasks,
        "automation_command_options": automation_command_options,
        "automation_company_options": automation_company_options,
        "recurring_invoice_items": recurring_invoice_items,
        "billing_contacts": billing_contacts,
        "company_staff": company_staff,
        "show_inactive_tasks": show_inactive_tasks,
        "m365_credential": m365_credential_view,
        "m365_has_credentials": m365_credential_view is not None,
        "m365_admin_credentials_configured": bool(
            all(await _main()._get_m365_admin_credentials(company_id))
        ),
        "staff_field_config": staff_field_config,
        "staff_custom_field_definitions": staff_custom_field_definitions,
        "tray_tokens": tray_tokens,
    }

    response = await _main()._render_template(
        "admin/company_edit.html", request, user, extra=extra
    )
    response.status_code = status_code
    return response


def _parse_custom_field_options(options_text: str) -> list[dict[str, str]]:
    options: list[dict[str, str]] = []
    for part in (options_text or "").split(","):
        item = part.strip()
        if not item:
            continue
        m365_upn = ""
        if "|" in item:
            item, m365_upn = item.split("|", 1)
            item = item.strip()
            m365_upn = m365_upn.strip().lower()
        if ":" in item:
            value_part, label_part = item.split(":", 1)
            value = value_part.strip()
            label = label_part.strip() or value
        else:
            value = item
            label = item
        if not value:
            continue
        options.append({"value": value, "label": label, "m365_upn": m365_upn})
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
                return (
                    parent_name,
                    operator,
                    json.dumps(parsed_map, separators=(",", ":")),
                )
        return parent_name, operator, normalized_condition_value or None
    if operator in {"is_checked", "is_not_checked"}:
        normalized_condition_value = None
    if operator in {"equals", "not_equals"} and not normalized_condition_value:
        fallback_operator = "is_checked" if operator == "equals" else "is_not_checked"
        return parent_name, fallback_operator, None
    return parent_name, operator, normalized_condition_value or None


def _normalize_staff_custom_field_visibility(value: Any) -> str | None:
    entries: list[str] = []
    seen: set[str] = set()
    for part in str(value or "").replace(";", ",").split(","):
        item = part.strip()
        if not item:
            continue
        key = item.lower()
        if key in seen:
            continue
        seen.add(key)
        entries.append(item)
    return ", ".join(entries) or None


async def _ensure_company_permission(
    request: Request,
    user: dict[str, Any],
    company_id: int,
    *,
    require_admin: bool = False,
    require_staff_manager: bool = False,
) -> None:
    is_super_admin, _, membership_lookup = await _get_company_management_scope(
        request, user
    )
    if is_super_admin:
        return
    membership = membership_lookup.get(company_id)
    if not membership:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Access denied"
        )
    staff_permission = _normalize_staff_access_scope(membership.get("staff_permission"))
    if require_admin and not bool(membership.get("is_admin")):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Access denied"
        )
    if (
        require_staff_manager
        and staff_permission < _STAFF_PERMISSION_ALL
        and not bool(membership.get("can_manage_staff"))
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Access denied"
        )


async def admin_companies_page(
    request: Request,
    company_id: int | None = Query(default=None),
    show_archived: bool = Query(default=False),
):
    current_user, redirect = await _main()._require_authenticated_user(request)
    if redirect:
        return redirect
    return await _render_companies_dashboard(
        request,
        current_user,
        selected_company_id=company_id,
        include_archived=show_archived,
    )


async def admin_company_edit_page(
    company_id: int,
    request: Request,
    show_inactive: bool = Query(default=False),
):
    current_user, redirect = await _main()._require_super_admin_page(request)
    if redirect:
        return redirect
    return await _render_company_edit_page(
        request,
        current_user,
        company_id=company_id,
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
        return await _render_companies_dashboard(
            request,
            current_user,
            error_message=str(exc),
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    if not name:
        return await _render_companies_dashboard(
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
        return await _render_companies_dashboard(
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
    for column in _COMPANY_PERMISSION_COLUMNS:
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
        return await _render_company_edit_page(
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
            staff_permission = _normalize_staff_access_scope(staff_permission_raw)

            permission_values: dict[str, bool] = {}
            for column in _COMPANY_PERMISSION_COLUMNS:
                field = column.get("field")
                if not field:
                    continue
                permission_values[field] = (
                    _main()._parse_bool(form.get(field))
                    if field in form_keys
                    else False
                )

            if "can_manage_staff" in form_keys:
                can_manage_staff_value = _main()._parse_bool(
                    form.get("can_manage_staff")
                )
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

            success_message = f"Saved pending access for {email}. Permissions will activate after sign-up."
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

    staff_permission = _normalize_staff_access_scope(staff_permission_raw)
    assign_form_state["staff_permission"] = staff_permission

    permission_values: dict[str, bool] = {}
    for column in _COMPANY_PERMISSION_COLUMNS:
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
                await membership_repo.update_membership(
                    int(membership_id), role_id=role_id
                )

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
    onedrive_export_selection_raw = str(form.get("onedriveExportSite") or "").strip()
    onedrive_export_site_id_raw = ""
    onedrive_export_drive_id_raw = ""
    onedrive_export_site_name_raw = ""
    if onedrive_export_selection_raw:
        try:
            selection_data = json.loads(onedrive_export_selection_raw)
        except json.JSONDecodeError:
            selection_data = {}
        if isinstance(selection_data, dict):
            onedrive_export_site_id_raw = str(
                selection_data.get("site_id") or ""
            ).strip()
            onedrive_export_drive_id_raw = str(
                selection_data.get("drive_id") or ""
            ).strip()
            onedrive_export_site_name_raw = str(
                selection_data.get("site_name") or ""
            ).strip()[:255]
    _selected_methods = [
        m
        for m, enabled in [
            ("invoice_prepay", invoice_prepay_enabled),
            ("invoice_postpay", invoice_postpay_enabled),
            ("stripe", stripe_enabled),
        ]
        if enabled
    ]
    payment_method = (
        ",".join(_selected_methods) if _selected_methods else "invoice_prepay"
    )
    raw_email_domains = form.get("emailDomains")
    email_domains_text = str(raw_email_domains) if raw_email_domains is not None else ""
    existing = await company_repo.get_company_by_id(company_id)
    if not existing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Company not found"
        )
    if "onedriveExportSite" not in form:
        onedrive_export_site_id_raw = str(
            existing.get("onedrive_export_site_id") or ""
        ).strip()
        onedrive_export_drive_id_raw = str(
            existing.get("onedrive_export_drive_id") or ""
        ).strip()
        onedrive_export_site_name_raw = str(
            existing.get("onedrive_export_site_name") or ""
        ).strip()[:255]
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
        "onedrive_export_site_id": onedrive_export_site_id_raw,
        "onedrive_export_drive_id": onedrive_export_drive_id_raw,
        "onedrive_export_site_name": onedrive_export_site_name_raw,
    }
    try:
        email_domains = company_domains.parse_email_domain_text(email_domains_text)
    except company_domains.EmailDomainError as exc:
        return await _render_company_edit_page(
            request,
            current_user,
            company_id=company_id,
            form_values=form_values,
            error_message=str(exc),
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    if not name:
        return await _render_company_edit_page(
            request,
            current_user,
            company_id=company_id,
            form_values=form_values,
            error_message="Enter a company name.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    if onedrive_export_selection_raw and (
        not onedrive_export_site_id_raw or not onedrive_export_drive_id_raw
    ):
        return await _render_company_edit_page(
            request,
            current_user,
            company_id=company_id,
            form_values=form_values,
            error_message="Select a valid SharePoint site for OneDrive exports.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    syncro_company_id = syncro_company_raw or None
    tactical_client_id = tactical_client_raw or None
    xero_id = xero_id_raw or None
    hudu_id = hudu_id_raw or None
    huntress_organization_id = huntress_organization_id_raw or None
    trello_board_id = trello_board_id_raw or None
    trello_api_key: str | None = (
        trello_api_key_raw
        if trello_api_key_raw
        else (existing.get("trello_api_key") or None)
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
        "offboarding_email_forwarding_enabled": (
            1 if offboarding_email_forwarding_enabled else 0
        ),
        "onedrive_export_site_id": onedrive_export_site_id_raw or None,
        "onedrive_export_site_name": onedrive_export_site_name_raw or None,
        "onedrive_export_drive_id": onedrive_export_drive_id_raw or None,
    }
    try:
        await company_repo.update_company(company_id, **updates)
    except Exception as exc:  # pragma: no cover - defensive logging
        log_error("Failed to update company", company_id=company_id, error=str(exc))
        error_message = "Unable to update company. Please try again."
        if (
            isinstance(exc, aiomysql.IntegrityError)
            and exc.args
            and exc.args[0] == 1062
        ):
            match = re.search(r"Duplicate entry '([^']+)'", str(exc))
            if match:
                error_message = f"The email domain '{match.group(1)}' is already assigned to another company."
            else:
                error_message = (
                    "One of the email domains is already assigned to another company."
                )
        return await _render_company_edit_page(
            request,
            current_user,
            company_id=company_id,
            form_values=form_values,
            error_message=error_message,
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
    if huntress_organization_id:
        existing_commands = await scheduled_tasks_repo.get_commands_for_company(
            company_id
        )
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
    visible_to_job_titles = _normalize_staff_custom_field_visibility(
        form.get("visible_to_job_titles")
    )
    visible_to_requester_emails = _normalize_staff_custom_field_visibility(
        form.get("visible_to_requester_emails")
    )
    options = _parse_custom_field_options(str(form.get("options") or ""))
    m365_upn = str(form.get("m365_upn") or "").strip().lower() or None
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
        visible_to_job_titles=visible_to_job_titles,
        visible_to_requester_emails=visible_to_requester_emails,
        m365_upn=m365_upn,
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
    visible_to_job_titles = _normalize_staff_custom_field_visibility(
        form.get("visible_to_job_titles")
    )
    visible_to_requester_emails = _normalize_staff_custom_field_visibility(
        form.get("visible_to_requester_emails")
    )
    options = _parse_custom_field_options(str(form.get("options") or ""))
    m365_upn = str(form.get("m365_upn") or "").strip().lower() or None
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
        visible_to_job_titles=visible_to_job_titles,
        visible_to_requester_emails=visible_to_requester_emails,
        m365_upn=m365_upn,
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
        return await _render_companies_dashboard(
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
        return await _render_companies_dashboard(
            request,
            current_user,
            selected_company_id=company_id,
            error_message="Enter an email address.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    if len(password) < 8:
        return await _render_companies_dashboard(
            request,
            current_user,
            selected_company_id=company_id,
            error_message="Enter a password of at least 8 characters.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    existing_user = await user_repo.get_user_by_email(email)
    if existing_user:
        return await _render_companies_dashboard(
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
        return await _render_companies_dashboard(
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
        return await _render_companies_dashboard(
            request,
            current_user,
            selected_company_id=company_id,
            error_message="Enter an email address.",
            status_code=status.HTTP_400_BAD_REQUEST,
        )
    existing_user = await user_repo.get_user_by_email(email)
    if existing_user:
        return await _render_companies_dashboard(
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
    return await _render_companies_dashboard(
        request,
        current_user,
        selected_company_id=company_id,
        success_message=f"Invitation generated for {email}.",
        temporary_password=temporary_password,
        invited_email=email,
    )


async def admin_update_company_permission(
    company_id: int, user_id: int, request: Request
):
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


async def admin_update_staff_permission(
    company_id: int, user_id: int, request: Request
):
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
        permission=_normalize_staff_access_scope(permission_value),
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

    membership = await membership_repo.get_membership_by_company_user(
        company_id, user_id
    )
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
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Role not found"
        )

    updated = await membership_repo.update_membership(
        int(membership_id), role_id=role_id
    )

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


async def admin_remove_company_assignment(
    company_id: int, user_id: int, request: Request
):
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


async def admin_remove_billing_contact(
    company_id: int, staff_id: int, request: Request
):
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
):
    from app.repositories import companies as companies_repo
    from app.repositories import tray as tray_repo
    from app.repositories import tray_ticket_questions as tq_repo

    current_user, redirect = await _main()._require_super_admin_page(request)
    if redirect:
        return redirect

    company = await companies_repo.get_company_by_id(company_id)
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")

    tokens = await tray_repo.list_install_tokens(company_id=company_id)
    company_questions = await tq_repo.list_questions(
        scope="company", company_id=company_id, active_only=False
    )
    portal_url = (
        str(_main().settings.portal_url).rstrip("/")
        if _main().settings.portal_url
        else str(request.base_url.replace(scheme="https")).rstrip("/")
    )
    extra = {
        "title": f"Tray settings — {company.get('name') or 'company'}",
        "company": company,
        "tokens": tokens,
        "new_token": new_token,
        "now_iso": datetime.now(timezone.utc).isoformat(),
        "portal_url": portal_url,
        "company_questions": company_questions,
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

    customer_chat_enabled = 1 if form.get("customer_chat_enabled") else 0
    tray_chat_enabled = 1 if form.get("tray_chat_enabled") else 0
    tray_notifications_enabled = 1 if form.get("tray_notifications_enabled") else 0

    await companies_repo.update_company(
        company_id,
        customer_chat_enabled=customer_chat_enabled,
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
        str(form.get("label", "")).strip()
        or f"{company.get('name') or 'Company'} token"
    )[:150]
    raw_token = tray_service.generate_install_token()
    await tray_repo.create_install_token(
        label=label,
        company_id=company_id,
        token_hash=tray_service.hash_token(raw_token),
        token_prefix=tray_service.token_prefix(raw_token),
        created_by_user_id=int(current_user["id"]),
    )
    trmm_client_id = str(company.get("tacticalrmm_client_id") or "").strip()
    if trmm_client_id:
        try:
            await tray_service.update_trmm_client_token_field(
                trmm_client_id=trmm_client_id,
                token=raw_token,
            )
        except Exception as exc:
            from app.core.logging import log_error

            log_error(
                "Failed to publish company tray token to Tactical RMM",
                company_id=company_id,
                error=str(exc),
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
        url=f"/admin/companies/{cid}/tray?" + urlencode({"success": "Token revoked."}),
        status_code=303,
    )


# ---------------------------------------------------------------------------
# Per-company ticket question handlers
# ---------------------------------------------------------------------------


async def _get_company_or_404(company_id: int):
    from app.repositories import companies as companies_repo

    company = await companies_repo.get_company_by_id(company_id)
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")
    return company


async def admin_company_ticket_question_new_page(company_id: int, request: Request):
    current_user, redirect = await _main()._require_super_admin_page(request)
    if redirect:
        return redirect
    company = await _get_company_or_404(company_id)
    from app.repositories import tray_ticket_questions as tq_repo

    # All questions available as parent candidates for conditions
    all_questions = await tq_repo.list_questions(active_only=False)
    extra = {
        "title": f"New question — {company.get('name')}",
        "company": company,
        "question": None,
        "all_questions": all_questions,
        "error_message": None,
    }
    return await _main()._render_template(
        "admin/tray/company_ticket_question_form.html",
        request,
        current_user,
        extra=extra,
    )


async def admin_company_ticket_question_create(company_id: int, request: Request):
    current_user, redirect = await _main()._require_super_admin_page(request)
    if redirect:
        return redirect
    company = await _get_company_or_404(company_id)
    from app.repositories import tray_ticket_questions as tq_repo

    form = await request.form()
    field_type = str(form.get("field_type") or "text").strip()
    label = str(form.get("label") or "").strip()
    placeholder = str(form.get("placeholder") or "").strip() or None
    is_required = bool(form.get("is_required"))
    is_active = bool(form.get("is_active"))
    sort_order = int(form.get("sort_order") or 0)
    options_text = str(form.get("options_text") or "")
    options = [o.strip() for o in options_text.splitlines() if o.strip()]

    if not label:
        all_questions = await tq_repo.list_questions(active_only=False)
        extra = {
            "title": f"New question — {company.get('name')}",
            "company": company,
            "question": None,
            "all_questions": all_questions,
            "error_message": "Label is required.",
        }
        return await _main()._render_template(
            "admin/tray/company_ticket_question_form.html",
            request,
            current_user,
            extra=extra,
        )

    record = await tq_repo.create_question(
        scope="company",
        company_id=int(company_id),
        field_type=field_type,
        label=label,
        placeholder=placeholder,
        is_required=is_required,
        options=options,
        sort_order=sort_order,
        is_active=is_active,
        created_by_user_id=int(current_user["id"]),
    )

    parent_ids = form.getlist("cond_parent_id[]")
    operators = form.getlist("cond_operator[]")
    expected_vals = form.getlist("cond_expected[]")
    conditions = [
        {"parent_question_id": int(p), "operator": o, "expected_value": e}
        for p, o, e in zip(parent_ids, operators, expected_vals)
        if p
    ]
    if conditions:
        await tq_repo.replace_conditions_for_question(int(record["id"]), conditions)

    cid = int(company_id)
    return RedirectResponse(
        url=f"/admin/companies/{cid}/tray?"
        + urlencode({"success": "Question created."}),
        status_code=303,
    )


async def admin_company_ticket_question_edit_page(
    company_id: int, question_id: int, request: Request
):
    current_user, redirect = await _main()._require_super_admin_page(request)
    if redirect:
        return redirect
    company = await _get_company_or_404(company_id)
    from app.repositories import tray_ticket_questions as tq_repo

    question = await tq_repo.get_question(question_id)
    if not question or question.get("company_id") != company_id:
        return RedirectResponse(
            url=f"/admin/companies/{company_id}/tray", status_code=303
        )
    question["conditions"] = await tq_repo.list_conditions_for_question(question_id)
    all_questions = await tq_repo.list_questions(active_only=False)
    extra = {
        "title": f"Edit question — {company.get('name')}",
        "company": company,
        "question": question,
        "all_questions": all_questions,
        "error_message": None,
    }
    return await _main()._render_template(
        "admin/tray/company_ticket_question_form.html",
        request,
        current_user,
        extra=extra,
    )


async def admin_company_ticket_question_update(
    company_id: int, question_id: int, request: Request
):
    current_user, redirect = await _main()._require_super_admin_page(request)
    if redirect:
        return redirect
    company = await _get_company_or_404(company_id)
    from app.repositories import tray_ticket_questions as tq_repo

    question = await tq_repo.get_question(question_id)
    if not question or question.get("company_id") != company_id:
        return RedirectResponse(
            url=f"/admin/companies/{company_id}/tray", status_code=303
        )

    form = await request.form()
    field_type = str(form.get("field_type") or "text").strip()
    label = str(form.get("label") or "").strip()
    placeholder = str(form.get("placeholder") or "").strip() or None
    is_required = bool(form.get("is_required"))
    is_active = bool(form.get("is_active"))
    sort_order = int(form.get("sort_order") or 0)
    options_text = str(form.get("options_text") or "")
    options = [o.strip() for o in options_text.splitlines() if o.strip()]

    if not label:
        question["conditions"] = await tq_repo.list_conditions_for_question(question_id)
        all_questions = await tq_repo.list_questions(active_only=False)
        extra = {
            "title": f"Edit question — {company.get('name')}",
            "company": company,
            "question": question,
            "all_questions": all_questions,
            "error_message": "Label is required.",
        }
        return await _main()._render_template(
            "admin/tray/company_ticket_question_form.html",
            request,
            current_user,
            extra=extra,
        )

    await tq_repo.update_question(
        question_id,
        field_type=field_type,
        label=label,
        placeholder=placeholder,
        is_required=is_required,
        options=options,
        sort_order=sort_order,
        is_active=is_active,
    )

    parent_ids = form.getlist("cond_parent_id[]")
    operators = form.getlist("cond_operator[]")
    expected_vals = form.getlist("cond_expected[]")
    conditions = [
        {"parent_question_id": int(p), "operator": o, "expected_value": e}
        for p, o, e in zip(parent_ids, operators, expected_vals)
        if p
    ]
    await tq_repo.replace_conditions_for_question(question_id, conditions)

    cid = int(company_id)
    return RedirectResponse(
        url=f"/admin/companies/{cid}/tray?"
        + urlencode({"success": "Question updated."}),
        status_code=303,
    )


async def admin_company_ticket_question_delete(
    company_id: int, question_id: int, request: Request
):
    current_user, redirect = await _main()._require_super_admin_page(request)
    if redirect:
        return redirect
    from app.repositories import tray_ticket_questions as tq_repo

    question = await tq_repo.get_question(question_id)
    if question and question.get("company_id") == company_id:
        await tq_repo.delete_question(question_id)

    cid = int(company_id)
    return RedirectResponse(
        url=f"/admin/companies/{cid}/tray?"
        + urlencode({"success": "Question deleted."}),
        status_code=303,
    )
