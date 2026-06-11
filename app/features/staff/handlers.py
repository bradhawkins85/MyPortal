"""Staff handler implementations for the ``staff`` feature pack."""

from __future__ import annotations

import json
import secrets
from datetime import datetime, timedelta, timezone
from html import escape
from typing import Any, cast

from fastapi import HTTPException, Request, status
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import ValidationError

from app import main as main_module

from .helpers import (
    _filter_staff_for_offboarding_choices,
    _load_staff_context,
    _normalize_staff_access_scope,
    _staff_member_is_offboarding_mail_choice,
    _staff_member_matches_company_email_domains,
)


CompanyWorkflowPolicyUpsertSchema = main_module.CompanyWorkflowPolicyUpsertSchema
WorkflowConfigSchema = main_module.WorkflowConfigSchema
_parse_input_datetime = main_module._parse_input_datetime
_parse_local_datetime_to_utc = main_module._parse_local_datetime_to_utc
_raw_value_includes_time = main_module._raw_value_includes_time
_render_template = main_module._render_template
_request_prefers_json = main_module._request_prefers_json
_serialise_for_json = main_module._serialise_for_json
audit_repo = main_module.audit_repo
audit_service = main_module.audit_service
auth_repo = main_module.auth_repo
company_repo = main_module.company_repo
email_service = main_module.email_service
log_error = main_module.log_error
log_info = main_module.log_info
m365_service = main_module.m365_service
modules_service = main_module.modules_service
settings = main_module.settings
staff_access_service = main_module.staff_access_service
staff_custom_fields_repo = main_module.staff_custom_fields_repo
staff_field_config_service = main_module.staff_field_config_service
staff_onboarding_workflow_service = main_module.staff_onboarding_workflow_service
staff_repo = main_module.staff_repo
staff_requests_repo = main_module.staff_requests_repo
staff_workflow_repo = main_module.staff_workflow_repo
user_company_repo = main_module.user_company_repo
user_repo = main_module.user_repo


async def _get_current_user_staff_department(user: dict[str, Any], company_id: int) -> str | None:
    user_email = str(user.get("email") or "").strip().lower()
    if not user_email:
        return None
    staff_rows = await staff_repo.list_staff_by_email(user_email)
    for staff_record in staff_rows:
        try:
            staff_company_id = int(staff_record.get("company_id") or 0)
        except (TypeError, ValueError):
            continue
        if staff_company_id != company_id:
            continue
        department = str(staff_record.get("department") or "").strip()
        return department or None
    return None


def _departments_match(left: str | None, right: str | None) -> bool:
    return bool(left and right and left.strip().lower() == right.strip().lower())


async def staff_page(
    request: Request,
    enabled: str = "",
    department: str = "",
    show_ex_staff: str = "",
):
    (
        user,
        membership,
        company,
        staff_permission,
        company_id,
        redirect,
    ) = await _load_staff_context(request)
    if redirect:
        return redirect

    is_super_admin = bool(user.get("is_super_admin"))
    is_admin = is_super_admin or bool(membership and membership.get("is_admin"))
    is_helpdesk_technician = await main_module._is_helpdesk_technician(user, request)
    membership_data = membership or {}
    raw_staff_menu_access = main_module.normalize_menu_permissions(
        membership_data.get("menu_permissions")
    ).get("menu.staff", "none")
    has_write_staff_menu_access = raw_staff_menu_access == "write"
    staff_access_scope = _normalize_staff_access_scope(membership_data.get("staff_permission", staff_permission))
    can_edit_staff = bool(is_super_admin or (staff_access_scope > 0 and has_write_staff_menu_access))
    can_approve_onboarding = bool(is_super_admin or is_helpdesk_technician)

    enabled_value = enabled.strip()
    enabled_filter: bool | None
    if enabled_value == "1":
        enabled_filter = True
    elif enabled_value == "0":
        enabled_filter = False
    else:
        enabled_filter = None

    department_filter = department.strip()
    show_ex_staff_flag = show_ex_staff.strip() == "1"

    staff_members: list[dict[str, Any]] = []
    staff_pending_requests: list[dict[str, Any]] = []
    departments: list[str] = []
    field_config: list[dict[str, Any]] = []
    custom_field_definitions: list[dict[str, Any]] = []
    active_staff_for_offboarding: list[dict[str, Any]] = []
    offboarding_email_forwarding_enabled: bool = True
    has_m365: bool = False
    if company_id is not None:
        field_config = await staff_field_config_service.load_effective_company_staff_fields(
            company_id
        )
        custom_field_definitions = await staff_custom_fields_repo.list_field_definitions(company_id)
        staff_members = await staff_repo.list_staff(
            company_id, enabled=enabled_filter, exclude_ex_staff=not show_ex_staff_flag,
            exclude_package_staff=not show_ex_staff_flag
        )
        company_record = await company_repo.get_company_by_id(company_id)
        active_staff_for_offboarding = _filter_staff_for_offboarding_choices(
            await staff_repo.list_active_staff_for_offboarding(company_id),
            list((company_record or {}).get("email_domains") or []),
        )
        offboarding_email_forwarding_enabled = bool(
            int((company_record or {}).get("offboarding_email_forwarding_enabled", 1) or 1)
        )
        m365_creds = await m365_service.get_credentials(company_id)
        has_m365 = bool(m365_creds)
        if can_approve_onboarding:
            staff_pending_requests = await staff_requests_repo.list_requests(
                company_id, status="pending"
            )
        workflow_map = await staff_workflow_repo.list_executions_for_staff_ids(
            [int(member["id"]) for member in staff_members if member.get("id") is not None]
        )
        execution_ids = [
            int(execution["id"])
            for execution in workflow_map.values()
            if execution and execution.get("id") is not None
        ]
        workflow_step_logs = await staff_workflow_repo.list_step_logs_for_execution_ids(execution_ids)
        external_checkpoint_logs = await staff_workflow_repo.list_external_checkpoints_for_execution_ids(execution_ids)
        for member in staff_members:
            execution = workflow_map.get(int(member["id"])) if member.get("id") is not None else None
            member["workflow_status"] = _serialise_for_json(execution)
            member["workflow_next_run_at"] = _serialise_for_json((execution or {}).get("scheduled_for_utc"))
            member["workflow_requested_timezone"] = (execution or {}).get("requested_timezone")
            member["workflow_is_overdue"] = False
            raw_next_run = (execution or {}).get("scheduled_for_utc")
            next_run_at: datetime | None = None
            if isinstance(raw_next_run, datetime):
                next_run_at = (
                    raw_next_run.astimezone(timezone.utc).replace(tzinfo=None)
                    if raw_next_run.tzinfo is not None
                    else raw_next_run
                )
            elif isinstance(raw_next_run, str) and raw_next_run.strip():
                try:
                    parsed_next_run = datetime.fromisoformat(raw_next_run.strip().replace("Z", "+00:00"))
                except ValueError:
                    parsed_next_run = None
                if parsed_next_run is not None:
                    next_run_at = (
                        parsed_next_run.astimezone(timezone.utc).replace(tzinfo=None)
                        if parsed_next_run.tzinfo is not None
                        else parsed_next_run
                    )
            workflow_state = str((execution or {}).get("state") or member.get("onboarding_status") or "requested").strip().lower()
            if next_run_at is not None and workflow_state in {"approved", "offboarding_approved"}:
                member["workflow_is_overdue"] = next_run_at <= datetime.now(timezone.utc).replace(tzinfo=None)
            staff_id = member.get("id")
            audit_logs: list[dict[str, Any]] = []
            if staff_id is not None:
                audit_logs = await audit_repo.list_audit_logs(
                    entity_type="staff",
                    entity_id=int(staff_id),
                    limit=50,
                )
            audit_logs = sorted(audit_logs, key=lambda entry: entry.get("created_at") or datetime.min)
            timeline: list[dict[str, Any]] = []
            approver_user_ids: list[int] = []
            for entry in audit_logs:
                action = str(entry.get("action") or "").strip()
                metadata = entry.get("metadata") if isinstance(entry.get("metadata"), dict) else {}
                if action == "staff.onboarding.requested":
                    raw_ids = metadata.get("approver_user_ids")
                    if isinstance(raw_ids, list):
                        approver_user_ids = [
                            int(raw_id)
                            for raw_id in raw_ids
                            if isinstance(raw_id, int) or (isinstance(raw_id, str) and raw_id.isdigit())
                        ]
                timeline.append(
                    {
                        "timestamp": entry.get("created_at"),
                        "event_type": "audit",
                        "event": action,
                        "actor": entry.get("user_email") or "System",
                        "details": metadata,
                    }
                )

            approver_emails: list[str] = []
            for approver_id in approver_user_ids:
                approver_user = await user_repo.get_user_by_id(int(approver_id))
                if approver_user and approver_user.get("email"):
                    approver_emails.append(str(approver_user["email"]))
            if execution and execution.get("id") is not None:
                execution_id = int(execution["id"])
                for step in workflow_step_logs.get(execution_id, []):
                    timeline.append(
                        {
                            "timestamp": step.get("started_at") or step.get("completed_at"),
                            "event_type": "workflow_step",
                            "event": f"{step.get('step_name')} ({step.get('status')})",
                            "actor": "Workflow engine",
                            "details": {
                                "attempt": step.get("attempt"),
                                "error_message": step.get("error_message"),
                            },
                        }
                    )
                for checkpoint in external_checkpoint_logs.get(execution_id, []):
                    timeline.append(
                        {
                            "timestamp": checkpoint.get("updated_at") or checkpoint.get("created_at"),
                            "event_type": "external_callback",
                            "event": f"external checkpoint {checkpoint.get('status')}",
                            "actor": f"API key #{checkpoint.get('confirmed_by_api_key_id')}" if checkpoint.get("confirmed_by_api_key_id") else "External system",
                            "details": {
                                "source": checkpoint.get("source"),
                                "proof_reference_id": checkpoint.get("proof_reference_id"),
                                "payload_hash": checkpoint.get("payload_hash"),
                            },
                        }
                    )
            timeline = sorted(timeline, key=lambda entry: entry.get("timestamp") or datetime.min)
            timeline = cast(list[dict[str, Any]], _serialise_for_json(timeline))
            member["onboarding_timeline"] = timeline
            if workflow_state == "waiting_external":
                current_step = str((execution or {}).get("current_step") or "await_external_confirmation")
                member["onboarding_pending_details"] = (
                    f"Waiting for external completion: callback pending for step "
                    f"{current_step.replace('_', ' ')}."
                )
            elif workflow_state == "awaiting_approval":
                if approver_emails:
                    member["onboarding_pending_details"] = (
                        "Waiting for approval from: " + ", ".join(approver_emails)
                    )
                else:
                    member["onboarding_pending_details"] = "Waiting for approval from authorized approvers."
            else:
                member["onboarding_pending_details"] = None
        company_email_domains = list((company or {}).get("email_domains") or [])
        staff_members = [
            member
            for member in staff_members
            if _staff_member_matches_company_email_domains(member, company_email_domains)
        ]
        if not is_super_admin and staff_permission == 1:
            user_email = (user.get("email") or "").lower()
            current_staff = next(
                (
                    member
                    for member in staff_members
                    if (member.get("email") or "").lower() == user_email
                ),
                None,
            )
            user_department = (current_staff or {}).get("department")
            if user_department:
                staff_members = [
                    member
                    for member in staff_members
                    if member.get("department")
                    and member["department"].lower() == user_department.lower()
                ]
            else:
                staff_members = []
        else:
            if department_filter:
                staff_members = [
                    member
                    for member in staff_members
                    if (member.get("department") or "") == department_filter
                ]
            departments = sorted(
                {
                    str(member.get("department"))
                    for member in staff_members
                    if member.get("department")
                }
            )

    extra = {
        "title": "Staff",
        "is_super_admin": is_super_admin,
        "is_admin": is_admin,
        "is_helpdesk_technician": is_helpdesk_technician,
        "staff_permission": staff_permission,
        "can_edit_staff": can_edit_staff,
        "can_approve_onboarding": can_approve_onboarding,
        "departments": departments,
        "staff_members": cast(list[dict[str, Any]], _serialise_for_json(staff_members)),
        "staff_pending_requests": cast(list[dict[str, Any]], _serialise_for_json(staff_pending_requests)),
        "enabled_filter": enabled_value,
        "department_filter": department_filter,
        "show_ex_staff": show_ex_staff_flag,
        "staff_field_config": field_config,
        "staff_custom_field_definitions": cast(list[dict[str, Any]], _serialise_for_json(custom_field_definitions)),
        "active_staff_for_offboarding": cast(list[dict[str, Any]], _serialise_for_json(active_staff_for_offboarding)),
        "offboarding_email_forwarding_enabled": offboarding_email_forwarding_enabled,
        "has_m365": has_m365,
    }
    return await _render_template("staff/index.html", request, user, extra=extra)


