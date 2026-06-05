"""Admin routes for Matrix chat auto-assign rules."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Query, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse

from app.core.logging import log_error
from app.security.flash import flash_redirect
from app.repositories import chat_auto_assign as assign_repo

__all__ = ["router"]

router = APIRouter(tags=["Matrix Chat Auto-Assign"])


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


def _parse_priority(form: Any) -> int:
    """Parse priority from form data, defaulting to 0."""
    try:
        return int(form.get("priority") or 0)
    except (TypeError, ValueError):
        return 0


def _parse_tech_user_id(form: Any) -> int | None:
    """Parse assigned_tech_user_id from form data."""
    raw = form.get("assigned_tech_user_id")
    if not raw:
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        return None


async def _render_dashboard(
    request: Request,
    user: dict[str, Any],
    *,
    editing_rule_id: int | None = None,
    success_message: str | None = None,
    error_message: str | None = None,
    status_code: int = status.HTTP_200_OK,
) -> HTMLResponse:
    main_module = _main()
    rules = await assign_repo.list_rules()
    technicians = await assign_repo.list_all_technicians()
    technicians_with_matrix = await assign_repo.list_technicians_with_matrix_id()

    editing_rule = None
    if editing_rule_id is not None:
        editing_rule = await assign_repo.get_rule(editing_rule_id)

    extra = {
        "title": "Matrix Chat Auto-Assign",
        "rules": rules,
        "technicians": technicians,
        "technicians_with_matrix": technicians_with_matrix,
        "editing_rule": editing_rule,
        "success_message": success_message,
        "error_message": error_message,
    }
    response = await main_module._render_template(
        "admin/matrix_chat_assign.html", request, user, extra=extra
    )
    response.status_code = status_code
    return response


def _parse_conditions(form: Any) -> list[dict[str, Any]]:
    """Extract condition entries from the submitted form data.

    Form fields follow the pattern:
      conditions[0][type], conditions[0][operator], conditions[0][value]
      conditions[1][type], ...
    """
    conditions: dict[int, dict[str, str]] = {}
    for key, val in form.multi_items():
        if not key.startswith("conditions["):
            continue
        # key format: conditions[<index>][<field>]
        try:
            idx_end = key.index("]")
            idx = int(key[len("conditions["):idx_end])
            field_start = key.index("[", idx_end) + 1
            field_end = key.index("]", field_start)
            field = key[field_start:field_end]
        except (ValueError, IndexError):
            continue
        if idx not in conditions:
            conditions[idx] = {}
        conditions[idx][field] = str(val)

    result = []
    for _, cond in sorted(conditions.items()):
        ctype = cond.get("type", "").strip()
        operator = cond.get("operator", "contains").strip()
        value = cond.get("value", "").strip()
        if ctype:
            result.append({"type": ctype, "operator": operator, "value": value})
    return result


def _normalize_conditions(*, is_default: bool, conditions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Default fallback rules never persist conditions."""
    if is_default:
        return []
    return conditions


@router.get("/admin/modules/matrix-chat-assign", response_class=HTMLResponse)
async def admin_matrix_chat_assign_page(
    request: Request,
    rule_id: int | None = Query(default=None, alias="ruleId"),
):
    main_module = _main()
    current_user, redirect = await main_module._require_super_admin_page(request)
    if redirect:
        return redirect
    return await _render_dashboard(request, current_user, editing_rule_id=rule_id)


@router.post("/admin/modules/matrix-chat-assign/rules", response_class=HTMLResponse)
async def admin_create_auto_assign_rule(request: Request):
    main_module = _main()
    current_user, redirect = await main_module._require_super_admin_page(request)
    if redirect:
        return redirect

    form = await request.form()
    name = str(form.get("name", "")).strip()
    if not name:
        return await _render_dashboard(
            request, current_user,
            error_message="Rule name is required.",
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        )

    try:
        priority = _parse_priority(form)
    except (TypeError, ValueError):
        priority = 0

    is_default = _form_bool(form, "is_default")
    is_active = _form_bool(form, "is_active")
    assigned_tech_user_id = _parse_tech_user_id(form)

    conditions = _normalize_conditions(
        is_default=is_default,
        conditions=_parse_conditions(form),
    )

    try:
        await assign_repo.create_rule(
            name=name,
            priority=priority,
            is_default=is_default,
            assigned_tech_user_id=assigned_tech_user_id,
            conditions=conditions,
            is_active=is_active,
        )
    except Exception as exc:
        log_error("Failed to create auto-assign rule", error=str(exc))
        return await _render_dashboard(
            request, current_user,
            error_message="Failed to create rule. Please try again.",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    return flash_redirect(
        "/admin/modules/matrix-chat-assign", "Rule created successfully.", "success"
    )


@router.post(
    "/admin/modules/matrix-chat-assign/rules/{rule_id}",
    response_class=HTMLResponse,
)
async def admin_update_auto_assign_rule(rule_id: int, request: Request):
    main_module = _main()
    current_user, redirect = await main_module._require_super_admin_page(request)
    if redirect:
        return redirect

    existing = await assign_repo.get_rule(rule_id)
    if not existing:
        return flash_redirect(
            "/admin/modules/matrix-chat-assign", "Rule not found.", "error"
        )

    form = await request.form()
    name = str(form.get("name", "")).strip()
    if not name:
        return await _render_dashboard(
            request, current_user,
            editing_rule_id=rule_id,
            error_message="Rule name is required.",
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
        )

    try:
        priority = _parse_priority(form)
    except (TypeError, ValueError):
        priority = 0

    is_default = _form_bool(form, "is_default")
    is_active = _form_bool(form, "is_active")
    assigned_tech_user_id = _parse_tech_user_id(form)

    conditions = _normalize_conditions(
        is_default=is_default,
        conditions=_parse_conditions(form),
    )

    try:
        await assign_repo.update_rule(
            rule_id,
            name=name,
            priority=priority,
            is_default=is_default,
            assigned_tech_user_id=assigned_tech_user_id,
            conditions=conditions,
            is_active=is_active,
        )
    except Exception as exc:
        log_error("Failed to update auto-assign rule", rule_id=rule_id, error=str(exc))
        return await _render_dashboard(
            request, current_user,
            editing_rule_id=rule_id,
            error_message="Failed to update rule. Please try again.",
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    return flash_redirect(
        "/admin/modules/matrix-chat-assign", "Rule updated successfully.", "success"
    )


@router.post(
    "/admin/modules/matrix-chat-assign/rules/{rule_id}/delete",
    response_class=HTMLResponse,
)
async def admin_delete_auto_assign_rule(rule_id: int, request: Request):
    main_module = _main()
    current_user, redirect = await main_module._require_super_admin_page(request)
    if redirect:
        return redirect

    try:
        await assign_repo.delete_rule(rule_id)
    except Exception as exc:
        log_error("Failed to delete auto-assign rule", rule_id=rule_id, error=str(exc))
        return flash_redirect(
            "/admin/modules/matrix-chat-assign",
            "Failed to delete rule.",
            "error",
        )

    return flash_redirect(
        "/admin/modules/matrix-chat-assign", "Rule deleted.", "success"
    )
