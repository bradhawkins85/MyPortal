"""Issue tracker handlers for the ``issue_tracker`` feature pack."""

from __future__ import annotations

from typing import Any

import aiomysql
from fastapi import Query, Request, status
from fastapi.responses import RedirectResponse

from app.security.flash import flash_redirect, set_flash


def _main():
    from app import main as main_module

    return main_module



def _coerce_company_id(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


async def _accessible_company_options(user: dict[str, Any]) -> tuple[list[dict[str, Any]], set[int]]:
    from app.services import company_access

    memberships = await company_access.list_accessible_companies(user)
    options: list[dict[str, Any]] = []
    allowed_ids: set[int] = set()
    for record in memberships:
        company_id = _coerce_company_id(record.get("company_id") or record.get("id"))
        if company_id is None:
            continue
        name = str(record.get("company_name") or record.get("name") or "").strip()
        allowed_ids.add(company_id)
        options.append({"id": company_id, "name": name or f"Company #{company_id}"})
    options.sort(key=lambda item: item["name"].lower())
    return options, allowed_ids


def _filter_company_ids(company_ids: list[int], allowed_company_ids: set[int]) -> list[int]:
    return [company_id for company_id in dict.fromkeys(company_ids) if company_id in allowed_company_ids]


async def _assignment_is_accessible(assignment_id: int, allowed_company_ids: set[int]) -> bool:
    from app.repositories import issues as issues_repo

    assignment = await issues_repo.get_assignment_by_id(assignment_id)
    company_id = _coerce_company_id((assignment or {}).get("company_id"))
    return company_id is not None and company_id in allowed_company_ids


def _format_issue_overview_for_template(overview: Any) -> dict[str, Any]:

    assignments = [
        {
            "assignment_id": assignment.assignment_id,
            "issue_id": assignment.issue_id,
            "company_id": assignment.company_id,
            "company_name": assignment.company_name,
            "status": assignment.status,
            "status_label": assignment.status_label,
            "updated_at_iso": assignment.updated_at_iso,
        }
        for assignment in overview.assignments
    ]
    return {
        "issue_id": overview.issue_id,
        "name": overview.name,
        "description": overview.description,
        "created_at_iso": overview.created_at_iso,
        "updated_at_iso": overview.updated_at_iso,
        "assignments": assignments,
        "assignment_count": len(assignments),
    }


async def admin_issue_tracker(
    request: Request,
    search: str | None = Query(default=None, max_length=255),
    status: str | None = Query(default=None, max_length=32),
    company_id: int | None = Query(default=None, alias="companyId"),
    issue_id: int | None = Query(default=None, alias="issueId"),
):
    from app.services import issues as issues_service

    current_user, redirect = await _main()._require_issue_tracker_access(request)
    if redirect:
        return redirect

    search_term = search.strip() if search else ""
    company_filter: int | None = None
    if company_id is not None:
        try:
            company_filter = int(company_id)
        except (TypeError, ValueError):
            company_filter = None

    status_filter: str | None = None
    if status:
        try:
            status_filter = issues_service.normalise_status(status)
        except ValueError:
            status_filter = None

    company_options, allowed_company_ids = await _accessible_company_options(current_user)
    if company_filter is not None and company_filter not in allowed_company_ids:
        company_filter = None

    overviews = await issues_service.build_issue_overview(
        search=search_term,
        status=status_filter,
        company_id=company_filter,
        company_ids=allowed_company_ids,
    )
    issues_payload = [_format_issue_overview_for_template(item) for item in overviews]

    editing_issue: dict[str, Any] | None = None
    edit_error: str | None = None
    if issue_id:
        lookup = await issues_service.get_issue_overview(issue_id, company_ids=allowed_company_ids)
        if lookup:
            editing_issue = _format_issue_overview_for_template(lookup)
        else:
            edit_error = "Selected issue could not be found."

    if editing_issue:
        assigned_company_ids = {
            assignment.get("company_id")
            for assignment in editing_issue.get("assignments", [])
            if assignment.get("company_id") is not None
        }
        available_companies = [
            option for option in company_options if option["id"] not in assigned_company_ids
        ]
        editing_issue["available_companies"] = available_companies

    issue_status_options = [
        {"value": value, "label": label} for value, label in issues_service.STATUS_OPTIONS
    ]

    extra = {
        "title": "Issue tracker",
        "issues": issues_payload,
        "issue_count": len(issues_payload),
        "issue_status_options": issue_status_options,
        "selected_status": status_filter,
        "selected_company_id": company_filter,
        "search_term": search_term,
        "company_options": company_options,
        "editing_issue": editing_issue,
        "error_message": edit_error,
    }

    response = await _main()._render_template("admin/issues.html", request, current_user, extra=extra)
    return response


async def admin_create_issue(request: Request):
    from app.core.logging import log_info
    from app.repositories import issues as issues_repo
    from app.services import issues as issues_service

    current_user, redirect = await _main()._require_issue_tracker_access(request)
    if redirect:
        return redirect

    form = await request.form()
    name = str(form.get("name", "")).strip()
    description = str(form.get("description", "")).strip() or None
    initial_status = str(form.get("initialStatus", issues_service.DEFAULT_STATUS)).strip()

    if not name:
        return flash_redirect("/admin/issues", 'Issue name is required.', "error")

    try:
        await issues_service.ensure_issue_name_available(name)
    except ValueError as exc:
        return flash_redirect("/admin/issues", str(exc), "error")

    user_id = _main()._get_current_user_id(current_user)
    try:
        issue_record = await issues_repo.create_issue(
            name=name,
            description=description,
            created_by=user_id,
        )
    except aiomysql.IntegrityError as exc:
        detail = "Issue name already exists." if exc.args and exc.args[0] == 1062 else "Unable to create issue."
        return flash_redirect("/admin/issues", detail, "error")

    issue_id = issue_record.get("issue_id")
    try:
        issue_id_int = int(issue_id) if issue_id is not None else None
    except (TypeError, ValueError):
        issue_id_int = None
    if issue_id_int is None:
        return flash_redirect("/admin/issues", 'Issue identifier missing.', "error")

    try:
        status_value = issues_service.normalise_status(initial_status)
    except ValueError:
        status_value = issues_service.DEFAULT_STATUS

    company_ids_raw = form.getlist("company_ids")
    selected_companies: list[int] = []
    for raw in company_ids_raw:
        company_id_value = _coerce_company_id(raw)
        if company_id_value is not None:
            selected_companies.append(company_id_value)
    _, allowed_company_ids = await _accessible_company_options(current_user)

    for company_id_value in _filter_company_ids(selected_companies, allowed_company_ids):
        await issues_repo.assign_issue_to_company(
            issue_id=issue_id_int,
            company_id=company_id_value,
            status=status_value,
            updated_by=user_id,
        )

    log_info(
        "Issue created via admin",
        issue_id=issue_id_int,
        name=name,
        created_by=user_id,
    )
    return flash_redirect("/admin/issues", 'Issue created.', "success")


async def admin_update_issue(issue_id: int, request: Request):
    from app.core.logging import log_info
    from app.repositories import issues as issues_repo
    from app.services import issues as issues_service

    current_user, redirect = await _main()._require_issue_tracker_access(request)
    if redirect:
        return redirect

    _, allowed_company_ids = await _accessible_company_options(current_user)
    issue = await issues_repo.get_issue_by_id(issue_id, company_ids=allowed_company_ids)
    if not issue or not issue.get("assignments"):
        return flash_redirect("/admin/issues", 'Issue not found.', "error")

    form = await request.form()
    name = str(form.get("name", "")).strip()
    description = str(form.get("description", "")).strip() or None
    new_company_status = str(form.get("newCompanyStatus", issues_service.DEFAULT_STATUS)).strip()

    if not name:
        return flash_redirect(f"/admin/issues?issueId={issue_id}", 'Issue name is required.', "error")

    updates: dict[str, Any] = {}
    if name != (issue.get("name") or ""):
        try:
            await issues_service.ensure_issue_name_available(name, exclude_issue_id=issue_id)
        except ValueError as exc:
            return flash_redirect(f"/admin/issues?issueId={issue_id}", str(exc), "error")
        updates["name"] = name

    if description != (issue.get("description") or None):
        updates["description"] = description

    if updates:
        updates["updated_by"] = _main()._get_current_user_id(current_user)
        try:
            await issues_repo.update_issue(issue_id, **updates)
        except aiomysql.IntegrityError as exc:
            detail = "Issue name already exists." if exc.args and exc.args[0] == 1062 else "Unable to update issue."
            return flash_redirect(f"/admin/issues?issueId={issue_id}", detail, "error")

    try:
        status_value = issues_service.normalise_status(new_company_status)
    except ValueError:
        status_value = issues_service.DEFAULT_STATUS

    new_company_ids_raw = form.getlist("newCompanyIds")
    selected_companies: list[int] = []
    for raw in new_company_ids_raw:
        company_id_value = _coerce_company_id(raw)
        if company_id_value is not None:
            selected_companies.append(company_id_value)

    for company_id_value in _filter_company_ids(selected_companies, allowed_company_ids):
        await issues_repo.assign_issue_to_company(
            issue_id=issue_id,
            company_id=company_id_value,
            status=status_value,
            updated_by=_main()._get_current_user_id(current_user),
        )

    log_info(
        "Issue updated via admin",
        issue_id=issue_id,
        updated_by=_main()._get_current_user_id(current_user),
    )
    url = f"/admin/issues?issueId={issue_id}"
    return flash_redirect(url, 'Issue updated.', "success")


async def admin_update_issue_assignment_status(
    issue_id: int,
    assignment_id: int,
    request: Request,
):
    from app.core.logging import log_info
    from app.repositories import issues as issues_repo
    from app.services import issues as issues_service

    current_user, redirect = await _main()._require_issue_tracker_access(request)
    if redirect:
        return redirect

    form = await request.form()
    status_value = str(form.get("status", "")).strip()
    return_url = str(form.get("returnUrl", "")).strip() or None

    try:
        normalised_status = issues_service.normalise_status(status_value)
    except ValueError:
        return flash_redirect(f"/admin/issues?issueId={issue_id}", 'Invalid status selection.', "error")

    _, allowed_company_ids = await _accessible_company_options(current_user)
    if not await _assignment_is_accessible(assignment_id, allowed_company_ids):
        return flash_redirect(f"/admin/issues?issueId={issue_id}", 'Assignment not found.', "error")

    try:
        await issues_repo.update_assignment_status(
            assignment_id,
            status=normalised_status,
            updated_by=_main()._get_current_user_id(current_user),
        )
    except ValueError:
        return flash_redirect(f"/admin/issues?issueId={issue_id}", 'Assignment not found.', "error")

    log_info(
        "Issue assignment status updated",
        issue_id=issue_id,
        assignment_id=assignment_id,
        status=normalised_status,
        updated_by=_main()._get_current_user_id(current_user),
    )

    destination = _main()._sanitize_local_redirect_target(
        return_url,
        fallback=f"/admin/issues?issueId={issue_id}",
        allowed_prefixes=("/admin/issues",),
    )
    response = RedirectResponse(url=destination, status_code=status.HTTP_303_SEE_OTHER)
    set_flash(response, "Status updated.", "success")
    return response


async def admin_delete_issue_assignment(
    issue_id: int,
    assignment_id: int,
    request: Request,
):
    from app.core.logging import log_info
    from app.repositories import issues as issues_repo

    current_user, redirect = await _main()._require_issue_tracker_access(request)
    if redirect:
        return redirect

    form = await request.form()
    return_url = str(form.get("returnUrl", "")).strip() or None

    _, allowed_company_ids = await _accessible_company_options(current_user)
    if not await _assignment_is_accessible(assignment_id, allowed_company_ids):
        return flash_redirect(f"/admin/issues?issueId={issue_id}", 'Assignment not found.', "error")

    await issues_repo.delete_assignment(assignment_id)
    log_info(
        "Issue assignment removed",
        issue_id=issue_id,
        assignment_id=assignment_id,
        removed_by=_main()._get_current_user_id(current_user),
    )

    destination = _main()._sanitize_local_redirect_target(
        return_url,
        fallback=f"/admin/issues?issueId={issue_id}",
        allowed_prefixes=("/admin/issues",),
    )
    response = RedirectResponse(url=destination, status_code=status.HTTP_303_SEE_OTHER)
    set_flash(response, "Assignment removed.", "success")
    return response