_WORKFLOW_POLICY_FORM_SCHEMA: dict[str, Any] = {
    "fields": [
        {
            "name": "workflow_key",
            "label": "Workflow key/name",
            "type": "text",
            "required": True,
            "description": "Unique workflow identifier. Letters, numbers, _, -, and . are normalized.",
        },
        {
            "name": "enabled",
            "label": "Enabled",
            "type": "boolean",
            "required": True,
            "default": True,
        },
        {
            "name": "max_retries",
            "label": "Max retries",
            "type": "number",
            "required": True,
            "min": 0,
            "max": 20,
            "default": 2,
        },
        {
            "name": "config_json",
            "label": "Config JSON",
            "type": "json",
            "required": False,
            "description": (
                "Workflow config object with `steps` and `failure_policy`, "
                "or send `steps` + `failure_policy` as structured fields."
            ),
        },
    ],
}

_OFFBOARDING_STEP_CATALOG: list[dict[str, Any]] = [
    {
        "type": "offboard_account",
        "name": "Disable sign-in & remove access",
        "description": (
            "Disables sign-in and optionally removes licenses/group membership. "
            "Can also optionally cancel future Exchange calendar events, set out-of-office reply, and configure email forwarding using "
            "${vars.offboarding.*} variables from the request."
        ),
    },
    {
        "type": "m365_rename_upn_display_name",
        "name": "Rename UPN/display name",
        "description": "Renames the account UPN and display name for offboarded identity hygiene.",
    },
    {
        "type": "m365_update_org_fields",
        "name": "Update department/company",
        "description": "Sets Entra ID department/companyName fields to offboarding values.",
    },
    {
        "type": "m365_hide_from_gal",
        "name": "Hide from GAL",
        "description": "Updates showInAddressList (or configured property path) to hide mailbox from GAL.",
    },
    {
        "type": "m365_identity_hygiene",
        "name": "Identity hygiene",
        "description": "Applies reversible profile cleanup and can revoke sign-in sessions.",
    },
    {
        "type": "m365_remove_teams_group_member",
        "name": "Remove from Teams groups",
        "description": "Removes the staff member from one or more Teams/M365 groups using object IDs.",
    },
    {
        "type": "m365_remove_sharepoint_site_member",
        "name": "Remove from SharePoint sites",
        "description": "Removes the staff member from one or more SharePoint sites using Graph site IDs.",
    },
    {
        "type": "send_welcome_email",
        "name": "Send welcome email",
        "description": "Sends a templated email to configured destination(s). Supports ${vars.*} values from system and earlier steps.",
    },
    {
        "type": "send_custom_email",
        "name": "Send custom email",
        "description": "Sends a custom email to configured destination(s) with custom subject/body and ${vars.*} interpolation.",
    },
    {
        "type": "email_requestor",
        "name": "Email requestor",
        "description": "Sends workflow details to the requesting user using workflow/system variables.",
    },
    {
        "type": "http_get",
        "name": "HTTP GET request",
        "description": "Calls an external endpoint with optional headers/query parameters and stores response variables.",
    },
    {
        "type": "http_post",
        "name": "HTTP POST request",
        "description": "Posts JSON data to an external endpoint with optional headers/query parameters and stores response variables.",
    },
    {
        "type": "curl_text",
        "name": "CURL text fetch",
        "description": "Fetches data from an external web source using CURL and stores response as plain text only.",
    },
    {
        "type": "generate_password",
        "name": "Generate password",
        "description": "Generates a cryptographically random password with configurable complexity and stores it as a workflow variable.",
    },
    {
        "type": "generate_kid_friendly_password",
        "name": "Generate kid-friendly password",
        "description": "Generates a word-based password with capitalisation, number suffix and symbol substitutions, safe for children.",
    },
    {
        "type": "push_to_password_pusher",
        "name": "Push to Password Pusher",
        "description": "Pushes a secret (e.g. a generated password) to Password Pusher and stores the resulting share URL as a workflow variable.",
    },
    {
        "type": "hudu_create_contact",
        "name": "Create Hudu contact",
        "description": "Creates a person/contact record in Hudu under the company linked to this workflow, for documentation purposes.",
    },
    {
        "type": "hudu_push_password",
        "name": "Push password to Hudu",
        "description": "Creates an asset password entry in Hudu under the company linked to this workflow for secure credential storage.",
    },
    {
        "type": "delete_staff_record",
        "name": "Delete staff record",
        "description": (
            "Deletes the staff record that triggered this workflow. "
            "Use this to remove duplicate entries, for example when the original onboarding request "
            "should be superseded by a record synced from Office 365."
        ),
    },
    {
        "type": "disable_myportal_account",
        "name": "Disable MyPortal account",
        "description": (
            "Disables the MyPortal portal account linked to this staff member's email address, "
            "preventing them from signing in. Has no effect if the staff member has no portal account."
        ),
    },
]

_ONBOARDING_STEP_CATALOG: list[dict[str, Any]] = [
    {
        "type": "create_user",
        "name": "Create user account",
        "description": "Creates the staff user account in Microsoft 365 / Entra ID.",
    },
    {
        "type": "assign_licenses",
        "name": "Assign licenses",
        "description": "Assigns one or more M365 licenses to the new starter.",
    },
    {
        "type": "add_to_groups",
        "name": "Add to groups",
        "description": "Adds the new staff member to required security and distribution groups.",
    },
    {
        "type": "m365_add_teams_group_member",
        "name": "Add to Teams groups",
        "description": "Adds the new staff member to one or more Teams/M365 groups using object IDs.",
    },
    {
        "type": "m365_add_sharepoint_site_member",
        "name": "Add to SharePoint sites",
        "description": "Adds the new staff member to one or more SharePoint sites using Graph site IDs.",
    },
    {
        "type": "set_manager",
        "name": "Set manager",
        "description": "Sets the direct manager relationship for org chart and approvals.",
    },
    {
        "type": "send_welcome_email",
        "name": "Send welcome email",
        "description": "Sends a templated welcome email to the staff member. Supports ${vars.*} values from system and earlier steps.",
    },
    {
        "type": "send_custom_email",
        "name": "Send custom email",
        "description": "Sends a custom email to configured destination(s) with custom subject/body and ${vars.*} interpolation.",
    },
    {
        "type": "email_requestor",
        "name": "Email requestor",
        "description": "Sends onboarding details (for example login/password) to the requesting user using workflow/system variables.",
    },
    {
        "type": "http_get",
        "name": "HTTP GET request",
        "description": "Calls an external endpoint with optional headers/query parameters and stores response variables.",
    },
    {
        "type": "http_post",
        "name": "HTTP POST request",
        "description": "Posts JSON data to an external endpoint with optional headers/query parameters and stores response variables.",
    },
    {
        "type": "curl_text",
        "name": "CURL text fetch",
        "description": "Fetches data from an external web source using CURL and stores response as plain text only.",
    },
    {
        "type": "generate_password",
        "name": "Generate password",
        "description": "Generates a cryptographically random password with configurable complexity and stores it as a workflow variable.",
    },
    {
        "type": "generate_kid_friendly_password",
        "name": "Generate kid-friendly password",
        "description": "Generates a word-based password with capitalisation, number suffix and symbol substitutions, safe for children.",
    },
    {
        "type": "push_to_password_pusher",
        "name": "Push to Password Pusher",
        "description": "Pushes a secret (e.g. a generated password) to Password Pusher and stores the resulting share URL as a workflow variable.",
    },
    {
        "type": "hudu_create_contact",
        "name": "Create Hudu contact",
        "description": "Creates a person/contact record in Hudu under the company linked to this workflow, for documentation purposes.",
    },
    {
        "type": "hudu_push_password",
        "name": "Push password to Hudu",
        "description": "Creates an asset password entry in Hudu under the company linked to this workflow for secure credential storage.",
    },
    {
        "type": "delete_staff_record",
        "name": "Delete staff record",
        "description": (
            "Deletes the staff record that triggered this workflow. "
            "Use this to remove duplicate entries, for example when the original onboarding request "
            "should be superseded by a record synced from Office 365."
        ),
    },
]

_WORKFLOW_STEP_FORM_SCHEMA: dict[str, dict[str, Any]] = {
    "*": {
        "fields": [
            {
                "name": "conditions.department_in",
                "label": "Only run for departments",
                "type": "text",
                "default": "",
                "description": "Optional comma-separated department names. Step is skipped when the staff department does not match.",
                "example": "Finance, Operations",
            },
            {
                "name": "conditions.custom_fields_truthy",
                "label": "Required tick-box fields",
                "type": "text",
                "default": "",
                "description": "Optional comma-separated custom field names that must be checked/true before this step runs.",
                "example": "needs_laptop, needs_phone",
            },
        ],
        "retry_policy": {"max_retries": 0},
        "failure_policy": {"mode": "fail_fast", "create_ticket_on_failure": True},
    },
    "create_user": {
        "fields": [
            {
                "name": "user_principal_name",
                "label": "User Principal Name (email)",
                "type": "text",
                "default": "${vars.staff.email}",
                "description": (
                    "The login email / UPN for the new account. "
                    "You can build it from variables and fixed text, e.g. "
                    "${vars.staff.first_name}.${vars.staff.last_name}@company.com. "
                    "Falls back to the staff member's email address if left blank."
                ),
                "example": "${vars.staff.first_name}.${vars.staff.last_name}@company.com",
            },
            {
                "name": "display_name",
                "label": "Display name",
                "type": "text",
                "default": "${vars.staff.full_name}",
                "description": "Full name shown in Microsoft 365 and address lists.",
                "example": "${vars.staff.first_name} ${vars.staff.last_name}",
            },
            {
                "name": "given_name",
                "label": "First name",
                "type": "text",
                "default": "${vars.staff.first_name}",
            },
            {
                "name": "surname",
                "label": "Last name",
                "type": "text",
                "default": "${vars.staff.last_name}",
            },
            {
                "name": "job_title",
                "label": "Job title",
                "type": "text",
                "default": "${vars.staff.job_title}",
            },
            {
                "name": "department",
                "label": "Department",
                "type": "text",
                "default": "${vars.staff.department}",
            },
            {
                "name": "company_name",
                "label": "Company",
                "type": "text",
                "default": "${vars.staff.company_name}",
            },
            {
                "name": "office_location",
                "label": "Office location",
                "type": "text",
                "default": "${vars.staff.office_location}",
            },
            {
                "name": "usage_location",
                "label": "Usage location",
                "type": "text",
                "default": "US",
                "description": "Two-letter country code used for licensing.",
                "example": "US",
            },
            {
                "name": "mail_nickname",
                "label": "Mail nickname",
                "type": "text",
                "default": "${vars.staff.email_local_part}",
                "description": "Alias/local-part used for mailbox nickname.",
            },
            {
                "name": "password",
                "label": "Initial password",
                "type": "text",
                "default": "${vars.password.strong}",
                "description": "Use variables where possible; avoid static passwords.",
            },
            {
                "name": "force_change_password_next_signin",
                "label": "Require password change at first sign-in",
                "type": "checkbox",
                "default": True,
            },
            {
                "name": "account_enabled",
                "label": "Enable account immediately",
                "type": "checkbox",
                "default": True,
            },
        ],
    },
    "assign_licenses": {
        "fields": [
            {
                "name": "licenses_csv",
                "label": "License SKUs (comma separated)",
                "type": "text",
                "default": "",
                "description": "Comma-separated SKU part numbers to assign.",
                "example": "ENTERPRISEPACK,BUSINESS_PREMIUM",
            },
            {
                "name": "remove_existing_licenses",
                "label": "Remove existing licenses first",
                "type": "checkbox",
                "default": False,
            },
        ],
    },
    "add_to_groups": {
        "fields": [
            {
                "name": "group_ids_csv",
                "label": "Group IDs (comma separated)",
                "type": "text",
                "default": "",
                "description": "Microsoft 365 group object IDs.",
            },
            {
                "name": "group_names_csv",
                "label": "Group names (comma separated)",
                "type": "text",
                "default": "",
                "description": "Optional display names for readable configuration.",
            },
        ],
    },
    "set_manager": {
        "fields": [
            {
                "name": "manager_email",
                "label": "Manager email",
                "type": "text",
                "default": "",
                "description": "Primary manager identifier for Microsoft Graph lookup.",
                "example": "manager@company.com",
            },
            {
                "name": "manager_id",
                "label": "Manager object ID (optional)",
                "type": "text",
                "default": "",
            },
        ],
    },
    "send_welcome_email": {
        "fields": [
            {
                "name": "to",
                "label": "To address",
                "type": "text",
                "default": "${vars.staff.email}",
            },
            {
                "name": "cc",
                "label": "CC addresses",
                "type": "text",
                "default": "",
                "description": "Comma-separated email addresses.",
            },
            {
                "name": "bcc",
                "label": "BCC addresses",
                "type": "text",
                "default": "",
                "description": "Comma-separated email addresses.",
            },
            {
                "name": "subject",
                "label": "Email subject",
                "type": "text",
                "default": "Welcome to ${vars.staff.company_name}, ${vars.staff.first_name}",
            },
            {
                "name": "body",
                "label": "Email body",
                "type": "textarea",
                "default": "Hi ${vars.staff.first_name}, welcome to ${vars.staff.company_name}.",
            },
            {
                "name": "is_html",
                "label": "Body is HTML",
                "type": "checkbox",
                "default": False,
            },
        ],
    },
    "send_custom_email": {
        "fields": [
            {"name": "to", "label": "To addresses", "type": "text", "default": ""},
            {"name": "cc", "label": "CC addresses", "type": "text", "default": ""},
            {"name": "bcc", "label": "BCC addresses", "type": "text", "default": ""},
            {"name": "subject", "label": "Email subject", "type": "text", "default": ""},
            {"name": "body", "label": "Email body", "type": "textarea", "default": ""},
            {"name": "is_html", "label": "Body is HTML", "type": "checkbox", "default": False},
            {
                "name": "reply_to",
                "label": "Reply-to address",
                "type": "text",
                "default": "",
            },
        ],
    },
    "email_requestor": {
        "fields": [
            {
                "name": "to",
                "label": "To address",
                "type": "text",
                "default": "${vars.requestor.email}",
                "description": "Defaults to the user who requested onboarding.",
            },
            {
                "name": "subject",
                "label": "Email subject",
                "type": "text",
                "default": "Onboarding update for ${vars.staff.full_name}",
            },
            {
                "name": "body",
                "label": "Email body",
                "type": "textarea",
                "default": "Account provisioning complete for ${vars.staff.full_name}.",
            },
            {"name": "is_html", "label": "Body is HTML", "type": "checkbox", "default": False},
        ],
    },
    "offboard_account": {
        "fields": [
            {"name": "disable_sign_in", "label": "Disable sign-in", "type": "checkbox", "default": True},
            {"name": "revoke_licenses", "label": "Remove assigned licenses", "type": "checkbox", "default": False},
            {
                "name": "remove_from_groups",
                "label": "Remove from groups",
                "type": "checkbox",
                "default": False,
            },
            {
                "name": "disable_mailbox_rules",
                "label": "Disable all mailbox rules",
                "type": "checkbox",
                "default": False,
                "description": "Disables every inbox message rule on the offboarded mailbox.",
            },
            {
                "name": "remove_calendar_events",
                "label": "Cancel future calendar events",
                "type": "checkbox",
                "default": False,
                "description": (
                    "Runs Exchange Online Remove-CalendarEvents with -CancelOrganizedMeetings "
                    "for the offboarded user."
                ),
            },
            {
                "name": "forwarding_address",
                "label": "Forward mail to",
                "type": "text",
                "default": "",
                "description": (
                    "Email address to forward mail to. Supports ${vars.offboarding.email_forward_to} "
                    "to use the address selected on the offboarding request."
                ),
                "example": "${vars.offboarding.email_forward_to}",
            },
            {
                "name": "out_of_office_message",
                "label": "Out of office message",
                "type": "textarea",
                "default": "",
                "description": (
                    "Sets an automatic reply message on the mailbox. Supports "
                    "${vars.offboarding.out_of_office_message} to use the message entered on the request."
                ),
            },
            {
                "name": "mailbox_grant_emails",
                "label": "Grant mailbox access to (comma-separated emails)",
                "type": "text",
                "default": "",
                "description": (
                    "Comma-separated list of email addresses to record as mailbox access requests. "
                    "Supports ${vars.offboarding.mailbox_grant_emails} to use the list from the request. "
                    "Note: actual Exchange Online delegation must be actioned separately."
                ),
                "example": "${vars.offboarding.mailbox_grant_emails}",
            },
        ],
    },
    "m365_rename_upn_display_name": {
        "fields": [
            {
                "name": "upn_prefix",
                "label": "UPN prefix",
                "type": "text",
                "default": "former",
                "description": "Prefix added before the current UPN local-part.",
                "example": "former",
            },
            {
                "name": "display_name",
                "label": "Display name template",
                "type": "text",
                "default": "${vars.staff_full_name} (Former Staff)",
                "description": (
                    "Supports workflow variables, for example ${vars.staff.full_name}, "
                    "${vars.staff.email}, ${vars.now.date}, or ${vars.now.local.display}."
                ),
                "example": "${vars.staff.full_name} (Former Staff - ${vars.now.local.display})",
            },
        ],
    },
    "m365_update_org_fields": {
        "fields": [
            {"name": "department", "label": "Department value", "type": "text", "default": "Former Staff"},
            {"name": "company_name", "label": "Company value", "type": "text", "default": "Former Staff"},
        ],
    },
    "m365_hide_from_gal": {
        "fields": [
            {"name": "hidden", "label": "Hide from GAL", "type": "checkbox", "default": True},
            {
                "name": "property_path",
                "label": "Graph property path",
                "type": "text",
                "default": "showInAddressList",
            },
        ],
    },
    "m365_identity_hygiene": {
        "fields": [
            {"name": "revoke_sign_in_sessions", "label": "Revoke sign-in sessions", "type": "checkbox", "default": True},
        ],
    },
    "m365_add_teams_group_member": {
        "fields": [
            {
                "name": "group_ids_csv",
                "label": "Teams group IDs (comma separated)",
                "type": "text",
                "default": "",
                "description": "Microsoft 365 group object IDs backing Teams teams.",
                "example": "11111111-1111-1111-1111-111111111111,22222222-2222-2222-2222-222222222222",
            },
            {
                "name": "user_id",
                "label": "User object ID (optional)",
                "type": "text",
                "default": "",
                "description": "Optional override. Defaults to the workflow staff member's M365 user ID.",
            },
        ],
    },
    "m365_remove_teams_group_member": {
        "fields": [
            {
                "name": "group_ids_csv",
                "label": "Teams group IDs (comma separated)",
                "type": "text",
                "default": "",
                "description": "Microsoft 365 group object IDs to remove the user from.",
            },
            {
                "name": "user_id",
                "label": "User object ID (optional)",
                "type": "text",
                "default": "",
            },
        ],
    },
    "m365_add_sharepoint_site_member": {
        "fields": [
            {
                "name": "site_ids_csv",
                "label": "SharePoint site IDs (comma separated)",
                "type": "text",
                "default": "",
                "description": "Graph site IDs to grant access to.",
                "example": "contoso.sharepoint.com,12345678-aaaa-bbbb-cccc-1234567890ab,11111111-2222-3333-4444-555555555555",
            },
            {
                "name": "site_role",
                "label": "SharePoint role",
                "type": "select",
                "default": "write",
                "options": [
                    {"value": "write", "label": "Edit (write)"},
                    {"value": "read", "label": "Read"},
                ],
            },
            {
                "name": "user_id",
                "label": "User object ID (optional)",
                "type": "text",
                "default": "",
            },
        ],
    },
    "m365_remove_sharepoint_site_member": {
        "fields": [
            {
                "name": "site_ids_csv",
                "label": "SharePoint site IDs (comma separated)",
                "type": "text",
                "default": "",
                "description": "Graph site IDs to remove access from.",
            },
            {
                "name": "user_id",
                "label": "User object ID (optional)",
                "type": "text",
                "default": "",
            },
        ],
    },
    "http_get": {
        "fields": [
            {
                "name": "url",
                "label": "Request URL",
                "type": "text",
                "default": "",
                "description": "Supports workflow variables like ${vars.staff.email}.",
                "example": "https://api.example.com/users/${vars.staff.email}",
            },
            {
                "name": "headers_json",
                "label": "Headers (JSON object)",
                "type": "textarea",
                "default": "{}",
                "description": "JSON object of headers. Values can include ${vars.*}.",
                "example": "{\"Authorization\":\"Bearer ${vars.system.api_token}\"}",
            },
            {
                "name": "query_params_json",
                "label": "Query params (JSON object)",
                "type": "textarea",
                "default": "{}",
                "description": "JSON object of query parameters.",
                "example": "{\"staffId\":\"${vars.staff_id}\",\"status\":\"active\"}",
            },
            {
                "name": "store_json",
                "label": "Workflow variables to store (JSON object)",
                "type": "textarea",
                "default": "{}",
                "description": "Map variable name to response path (e.g. body.id). Values persist only for this workflow run.",
                "example": "{\"external_user_id\":\"body.id\",\"sync_status\":\"body.status\"}",
            },
            {
                "name": "timeout_seconds",
                "label": "Timeout seconds",
                "type": "number",
                "default": 30,
            },
        ],
    },
    "http_post": {
        "fields": [
            {
                "name": "url",
                "label": "Request URL",
                "type": "text",
                "default": "",
                "description": "Supports workflow variables like ${vars.staff.email}.",
                "example": "https://api.example.com/users",
            },
            {
                "name": "headers_json",
                "label": "Headers (JSON object)",
                "type": "textarea",
                "default": "{\"Content-Type\":\"application/json\"}",
                "description": "JSON object of headers. Values can include ${vars.*}.",
            },
            {
                "name": "query_params_json",
                "label": "Query params (JSON object)",
                "type": "textarea",
                "default": "{}",
                "description": "JSON object of query parameters.",
            },
            {
                "name": "json_body",
                "label": "JSON body",
                "type": "textarea",
                "default": "{}",
                "description": "JSON object/array sent as request body. Supports ${vars.*}.",
                "example": "{\"email\":\"${vars.staff.email}\",\"firstName\":\"${vars.staff.first_name}\"}",
            },
            {
                "name": "store_json",
                "label": "Workflow variables to store (JSON object)",
                "type": "textarea",
                "default": "{}",
                "description": "Map variable name to response path (e.g. body.id). Values persist only for this workflow run.",
                "example": "{\"external_user_id\":\"body.id\",\"external_ticket_id\":\"body.ticket.id\"}",
            },
            {
                "name": "timeout_seconds",
                "label": "Timeout seconds",
                "type": "number",
                "default": 30,
            },
        ],
    },
    "curl_text": {
        "fields": [
            {
                "name": "url",
                "label": "Request URL",
                "type": "text",
                "default": "",
                "description": "HTTP/HTTPS URL to fetch via CURL. Supports workflow variables like ${vars.staff.email}.",
                "example": "https://example.com/api/source?email=${vars.staff.email}",
            },
            {
                "name": "store_json",
                "label": "Workflow variables to store (JSON object)",
                "type": "textarea",
                "default": "{\"external_text\":\"body\"}",
                "description": "Map variable name to response path. CURL response is stored as plain text in `body`.",
                "example": "{\"external_text\":\"body\"}",
            },
            {
                "name": "timeout_seconds",
                "label": "Timeout seconds",
                "type": "number",
                "default": 30,
            },
        ],
    },
    "generate_password": {
        "fields": [
            {
                "name": "length",
                "label": "Password length",
                "type": "number",
                "default": 16,
                "description": "Number of characters in the generated password (8–128).",
            },
            {
                "name": "use_upper",
                "label": "Include uppercase letters",
                "type": "checkbox",
                "default": True,
            },
            {
                "name": "use_digits",
                "label": "Include digits",
                "type": "checkbox",
                "default": True,
            },
            {
                "name": "use_symbols",
                "label": "Include symbols",
                "type": "checkbox",
                "default": True,
            },
            {
                "name": "output_var",
                "label": "Store password as variable",
                "type": "text",
                "default": "generated_password",
                "description": "Workflow variable name used to reference this password in later steps, e.g. ${vars.generated_password}.",
            },
        ],
    },
    "generate_kid_friendly_password": {
        "fields": [
            {
                "name": "output_var",
                "label": "Store password as variable",
                "type": "text",
                "default": "generated_password",
                "description": (
                    "Workflow variable name used to reference this password in later steps, "
                    "e.g. ${vars.generated_password}. The password will also be available as "
                    "${vars.generated_password} automatically."
                ),
            },
        ],
    },
    "push_to_password_pusher": {
        "fields": [
            {
                "name": "payload",
                "label": "Secret text",
                "type": "text",
                "default": "${vars.generated_password}",
                "description": "The secret to push (e.g. a generated password). Supports workflow variables.",
                "example": "${vars.generated_password}",
            },
            {
                "name": "expire_after_days",
                "label": "Expire after days",
                "type": "number",
                "default": 7,
                "description": "Number of days before the push expires (leave blank to use the Password Pusher default).",
            },
            {
                "name": "expire_after_views",
                "label": "Expire after views",
                "type": "number",
                "default": 5,
                "description": "Number of views before the push expires (leave blank to use the Password Pusher default).",
            },
            {
                "name": "deletable_by_viewer",
                "label": "Deletable by viewer",
                "type": "checkbox",
                "default": True,
                "description": "Allow the recipient to delete the push before it expires.",
            },
            {
                "name": "retrieval_step",
                "label": "Require retrieval step",
                "type": "checkbox",
                "default": True,
                "description": "Require the recipient to click through an extra confirmation step before viewing the secret.",
            },
            {
                "name": "note",
                "label": "Note",
                "type": "text",
                "default": "",
                "description": "Optional note attached to the push (visible to the recipient).",
            },
            {
                "name": "output_var",
                "label": "Store push URL as variable",
                "type": "text",
                "default": "push_url",
                "description": "Workflow variable name used to reference the share URL in later steps, e.g. ${vars.push_url}.",
            },
        ],
    },
    "hudu_create_contact": {
        "fields": [
            {
                "name": "first_name",
                "label": "First name",
                "type": "text",
                "default": "${vars.staff.first_name}",
                "description": "First name of the person to create in Hudu.",
            },
            {
                "name": "last_name",
                "label": "Last name",
                "type": "text",
                "default": "${vars.staff.last_name}",
                "description": "Last name of the person to create in Hudu.",
            },
            {
                "name": "email",
                "label": "Email",
                "type": "text",
                "default": "${vars.staff.email}",
                "description": "Email address of the person.",
            },
            {
                "name": "job_title",
                "label": "Job title",
                "type": "text",
                "default": "${vars.staff.job_title}",
                "description": "Job title of the person.",
            },
            {
                "name": "phone",
                "label": "Phone",
                "type": "text",
                "default": "${vars.staff.phone}",
                "description": "Phone number of the person.",
            },
            {
                "name": "notes",
                "label": "Notes",
                "type": "text",
                "default": "",
                "description": "Optional notes to attach to the Hudu contact.",
            },
        ],
    },
    "hudu_push_password": {
        "fields": [
            {
                "name": "name",
                "label": "Password name / label",
                "type": "text",
                "default": "${vars.staff.full_name} - Account Password",
                "description": "Display name for this credential in Hudu.",
                "example": "${vars.staff.full_name} - Account Password",
            },
            {
                "name": "password",
                "label": "Password",
                "type": "text",
                "default": "${vars.generated_password}",
                "description": "The password value to store. Supports workflow variables.",
                "example": "${vars.generated_password}",
            },
            {
                "name": "username",
                "label": "Username",
                "type": "text",
                "default": "${vars.staff.email}",
                "description": "Associated username for this credential.",
            },
            {
                "name": "url",
                "label": "URL",
                "type": "text",
                "default": "",
                "description": "Optional URL associated with this credential (e.g. login page).",
            },
            {
                "name": "description",
                "label": "Description",
                "type": "text",
                "default": "",
                "description": "Optional description for this credential entry.",
            },
        ],
    },
}


def _collect_validation_errors(exc: ValidationError) -> list[str]:
    errors: list[str] = []
    for item in exc.errors():
        loc = ".".join(str(part) for part in item.get("loc") or [])
        message = str(item.get("msg") or "Invalid value")
        errors.append(f"{loc}: {message}" if loc else message)
    return errors or ["Invalid workflow policy payload."]


def _parse_json_object(value: Any, *, field_name: str) -> tuple[dict[str, Any] | None, str | None]:
    if value is None or value == "":
        return {}, None
    if isinstance(value, dict):
        return dict(value), None
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return {}, None
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            return None, f"{field_name} must be valid JSON: {exc.msg}."
        if not isinstance(parsed, dict):
            return None, f"{field_name} must decode to an object."
        return parsed, None
    return None, f"{field_name} must be a JSON object."


def _parse_json_list(value: Any, *, field_name: str) -> tuple[list[Any] | None, str | None]:
    if value is None or value == "":
        return None, None
    if isinstance(value, list):
        return list(value), None
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None, None
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            return None, f"{field_name} must be valid JSON: {exc.msg}."
        if not isinstance(parsed, list):
            return None, f"{field_name} must decode to an array."
        return parsed, None
    return None, f"{field_name} must be a JSON array."


def _normalise_workflow_config(raw_config: dict[str, Any] | None) -> dict[str, Any]:
    config: dict[str, Any] = dict(raw_config or {})
    # Backward compatibility: steps saved before WorkflowStepDefinition was introduced may
    # not have a 'key' field.  Derive the key from 'type' so they pass schema validation
    # without requiring a data migration.
    for step_list_key in ("steps", "offboarding_steps"):
        step_list = config.get(step_list_key)
        if isinstance(step_list, list):
            normalised: list[Any] = []
            for step in step_list:
                if isinstance(step, dict) and not step.get("key"):
                    raw_type = str(step.get("type") or "").strip().lower()
                    if raw_type:
                        step = {**step, "key": raw_type}
                normalised.append(step)
            config[step_list_key] = normalised
    try:
        validated = WorkflowConfigSchema.model_validate(config)
    except ValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"message": "Invalid workflow config.", "errors": _collect_validation_errors(exc)},
        ) from exc
    return validated.model_dump(mode="python")


def _normalise_workflow_policy_response(
    policy: dict[str, Any],
    *,
    default_workflow_key: str = staff_workflow_repo.DEFAULT_WORKFLOW_KEY,
) -> dict[str, Any]:
    config = policy.get("config") if isinstance(policy.get("config"), dict) else {}
    return {
        "id": policy.get("id"),
        "company_id": int(policy.get("company_id") or 0),
        "direction": str(policy.get("direction") or staff_workflow_repo.DIRECTION_ONBOARDING),
        "workflow_key": str(policy.get("workflow_key") or default_workflow_key),
        "workflow_name": policy.get("workflow_name"),
        "delay_type": str(policy.get("delay_type") or "scheduled"),
        "sort_order": int(policy.get("sort_order") or 0),
        "enabled": bool(policy.get("is_enabled", True)),
        "max_retries": max(0, int(policy.get("max_retries") or 0)),
        "config_json": _normalise_workflow_config(config),
    }


async def _extract_workflow_policy_payload(request: Request) -> CompanyWorkflowPolicyUpsertSchema:
    if _request_prefers_json(request):
        payload: dict[str, Any] = await request.json()
    else:
        form = await request.form()
        payload = {str(key): form.get(key) for key in form.keys()}
        if "enabled" in payload:
            payload["enabled"] = str(payload.get("enabled")).strip().lower() in {"1", "true", "on", "yes"}
        elif "is_enabled" in payload:
            payload["enabled"] = str(payload.get("is_enabled")).strip().lower() in {"1", "true", "on", "yes"}
        elif "enabled_present" in payload or "is_enabled_present" in payload:
            payload["enabled"] = False

    config_obj, config_error = _parse_json_object(
        payload.get("config") or payload.get("config_json") or payload.get("configJson"),
        field_name="config_json",
    )
    if config_error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"message": "Invalid workflow config.", "errors": [config_error]},
        )

    steps_list, steps_error = _parse_json_list(
        payload.get("steps") or payload.get("step_definitions") or payload.get("stepDefinitions"),
        field_name="steps",
    )
    if steps_error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"message": "Invalid workflow config.", "errors": [steps_error]},
        )
    if steps_list is not None:
        config_obj["steps"] = steps_list

    failure_obj, failure_error = _parse_json_object(
        payload.get("failure_policy") or payload.get("failurePolicy"),
        field_name="failure_policy",
    )
    if failure_error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"message": "Invalid workflow config.", "errors": [failure_error]},
        )
    if failure_obj:
        config_obj["failure_policy"] = failure_obj

    payload["config"] = _normalise_workflow_config(config_obj)
    try:
        return CompanyWorkflowPolicyUpsertSchema.model_validate(payload)
    except ValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"message": "Invalid workflow policy fields.", "errors": _collect_validation_errors(exc)},
        ) from exc


async def staff_onboarding_workflow_page(request: Request):
    (
        user,
        membership,
        company,
        staff_permission,
        _company_id,
        redirect,
    ) = await _load_staff_context(request, require_admin=True, require_all_staff_access=True)
    if redirect:
        return redirect

    is_super_admin = bool(user.get("is_super_admin"))
    is_admin = is_super_admin or bool(membership and membership.get("is_admin"))
    extra = {
        "title": "Staff onboarding workflow",
        "is_super_admin": is_super_admin,
        "is_admin": is_admin,
        "staff_permission": staff_permission,
        "company": company,
    }
    return await _render_template("staff/workflows_onboarding.html", request, user, extra=extra)


async def staff_onboarding_workflow_policy(request: Request):
    (
        _user,
        _membership,
        _company,
        _staff_permission,
        company_id,
        redirect,
    ) = await _load_staff_context(request, require_admin=True, require_all_staff_access=True)
    if redirect:
        return redirect
    if company_id is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No active company")
    policy = await staff_workflow_repo.get_company_workflow_policy(company_id, direction=staff_workflow_repo.DIRECTION_ONBOARDING)
    return JSONResponse(
        {
            "policy": _normalise_workflow_policy_response(policy),
            "form_schema": _WORKFLOW_POLICY_FORM_SCHEMA,
            "step_catalog": _ONBOARDING_STEP_CATALOG,
            "step_form_schema": _WORKFLOW_STEP_FORM_SCHEMA,
        }
    )


async def upsert_staff_onboarding_workflow_policy(request: Request):
    (
        user,
        _membership,
        _company,
        _staff_permission,
        company_id,
        redirect,
    ) = await _load_staff_context(request, require_admin=True, require_all_staff_access=True)
    if redirect:
        return redirect
    if company_id is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No active company")
    policy_input = await _extract_workflow_policy_payload(request)
    updated = await staff_workflow_repo.upsert_company_workflow_policy(
        company_id=company_id,
        workflow_key=policy_input.workflow_key or staff_workflow_repo.DEFAULT_WORKFLOW_KEY,
        is_enabled=bool(policy_input.enabled),
        max_retries=int(policy_input.max_retries),
        config=policy_input.config,
        direction=staff_workflow_repo.DIRECTION_ONBOARDING,
        delay_type=str(policy_input.delay_type or "scheduled"),
        workflow_name=policy_input.workflow_name,
        sort_order=int(policy_input.sort_order or 0),
    )
    await audit_service.log_action(
        user_id=int(user["id"]) if user.get("id") is not None else None,
        action="staff.workflows.onboarding.policy.upserted",
        entity_type="company",
        entity_id=company_id,
        metadata={"policy": _normalise_workflow_policy_response(updated)},
    )
    return JSONResponse({"success": True, "policy": _normalise_workflow_policy_response(updated)})


async def staff_offboarding_workflow_page(request: Request):
    (
        user,
        membership,
        company,
        staff_permission,
        _company_id,
        redirect,
    ) = await _load_staff_context(request, require_admin=True, require_all_staff_access=True)
    if redirect:
        return redirect

    is_super_admin = bool(user.get("is_super_admin"))
    is_admin = is_super_admin or bool(membership and membership.get("is_admin"))
    extra = {
        "title": "Staff offboarding workflow",
        "is_super_admin": is_super_admin,
        "is_admin": is_admin,
        "staff_permission": staff_permission,
        "company": company,
    }
    return await _render_template("staff/workflows_offboarding.html", request, user, extra=extra)


async def staff_offboarding_workflow_policy(request: Request):
    (
        _user,
        _membership,
        _company,
        _staff_permission,
        company_id,
        redirect,
    ) = await _load_staff_context(request, require_admin=True, require_all_staff_access=True)
    if redirect:
        return redirect
    if company_id is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No active company")
    policy = await staff_workflow_repo.get_company_workflow_policy(
        company_id,
        default_workflow_key=staff_workflow_repo.DEFAULT_OFFBOARDING_WORKFLOW_KEY,
        direction=staff_workflow_repo.DIRECTION_OFFBOARDING,
    )
    return JSONResponse(
        {
            "policy": _normalise_workflow_policy_response(
                policy,
                default_workflow_key=staff_workflow_repo.DEFAULT_OFFBOARDING_WORKFLOW_KEY,
            ),
            "form_schema": _WORKFLOW_POLICY_FORM_SCHEMA,
            "step_catalog": _OFFBOARDING_STEP_CATALOG,
            "step_form_schema": _WORKFLOW_STEP_FORM_SCHEMA,
        }
    )


async def upsert_staff_offboarding_workflow_policy(request: Request):
    (
        user,
        _membership,
        _company,
        _staff_permission,
        company_id,
        redirect,
    ) = await _load_staff_context(request, require_admin=True, require_all_staff_access=True)
    if redirect:
        return redirect
    if company_id is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No active company")
    policy_input = await _extract_workflow_policy_payload(request)
    updated = await staff_workflow_repo.upsert_company_workflow_policy(
        company_id=company_id,
        workflow_key=policy_input.workflow_key or "",
        is_enabled=bool(policy_input.enabled),
        max_retries=int(policy_input.max_retries),
        config=policy_input.config,
        default_workflow_key=staff_workflow_repo.DEFAULT_OFFBOARDING_WORKFLOW_KEY,
        direction=staff_workflow_repo.DIRECTION_OFFBOARDING,
        delay_type=str(policy_input.delay_type or "scheduled"),
        workflow_name=policy_input.workflow_name,
        sort_order=int(policy_input.sort_order or 0),
    )
    await audit_service.log_action(
        user_id=int(user["id"]) if user.get("id") is not None else None,
        action="staff.workflows.offboarding.policy.upserted",
        entity_type="company",
        entity_id=company_id,
        metadata={
            "policy": _normalise_workflow_policy_response(
                updated,
                default_workflow_key=staff_workflow_repo.DEFAULT_OFFBOARDING_WORKFLOW_KEY,
            )
        },
    )
    return JSONResponse(
        {
            "success": True,
            "policy": _normalise_workflow_policy_response(
                updated,
                default_workflow_key=staff_workflow_repo.DEFAULT_OFFBOARDING_WORKFLOW_KEY,
            ),
        }
    )


async def list_staff_workflow_policies(direction: str, request: Request):
    (
        _user,
        _membership,
        _company,
        _staff_permission,
        company_id,
        redirect,
    ) = await _load_staff_context(request, require_admin=True, require_all_staff_access=True)
    if redirect:
        return redirect
    if company_id is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No active company")
    direction = direction.strip().lower()
    if direction not in (staff_workflow_repo.DIRECTION_ONBOARDING, staff_workflow_repo.DIRECTION_OFFBOARDING):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="direction must be 'onboarding' or 'offboarding'")
    default_key = staff_workflow_repo.DEFAULT_OFFBOARDING_WORKFLOW_KEY if direction == staff_workflow_repo.DIRECTION_OFFBOARDING else staff_workflow_repo.DEFAULT_WORKFLOW_KEY
    policies = await staff_workflow_repo.list_company_workflow_policies(company_id, direction=direction)
    catalog = _OFFBOARDING_STEP_CATALOG if direction == staff_workflow_repo.DIRECTION_OFFBOARDING else _ONBOARDING_STEP_CATALOG
    return JSONResponse(
        {
            "policies": [_normalise_workflow_policy_response(p, default_workflow_key=default_key) for p in policies],
            "step_catalog": catalog,
            "step_form_schema": _WORKFLOW_STEP_FORM_SCHEMA,
        }
    )


async def create_staff_workflow_policy(direction: str, request: Request):
    (
        user,
        _membership,
        _company,
        _staff_permission,
        company_id,
        redirect,
    ) = await _load_staff_context(request, require_admin=True, require_all_staff_access=True)
    if redirect:
        return redirect
    if company_id is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No active company")
    direction = direction.strip().lower()
    if direction not in (staff_workflow_repo.DIRECTION_ONBOARDING, staff_workflow_repo.DIRECTION_OFFBOARDING):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="direction must be 'onboarding' or 'offboarding'")
    default_key = staff_workflow_repo.DEFAULT_OFFBOARDING_WORKFLOW_KEY if direction == staff_workflow_repo.DIRECTION_OFFBOARDING else staff_workflow_repo.DEFAULT_WORKFLOW_KEY
    policy_input = await _extract_workflow_policy_payload(request)
    updated = await staff_workflow_repo.upsert_company_workflow_policy(
        company_id=company_id,
        workflow_key=policy_input.workflow_key or default_key,
        is_enabled=bool(policy_input.enabled),
        max_retries=int(policy_input.max_retries),
        config=policy_input.config,
        default_workflow_key=default_key,
        direction=direction,
        delay_type=str(policy_input.delay_type or "scheduled"),
        workflow_name=policy_input.workflow_name,
        sort_order=int(policy_input.sort_order or 0),
    )
    await audit_service.log_action(
        user_id=int(user["id"]) if user.get("id") is not None else None,
        action=f"staff.workflows.{direction}.policy.created",
        entity_type="company",
        entity_id=company_id,
        metadata={"policy": _normalise_workflow_policy_response(updated, default_workflow_key=default_key)},
    )
    return JSONResponse({"success": True, "policy": _normalise_workflow_policy_response(updated, default_workflow_key=default_key)})


async def update_staff_workflow_policy(direction: str, policy_id: int, request: Request):
    (
        user,
        _membership,
        _company,
        _staff_permission,
        company_id,
        redirect,
    ) = await _load_staff_context(request, require_admin=True, require_all_staff_access=True)
    if redirect:
        return redirect
    if company_id is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No active company")
    direction = direction.strip().lower()
    if direction not in (staff_workflow_repo.DIRECTION_ONBOARDING, staff_workflow_repo.DIRECTION_OFFBOARDING):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="direction must be 'onboarding' or 'offboarding'")
    existing = await staff_workflow_repo.get_company_workflow_policy_by_id(policy_id, company_id)
    if not existing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workflow policy not found")
    default_key = staff_workflow_repo.DEFAULT_OFFBOARDING_WORKFLOW_KEY if direction == staff_workflow_repo.DIRECTION_OFFBOARDING else staff_workflow_repo.DEFAULT_WORKFLOW_KEY
    policy_input = await _extract_workflow_policy_payload(request)
    updated = await staff_workflow_repo.upsert_company_workflow_policy(
        company_id=company_id,
        workflow_key=policy_input.workflow_key or str(existing.get("workflow_key") or default_key),
        is_enabled=bool(policy_input.enabled),
        max_retries=int(policy_input.max_retries),
        config=policy_input.config,
        default_workflow_key=default_key,
        direction=direction,
        delay_type=str(policy_input.delay_type or "scheduled"),
        workflow_name=policy_input.workflow_name,
        sort_order=int(policy_input.sort_order or 0),
    )
    await audit_service.log_action(
        user_id=int(user["id"]) if user.get("id") is not None else None,
        action=f"staff.workflows.{direction}.policy.updated",
        entity_type="company",
        entity_id=company_id,
        metadata={"policy_id": policy_id, "policy": _normalise_workflow_policy_response(updated, default_workflow_key=default_key)},
    )
    return JSONResponse({"success": True, "policy": _normalise_workflow_policy_response(updated, default_workflow_key=default_key)})


async def delete_staff_workflow_policy(direction: str, policy_id: int, request: Request):
    (
        user,
        _membership,
        _company,
        _staff_permission,
        company_id,
        redirect,
    ) = await _load_staff_context(request, require_admin=True, require_all_staff_access=True)
    if redirect:
        return redirect
    if company_id is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No active company")
    direction = direction.strip().lower()
    if direction not in (staff_workflow_repo.DIRECTION_ONBOARDING, staff_workflow_repo.DIRECTION_OFFBOARDING):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="direction must be 'onboarding' or 'offboarding'")
    deleted = await staff_workflow_repo.delete_company_workflow_policy(policy_id, company_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Workflow policy not found")
    await audit_service.log_action(
        user_id=int(user["id"]) if user.get("id") is not None else None,
        action=f"staff.workflows.{direction}.policy.deleted",
        entity_type="company",
        entity_id=company_id,
        metadata={"policy_id": policy_id},
    )
    return JSONResponse({"success": True})


async def staff_workflow_history_page(request: Request):
    (
        user,
        membership,
        company,
        staff_permission,
        _company_id,
        redirect,
    ) = await _load_staff_context(request, require_admin=True, require_all_staff_access=True)
    if redirect:
        return redirect

    is_super_admin = bool(user.get("is_super_admin"))
    if not is_super_admin:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Super admin access required")

    extra = {
        "title": "Workflow execution history",
        "is_super_admin": is_super_admin,
        "is_admin": True,
        "staff_permission": staff_permission,
        "company": company,
    }
    return await _render_template("staff/workflow_history.html", request, user, extra=extra)


async def staff_workflow_history_recent(request: Request):
    (
        user,
        _membership,
        _company,
        _staff_permission,
        company_id,
        redirect,
    ) = await _load_staff_context(request, require_admin=True, require_all_staff_access=True)
    if redirect:
        return redirect
    if not bool(user.get("is_super_admin")):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Super admin access required")
    if company_id is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No active company")

    direction_param = str(request.query_params.get("direction") or "").strip().lower() or None
    limit_param = min(max(1, int(request.query_params.get("limit") or "100")), 500)
    executions = await staff_workflow_repo.list_recent_executions_for_company(
        company_id, limit=limit_param, direction=direction_param
    )
    execution_ids = [int(ex["id"]) for ex in executions if ex.get("id")]

    # Fetch staff names for all staff IDs referenced
    staff_ids = list({int(ex["staff_id"]) for ex in executions if ex.get("staff_id")})
    staff_map: dict[int, dict] = {}
    if staff_ids:
        for sid in staff_ids:
            member = await staff_repo.get_staff_by_id(sid)
            if member:
                staff_map[sid] = member

    step_logs_map = await staff_workflow_repo.list_step_logs_for_execution_ids(execution_ids)

    def _serialise_dt(val: Any) -> str | None:
        if isinstance(val, datetime):
            return val.isoformat()
        return str(val) if val else None

    items = []
    for ex in executions:
        ex_id = int(ex["id"])
        sid = int(ex.get("staff_id") or 0)
        member = staff_map.get(sid, {})
        raw_logs = step_logs_map.get(ex_id, [])
        steps = []
        for log in raw_logs:
            req_p = log.get("request_payload")
            res_p = log.get("response_payload")
            if isinstance(req_p, str):
                try:
                    req_p = json.loads(req_p)
                except Exception:
                    req_p = {}
            if isinstance(res_p, str):
                try:
                    res_p = json.loads(res_p)
                except Exception:
                    res_p = {}
            steps.append({
                "id": int(log.get("id") or 0),
                "stepName": str(log.get("step_name") or ""),
                "status": str(log.get("status") or ""),
                "attempt": int(log.get("attempt") or 1),
                "requestPayload": req_p or {},
                "responsePayload": res_p or {},
                "errorMessage": log.get("error_message"),
                "startedAt": _serialise_dt(log.get("started_at")),
                "completedAt": _serialise_dt(log.get("completed_at")),
            })
        items.append({
            "executionId": ex_id,
            "staffId": sid,
            "staffName": f"{member.get('first_name', '')} {member.get('last_name', '')}".strip() or f"Staff #{sid}",
            "direction": str(ex.get("direction") or "onboarding"),
            "state": str(ex.get("state") or ""),
            "workflowKey": str(ex.get("workflow_key") or ""),
            "currentStep": ex.get("current_step"),
            "retriesUsed": int(ex.get("retries_used") or 0),
            "lastError": ex.get("last_error"),
            "helpdeskTicketId": ex.get("helpdesk_ticket_id"),
            "requestedAt": _serialise_dt(ex.get("requested_at")),
            "startedAt": _serialise_dt(ex.get("started_at")),
            "completedAt": _serialise_dt(ex.get("completed_at")),
            "steps": steps,
        })
    return JSONResponse({"executions": items, "total": len(items)})


async def retry_workflow_execution(execution_id: int, request: Request):
    (
        user,
        _membership,
        _company,
        _staff_permission,
        _company_id,
        redirect,
    ) = await _load_staff_context(request, require_admin=True, require_all_staff_access=True)
    if redirect:
        return redirect
    if not bool(user.get("is_super_admin")):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Super admin access required")

    try:
        result = await staff_onboarding_workflow_service.retry_failed_workflow_execution(
            execution_id=execution_id,
            initiated_by_user_id=int(user["id"]),
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return JSONResponse(result)


async def resume_workflow_execution(execution_id: int, request: Request):
    (
        user,
        _membership,
        _company,
        _staff_permission,
        _company_id,
        redirect,
    ) = await _load_staff_context(request, require_admin=True, require_all_staff_access=True)
    if redirect:
        return redirect
    if not bool(user.get("is_super_admin")):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Super admin access required")

    try:
        result = await staff_onboarding_workflow_service.resume_paused_workflow_execution(
            execution_id=execution_id,
            initiated_by_user_id=int(user["id"]),
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return JSONResponse(result)


async def create_staff_member(request: Request):
    (
        user,
        membership,
        company,
        staff_permission,
        company_id,
        redirect,
    ) = await _load_staff_context(request, require_admin=True)
    if redirect:
        return redirect
    if company_id is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No active company")
    is_super_admin = bool(user.get("is_super_admin"))

    form = await request.form()
    field_config = await staff_field_config_service.load_effective_company_staff_fields(company_id)
    submitted = {str(key): form.get(key) for key in form.keys()}
    values, validation_errors = staff_field_config_service.validate_staff_form_values(
        submitted, field_config
    )
    if validation_errors:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="; ".join(validation_errors),
        )

    first_name = str(values.get("first_name") or "").strip()
    last_name = str(values.get("last_name") or "").strip()
    email = str(values.get("email") or "").strip() or None
    if not first_name or not last_name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="First name and last name are required",
        )

    mobile_phone = str(values.get("mobile_phone") or "").strip() or None
    raw_date_onboarded = str(submitted.get("date_onboarded") or "").strip()
    submitted_timezone = str(
        submitted.get("browser_timezone")
        or submitted.get("onboarding_timezone")
        or submitted.get("date_onboarded_timezone")
        or ""
    ).strip() or None
    date_onboarded = _parse_local_datetime_to_utc(
        raw_date_onboarded,
        timezone_name=submitted_timezone,
        assume_midnight=True,
    )
    department = str(values.get("department") or "").strip() or None
    if not is_super_admin and staff_permission == 1:
        user_department = await _get_current_user_staff_department(user, company_id)
        if not _departments_match(user_department, department):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Department staff access can only request staff for your department",
            )

    custom_definitions = await staff_custom_fields_repo.list_field_definitions(company_id)
    custom_values: dict[str, Any] = {}
    for definition in custom_definitions:
        key = str(definition.get("name") or "").strip()
        if not key:
            continue
        field_type = str(definition.get("field_type") or "text")
        raw_value = form.get(key)
        if field_type == "checkbox":
            custom_values[key] = str(raw_value or "").lower() in {"1", "true", "on", "yes"}
        elif field_type == "multiselect":
            raw_values = form.getlist(key)
            custom_values[key] = ",".join(v for v in (str(v).strip() for v in raw_values) if v) or None
        else:
            custom_values[key] = str(raw_value or "").strip() or None

    requester_id = int(user["id"]) if user.get("id") is not None else None

    created = await staff_requests_repo.create_request(
        company_id=company_id,
        first_name=first_name,
        last_name=last_name,
        email=email,
        mobile_phone=mobile_phone,
        date_onboarded=date_onboarded,
        department=department,
        job_title=None,
        request_notes=None,
        custom_fields=custom_values or None,
        requested_by_user_id=requester_id,
        requested_at=datetime.now(tz=timezone.utc),
    )

    approver_user_ids = await staff_onboarding_workflow_service.notify_staff_approval_requested(
        company_id=company_id,
        staff={
            "id": created["id"],
            "company_id": company_id,
            "first_name": first_name,
            "last_name": last_name,
            "email": email,
            "onboarding_status": "awaiting_approval",
            "approval_status": "pending",
        },
        requester_user_id=requester_id,
    )
    await audit_service.log_action(
        user_id=requester_id,
        action="staff.onboarding.requested",
        entity_type="staff_request",
        entity_id=int(created["id"]),
        metadata={
            "company_id": company_id,
            "approval_status": "pending",
            "approver_user_ids": approver_user_ids,
            "onboarding_input": {
                "date_onboarded_local": raw_date_onboarded or None,
                "timezone": submitted_timezone,
            },
        },
    )

    return RedirectResponse(url="/staff", status_code=status.HTTP_303_SEE_OTHER)


async def update_staff_member(staff_id: int, request: Request):
    (
        user,
        membership,
        company,
        staff_permission,
        company_id,
        redirect,
    ) = await _load_staff_context(request)
    if redirect:
        return redirect

    existing = await staff_repo.get_staff_by_id(staff_id)
    if not existing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Staff not found")

    is_super_admin = bool(user.get("is_super_admin"))
    if not is_super_admin and existing.get("company_id") != company_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")

    payload = await request.json()
    if not isinstance(payload, dict):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid request payload")

    def get_value(*keys: str) -> Any:
        for key in keys:
            if key in payload:
                return payload[key]
        return None

    if is_super_admin:
        first_name = (get_value("firstName", "first_name") or existing.get("first_name") or "").strip()
        last_name = (get_value("lastName", "last_name") or existing.get("last_name") or "").strip()
        email = (get_value("email") or existing.get("email") or "").strip()
        mobile_phone = (get_value("mobilePhone", "mobile_phone") or existing.get("mobile_phone") or "").strip() or None
        date_onboarded = _parse_input_datetime(get_value("dateOnboarded", "date_onboarded"), assume_midnight=True) or _parse_input_datetime(existing.get("date_onboarded"))
        if existing.get("date_onboarded") and not get_value("dateOnboarded", "date_onboarded"):
            date_onboarded = _parse_input_datetime(existing.get("date_onboarded"))
        enabled = bool(get_value("enabled") if get_value("enabled") is not None else existing.get("enabled", True))
        street = get_value("street") or existing.get("street")
        city = get_value("city") or existing.get("city")
        state_val = get_value("state") or existing.get("state")
        postcode = get_value("postcode") or existing.get("postcode")
        country = get_value("country") or existing.get("country")
        department = get_value("department") or existing.get("department")
        job_title = get_value("jobTitle", "job_title") or existing.get("job_title")
        org_company = get_value("company", "org_company") or existing.get("org_company")
        manager_name = get_value("managerName", "manager_name") or existing.get("manager_name")
        account_action = get_value("accountAction", "account_action") or existing.get("account_action")
    else:
        first_name = existing.get("first_name") or ""
        last_name = existing.get("last_name") or ""
        email = existing.get("email") or ""
        mobile_phone = existing.get("mobile_phone")
        date_onboarded = _parse_input_datetime(existing.get("date_onboarded"))
        enabled = bool(existing.get("enabled", True))
        street = existing.get("street")
        city = existing.get("city")
        state_val = existing.get("state")
        postcode = existing.get("postcode")
        country = existing.get("country")
        department = existing.get("department")
        job_title = existing.get("job_title")
        org_company = existing.get("org_company")
        manager_name = existing.get("manager_name")
        account_action = existing.get("account_action")

    date_offboarded = _parse_input_datetime(get_value("dateOffboarded", "date_offboarded"))
    if date_offboarded is None and existing.get("date_offboarded"):
        date_offboarded = _parse_input_datetime(existing.get("date_offboarded"))

    updated = await staff_repo.update_staff(
        staff_id,
        company_id=existing.get("company_id") or company_id,
        first_name=first_name,
        last_name=last_name,
        email=email,
        mobile_phone=mobile_phone,
        date_onboarded=date_onboarded,
        date_offboarded=date_offboarded,
        enabled=enabled,
        is_ex_staff=bool(existing.get("is_ex_staff", False)),
        street=street,
        city=city,
        state=state_val,
        postcode=postcode,
        country=country,
        department=department,
        job_title=job_title,
        org_company=org_company,
        manager_name=manager_name,
        account_action=account_action,
        syncro_contact_id=existing.get("syncro_contact_id"),
        onboarding_status=existing.get("onboarding_status"),
        onboarding_complete=bool(existing.get("onboarding_complete", False)),
        onboarding_completed_at=existing.get("onboarding_completed_at"),
    )
    raw_custom_fields = get_value("customFields", "custom_fields")
    if isinstance(raw_custom_fields, dict):
        await staff_custom_fields_repo.set_staff_field_values_by_name(
            company_id=int(existing.get("company_id") or company_id or 0),
            staff_id=staff_id,
            values=raw_custom_fields,
        )
        refreshed = await staff_repo.get_staff_by_id(staff_id)
        if refreshed:
            updated = refreshed

    requested_status = str(get_value("onboardingStatus", "onboarding_status") or "").strip().lower()
    if requested_status in {
        staff_onboarding_workflow_service.STATE_APPROVED,
        staff_onboarding_workflow_service.STATE_OFFBOARDING_APPROVED,
    }:
        await staff_onboarding_workflow_service.enqueue_staff_onboarding_workflow(
            company_id=int(updated.get("company_id") or company_id or 0),
            staff_id=staff_id,
            initiated_by_user_id=int(user["id"]) if user.get("id") is not None else None,
            direction=(
                staff_onboarding_workflow_service.DIRECTION_OFFBOARDING
                if requested_status == staff_onboarding_workflow_service.STATE_OFFBOARDING_APPROVED
                else staff_onboarding_workflow_service.DIRECTION_ONBOARDING
            ),
        )

    updated["workflow_status"] = await staff_onboarding_workflow_service.get_staff_workflow_status(staff_id)
    return JSONResponse({"success": True, "staff": updated})


async def request_staff_offboarding(staff_id: int, request: Request):
    (
        user,
        membership,
        company,
        staff_permission,
        company_id,
        redirect,
    ) = await _load_staff_context(request, require_admin=True)
    if redirect:
        return redirect

    existing = await staff_repo.get_staff_by_id(staff_id)
    if not existing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Staff not found")

    is_super_admin = bool(user.get("is_super_admin"))
    if not is_super_admin and existing.get("company_id") != company_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
    if not is_super_admin and staff_permission == 1:
        user_department = await _get_current_user_staff_department(user, company_id)
        target_department = str(existing.get("department") or "").strip() or None
        if not _departments_match(user_department, target_department):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")

    if not bool(existing.get("enabled", False)) or bool(existing.get("is_ex_staff", False)):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only active staff members can be offboarding requested",
        )

    if str(existing.get("account_action") or "").strip().lower() == "offboard requested":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="An offboarding request is already pending for this staff member",
        )

    payload = await request.json()
    offboarding_type = str(payload.get("offboardingType") or payload.get("offboarding_type") or payload.get("type") or "").strip()
    legacy_reason = str(payload.get("reason") or "").strip()
    if not offboarding_type and legacy_reason in {"Resignation", "Termination"}:
        offboarding_type = legacy_reason
    requested_at_raw = str(payload.get("requestedAt", payload.get("requested_at")) or "").strip()
    requested_timezone = str(payload.get("requestedTimezone") or payload.get("requested_timezone") or "").strip() or None
    requested_at = _parse_local_datetime_to_utc(
        requested_at_raw,
        timezone_name=requested_timezone,
    )
    notes = str(payload.get("notes") or "").strip() or None
    company_email_domains = list((company or {}).get("email_domains") or [])

    # Optional offboarding request fields
    out_of_office_message = str(payload.get("outOfOfficeMessage") or payload.get("out_of_office_message") or "").strip() or None
    email_forward_to_staff_id_raw = payload.get("emailForwardToStaffId") or payload.get("email_forward_to_staff_id")
    mailbox_grant_staff_ids_raw = payload.get("mailboxGrantStaffIds") or payload.get("mailbox_grant_staff_ids") or []

    # Resolve email forwarding target
    email_forward_to: str | None = None
    if email_forward_to_staff_id_raw is not None:
        try:
            forward_staff_id = int(email_forward_to_staff_id_raw)
        except (TypeError, ValueError):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid emailForwardToStaffId")
        forward_staff = await staff_repo.get_staff_by_id(forward_staff_id)
        if not forward_staff or not forward_staff.get("email"):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Email forwarding target staff not found")
        if int(forward_staff.get("company_id") or 0) != int(company_id or 0):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Email forwarding target must belong to this company")
        if (
            not bool(forward_staff.get("enabled", False))
            or bool(forward_staff.get("is_ex_staff", False))
        ):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Email forwarding target must be an active staff member")
        if not _staff_member_is_offboarding_mail_choice(forward_staff, company_email_domains):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Email forwarding target must use an allowed company email domain")
        email_forward_to = str(forward_staff["email"]).strip().lower()

    # Resolve mailbox grant access list
    mailbox_grant_emails: list[str] = []
    if isinstance(mailbox_grant_staff_ids_raw, list):
        for raw_id in mailbox_grant_staff_ids_raw:
            try:
                grant_staff_id = int(raw_id)
            except (TypeError, ValueError):
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid mailboxGrantStaffIds entry")
            grant_staff = await staff_repo.get_staff_by_id(grant_staff_id)
            if not grant_staff or not grant_staff.get("email"):
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Mailbox grant staff #{grant_staff_id} not found")
            if int(grant_staff.get("company_id") or 0) != int(company_id or 0):
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Mailbox grant target #{grant_staff_id} must belong to this company")
            if (
                not bool(grant_staff.get("enabled", False))
                or bool(grant_staff.get("is_ex_staff", False))
            ):
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Mailbox grant target #{grant_staff_id} must be an active staff member")
            if not _staff_member_is_offboarding_mail_choice(grant_staff, company_email_domains):
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Mailbox grant target #{grant_staff_id} must use an allowed company email domain")
            grant_email = str(grant_staff["email"]).strip().lower()
            if grant_email not in mailbox_grant_emails:
                mailbox_grant_emails.append(grant_email)

    mailbox_grant_emails_json: str | None = json.dumps(mailbox_grant_emails) if mailbox_grant_emails else None

    if offboarding_type not in {"Resignation", "Termination"}:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Offboarding type must be Resignation or Termination")
    if len(notes or "") > 2000:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Notes must be 2000 characters or fewer")
    if not requested_at_raw or not _raw_value_includes_time(requested_at_raw):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Requested offboarding date and time are both required",
        )
    if requested_at is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="A valid requested offboarding date/time is required",
        )

    request_notes = f"Type: {offboarding_type}" if not notes else f"Type: {offboarding_type}\n\nNotes: {notes}"

    updated = await staff_repo.update_staff(
        staff_id,
        company_id=int(existing.get("company_id") or company_id or 0),
        first_name=existing.get("first_name") or "",
        last_name=existing.get("last_name") or "",
        email=existing.get("email") or "",
        mobile_phone=existing.get("mobile_phone"),
        date_onboarded=_parse_input_datetime(existing.get("date_onboarded")),
        date_offboarded=None,
        enabled=bool(existing.get("enabled", True)),
        is_ex_staff=bool(existing.get("is_ex_staff", False)),
        street=existing.get("street"),
        city=existing.get("city"),
        state=existing.get("state"),
        postcode=existing.get("postcode"),
        country=existing.get("country"),
        department=existing.get("department"),
        job_title=existing.get("job_title"),
        org_company=existing.get("org_company"),
        manager_name=existing.get("manager_name"),
        account_action="Offboard Requested",
        syncro_contact_id=existing.get("syncro_contact_id"),
        onboarding_status=staff_onboarding_workflow_service.STATE_OFFBOARDING_AWAITING_APPROVAL,
        onboarding_complete=bool(existing.get("onboarding_complete", False)),
        onboarding_completed_at=existing.get("onboarding_completed_at"),
        approval_status="pending",
        requested_by_user_id=int(user["id"]) if user.get("id") is not None else None,
        requested_at=datetime.now(tz=timezone.utc),
        approved_by_user_id=None,
        approved_at=None,
        request_notes=request_notes,
        approval_notes=None,
        m365_last_sign_in=_parse_input_datetime(existing.get("m365_last_sign_in")),
        offboarding_out_of_office=out_of_office_message,
        offboarding_email_forward_to=email_forward_to,
        offboarding_mailbox_grant_emails=mailbox_grant_emails_json,
    )

    approver_user_ids = await staff_onboarding_workflow_service.notify_staff_approval_requested(
        company_id=int(updated["company_id"]),
        staff=updated,
        requester_user_id=int(user["id"]) if user.get("id") is not None else None,
        direction=staff_onboarding_workflow_service.DIRECTION_OFFBOARDING,
    )
    await audit_service.log_action(
        user_id=int(user["id"]) if user.get("id") is not None else None,
        action="staff.offboarding.requested",
        entity_type="staff",
        entity_id=staff_id,
        metadata={
            "company_id": int(existing.get("company_id") or company_id or 0),
            "requested_offboarding_at": requested_at,
            "offboarding_type": offboarding_type,
            "notes": notes,
            "requested_offboarding_input": {
                "requested_at_local": requested_at_raw,
                "timezone": requested_timezone,
            },
            "approver_user_ids": approver_user_ids,
            "out_of_office_message": out_of_office_message,
            "email_forward_to": email_forward_to,
            "mailbox_grant_emails": mailbox_grant_emails,
        },
    )

    policy = await staff_workflow_repo.get_company_workflow_policy(
        int(updated["company_id"]),
        default_workflow_key=staff_workflow_repo.DEFAULT_OFFBOARDING_WORKFLOW_KEY,
        direction=staff_workflow_repo.DIRECTION_OFFBOARDING,
    )
    scheduled_for_utc, normalized_timezone = staff_onboarding_workflow_service._compute_scheduled_execution(
        staff=updated,
        direction=staff_onboarding_workflow_service.DIRECTION_OFFBOARDING,
        requested_timezone=requested_timezone,
    )
    await staff_workflow_repo.create_or_reset_execution(
        company_id=int(updated["company_id"]),
        staff_id=staff_id,
        workflow_key=str(policy.get("workflow_key") or staff_workflow_repo.DEFAULT_OFFBOARDING_WORKFLOW_KEY),
        direction=staff_onboarding_workflow_service.DIRECTION_OFFBOARDING,
        scheduled_for_utc=scheduled_for_utc,
        requested_timezone=normalized_timezone,
    )

    return JSONResponse({"success": True, "staff": updated})


async def delete_staff_member(staff_id: int, request: Request):
    (
        user,
        membership,
        company,
        staff_permission,
        company_id,
        redirect,
    ) = await _load_staff_context(request, require_super_admin=True)
    if redirect:
        return redirect
    existing = await staff_repo.get_staff_by_id(staff_id)
    if not existing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Staff not found")
    await staff_repo.delete_staff(staff_id)
    return JSONResponse({"success": True})


async def set_staff_enabled(request: Request):
    (
        user,
        membership,
        company,
        staff_permission,
        company_id,
        redirect,
    ) = await _load_staff_context(request)
    if redirect:
        return redirect
    form = await request.form()
    staff_id_raw = form.get("staffId")
    enabled_raw = form.get("enabled", "0")
    try:
        staff_id = int(staff_id_raw)
    except (TypeError, ValueError):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid staff identifier")
    enabled = str(enabled_raw).lower() in {"1", "true", "on"}
    existing = await staff_repo.get_staff_by_id(staff_id)
    if not existing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Staff not found")
    is_super_admin = bool(user.get("is_super_admin"))
    if not is_super_admin and existing.get("company_id") != company_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
    await staff_repo.set_enabled(staff_id, enabled)
    return RedirectResponse(url="/staff", status_code=status.HTTP_303_SEE_OTHER)


async def verify_staff_member(staff_id: int, request: Request):
    (
        user,
        membership,
        company,
        staff_permission,
        company_id,
        redirect,
    ) = await _load_staff_context(request, require_super_admin=True)
    if redirect:
        return redirect
    staff = await staff_repo.get_staff_by_id(staff_id)
    if not staff:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Staff not found")
    if not staff.get("mobile_phone"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No mobile phone for staff member")

    await staff_repo.purge_expired_verification_codes()
    code = f"{secrets.randbelow(900000) + 100000:06d}"
    admin_name = " ".join(filter(None, [user.get("first_name"), user.get("last_name")])).strip()
    await staff_repo.upsert_verification_code(
        staff_id,
        code=code,
        admin_name=admin_name or None,
    )

    staff_company = await company_repo.get_company_by_id(staff.get("company_id"))
    company_name = staff_company.get("name") if staff_company else ""
    
    # Construct SMS message
    message_parts = [f"Your verification code is: {code}"]
    if admin_name:
        message_parts.append(f"Requested by: {admin_name}")
    if company_name:
        message_parts.append(f"Company: {company_name}")
    message = " | ".join(message_parts)
    
    # Send SMS via SMS Gateway module
    result: dict[str, Any] = {}
    try:
        result = await modules_service.trigger_module(
            "sms-gateway",
            {
                "message": message,
                "phoneNumbers": [staff.get("mobile_phone")],
            },
            background=False,
        )
    except Exception as exc:
        log_error("SMS Gateway module failed", staff_id=staff_id, error=str(exc))
        result = {"status": "error", "error": str(exc)}

    status_code = int(result.get("response_status")) if result.get("response_status") is not None else None
    # Accept both "succeeded" and "queued" as success states
    # (background=False should always return "succeeded", but we check "queued" defensively)
    success = result.get("status") in {"succeeded", "queued"}

    return JSONResponse({
        "success": success,
        "status": status_code,
        "code": code,
    })


async def invite_staff_member(staff_id: int, request: Request):
    (
        user,
        membership,
        company,
        staff_permission,
        company_id,
        redirect,
    ) = await _load_staff_context(request, require_admin=True)
    if redirect:
        return redirect
    staff = await staff_repo.get_staff_by_id(staff_id)
    if not staff:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Staff not found")
    if not staff.get("email"):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="No email for staff member")
    if not bool(user.get("is_super_admin")) and staff.get("company_id") != company_id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")

    existing_user = await user_repo.get_user_by_email(staff["email"])
    if existing_user:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="User already exists")

    temp_password = secrets.token_urlsafe(12)
    created_user = await user_repo.create_user(
        email=staff["email"],
        password=temp_password,
        first_name=staff.get("first_name"),
        last_name=staff.get("last_name"),
        mobile_phone=staff.get("mobile_phone"),
        company_id=staff.get("company_id"),
    )
    await staff_access_service.apply_pending_access_for_user(created_user)
    await user_repo.update_user(created_user["id"], force_password_change=1)
    await user_company_repo.upsert_user_company(
        user_id=created_user["id"],
        company_id=staff.get("company_id"),
        can_manage_staff=False,
        staff_permission=0,
        is_admin=False,
    )
    token = secrets.token_urlsafe(32)
    expires_at = datetime.utcnow() + timedelta(hours=1)
    await auth_repo.create_password_reset_token(
        user_id=created_user["id"],
        token=token,
        expires_at=expires_at,
    )
    base_url = str(settings.portal_url).rstrip("/") if settings.portal_url else None
    reset_path = f"/reset-password?token={token}"
    reset_link = f"{base_url}{reset_path}" if base_url else reset_path
    inviter_email = user.get("email") or settings.smtp_user or None
    staff_name = staff.get("first_name") or staff.get("last_name") or "there"
    company_name = (company or {}).get("name") if company else None
    company_phrase = f" for {company_name}" if company_name else ""
    company_phrase_html = f" for {escape(company_name)}" if company_name else ""
    text_body = (
        f"Hello {staff_name},\n\n"
        f"You've been invited to access {settings.app_name}{company_phrase}. "
        f"Use the link below to set your password and activate your account:\n\n"
        f"{reset_link}\n\n"
        "The link expires in one hour. If you were not expecting this invitation you can ignore this email."
    )
    html_body = (
        f"<p>Hello {escape(staff_name)},</p>"
        f"<p>You've been invited to access {escape(settings.app_name)}{company_phrase_html}.</p>"
        f"<p><a href=\"{escape(reset_link)}\">Set your password and activate your account</a></p>"
        "<p>The link expires in one hour. If you were not expecting this invitation you can ignore this email.</p>"
    )
    try:
        sent, event_metadata = await email_service.send_email(
            subject=f"You're invited to {settings.app_name}",
            recipients=[staff["email"]],
            text_body=text_body,
            html_body=html_body,
            reply_to=inviter_email,
        )
        if not sent:
            log_info(
                "Staff invitation email skipped due to SMTP configuration",
                staff_id=staff_id,
                invited_user_id=created_user["id"],
                event_id=(event_metadata or {}).get("id") if isinstance(event_metadata, dict) else None,
            )
        else:
            log_info(
                "Staff invitation email sent",
                staff_id=staff_id,
                invited_user_id=created_user["id"],
                event_id=(event_metadata or {}).get("id") if isinstance(event_metadata, dict) else None,
            )
    except email_service.EmailDispatchError as exc:  # pragma: no cover - logged for diagnostics
        log_error(
            "Failed to send staff invitation email",
            staff_id=staff_id,
            invited_user_id=created_user["id"],
            error=str(exc),
            event_id=(event_metadata or {}).get("id") if "event_metadata" in locals() and isinstance(event_metadata, dict) else None,
        )

    log_info(
        "Staff invitation generated",
        staff_id=staff_id,
        invited_user_id=created_user["id"],
    )
    return JSONResponse({"success": True})


async def m365_reset_staff_password(staff_id: int, request: Request):
    """Reset the Office 365 password for the staff member identified by *staff_id*.

    Returns the newly generated password so the admin can communicate it to the
    user.  The password is not persisted in MyPortal.

    Requires super-admin privileges.
    """
    (
        user,
        membership,
        company,
        staff_permission,
        company_id,
        redirect,
    ) = await _load_staff_context(request, require_super_admin=True)
    if redirect:
        return redirect
    staff = await staff_repo.get_staff_by_id(staff_id)
    if not staff:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Staff not found")
    email = str(staff.get("email") or "").strip()
    if not email:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Staff member has no email address")
    staff_company_id = int(staff.get("company_id") or 0)
    try:
        new_password = await m365_service.reset_user_password(staff_company_id, email)
    except m365_service.M365Error as exc:
        if exc.http_status == 403:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))
    await audit_service.log_action(
        entity_type="staff",
        entity_id=staff_id,
        action="staff.m365.password_reset",
        user_id=int(user["id"]) if user.get("id") is not None else None,
        metadata={"email": email},
    )
    return JSONResponse({"success": True, "password": new_password})


async def m365_set_staff_sign_in(staff_id: int, request: Request):
    """Enable or disable Office 365 sign-in for the staff member.

    Expects a JSON body: ``{"enabled": true}`` or ``{"enabled": false}``.

    Requires super-admin privileges.
    """
    (
        user,
        membership,
        company,
        staff_permission,
        company_id,
        redirect,
    ) = await _load_staff_context(request, require_super_admin=True)
    if redirect:
        return redirect
    staff = await staff_repo.get_staff_by_id(staff_id)
    if not staff:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Staff not found")
    email = str(staff.get("email") or "").strip()
    if not email:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Staff member has no email address")
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid JSON body")
    if "enabled" not in body:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="'enabled' field is required")
    enabled = bool(body["enabled"])
    staff_company_id = int(staff.get("company_id") or 0)
    try:
        await m365_service.set_user_sign_in_enabled(staff_company_id, email, enabled=enabled)
    except m365_service.M365Error as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc))
    await audit_service.log_action(
        entity_type="staff",
        entity_id=staff_id,
        action="staff.m365.sign_in_toggled",
        user_id=int(user["id"]) if user.get("id") is not None else None,
        metadata={"email": email, "enabled": enabled},
    )
    return JSONResponse({"success": True, "enabled": enabled})
