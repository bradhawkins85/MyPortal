"""Issue tracker handlers for the ``issue_tracker`` feature pack."""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

import aiomysql
from fastapi import Query, Request, status
from fastapi.responses import RedirectResponse


def _main():
    from app import main as main_module

    return main_module


def _format_issue_overview_for_template(overview: Any) -> dict[str, Any]:
    from app.services import issues as issues_service

    assignments = []
    for assignment in overview.assignments:
        assignments.append(
            {
                "assignment_id": assignment.assignment_id,
                "issue_id": assignment.issue_id,
                "company_id": assignment.company_id,
                "company_name": assignment.company_name,
                "status": assignment.status,
                "status_label": assignment.status_label,
                "updated_at_iso": assignment.updated_at_iso,
            }
        )
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
    from app.repositories import companies as company_repo
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

    overviews = await issues_service.build_issue_overview(
        search=search_term,
        status=status_filter,
        company_id=company_filter,
    )
    issues_payload = [_format_issue_overview_for_template(item) for item in overviews]

    editing_issue: dict[str, Any] | None = None
    edit_error: str | None = None
    if issue_id:
        lookup = await issues_service.get_issue_overview(issue_id)
        if lookup:
            editing_issue = _format_issue_overview_for_template(lookup)
        else:
            edit_error = "Selected issue could not be found."

    companies = await company_repo.list_companies()
    company_options: list[dict[str, Any]] = []
    for record in companies:
        raw_id = record.get("id")
        name = (record.get("name") or "").strip()
        try:
            option_id = int(raw_id)
        except (TypeError, ValueError):
            continue
        company_options.append(
            {
                "id": option_id,
                "name": name or f"Company #{option_id}",
            }
        )
    company_options.sort(key=lambda item: item["name"].lower())

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
    success_message = request.query_params.get("success")
    error_message = request.query_params.get("error") or edit_error

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
        "success_message": success_message,
        "error_message": error_message,
    }

    response = await _main()._render_template("admin/issues.html", request, current_user, extra=extra)
    return response


async def admin_create_issue(request: Request):
    from app.core.logging import log_info
    from app.repositories import companies as company_repo
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
        url = f"/admin/issues?error={quote('Issue name is required.')}"
        return RedirectResponse(url=url, status_code=status.HTTP_303_SEE_OTHER)

    try:
        await issues_service.ensure_issue_name_available(name)
    except ValueError as exc:
        url = f"/admin/issues?error={quote(str(exc))}"
        return RedirectResponse(url=url, status_code=status.HTTP_303_SEE_OTHER)

    user_id = _main()._get_current_user_id(current_user)
    try:
        issue_record = await issues_repo.create_issue(
            name=name,
            description=description,
            created_by=user_id,
        )
    except aiomysql.IntegrityError as exc:
        detail = "Issue name already exists." if exc.args and exc.args[0] == 1062 else "Unable to create issue."
        url = f"/admin/issues?error={quote(detail)}"
        return RedirectResponse(url=url, status_code=status.HTTP_303_SEE_OTHER)

    issue_id = issue_record.get("issue_id")
    try:
        issue_id_int = int(issue_id) if issue_id is not None else None
    except (TypeError, ValueError):
        issue_id_int = None
    if issue_id_int is None:
        url = f"/admin/issues?error={quote('Issue identifier missing.')}"
        return RedirectResponse(url=url, status_code=status.HTTP_303_SEE_OTHER)

    try:
        status_value = issues_service.normalise_status(initial_status)
    except ValueError:
        status_value = issues_service.DEFAULT_STATUS

    company_ids_raw = form.getlist("company_ids")
    selected_companies: list[int] = []
    for raw in company_ids_raw:
        try:
            selected_companies.append(int(raw))
        except (TypeError, ValueError):
            continue

    for company_id_value in selected_companies:
        company = await company_repo.get_company_by_id(company_id_value)
        if not company:
            continue
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
    url = f"/admin/issues?success={quote('Issue created.')}"
    return RedirectResponse(url=url, status_code=status.HTTP_303_SEE_OTHER)


async def admin_update_issue(issue_id: int, request: Request):
    from app.core.logging import log_info
    from app.repositories import companies as company_repo
    from app.repositories import issues as issues_repo
    from app.services import issues as issues_service

    current_user, redirect = await _main()._require_issue_tracker_access(request)
    if redirect:
        return redirect

    issue = await issues_repo.get_issue_by_id(issue_id)
    if not issue:
        url = f"/admin/issues?error={quote('Issue not found.')}"
        return RedirectResponse(url=url, status_code=status.HTTP_303_SEE_OTHER)

    form = await request.form()
    name = str(form.get("name", "")).strip()
    description = str(form.get("description", "")).strip() or None
    new_company_status = str(form.get("newCompanyStatus", issues_service.DEFAULT_STATUS)).strip()

    if not name:
        url = f"/admin/issues?issueId={issue_id}&error={quote('Issue name is required.')}"
        return RedirectResponse(url=url, status_code=status.HTTP_303_SEE_OTHER)

    updates: dict[str, Any] = {}
    if name != (issue.get("name") or ""):
        try:
            await issues_service.ensure_issue_name_available(name, exclude_issue_id=issue_id)
        except ValueError as exc:
            url = f"/admin/issues?issueId={issue_id}&error={quote(str(exc))}"
            return RedirectResponse(url=url, status_code=status.HTTP_303_SEE_OTHER)
        updates["name"] = name

    if description != (issue.get("description") or None):
        updates["description"] = description

    if updates:
        updates["updated_by"] = _main()._get_current_user_id(current_user)
        try:
            await issues_repo.update_issue(issue_id, **updates)
        except aiomysql.IntegrityError as exc:
            detail = "Issue name already exists." if exc.args and exc.args[0] == 1062 else "Unable to update issue."
            url = f"/admin/issues?issueId={issue_id}&error={quote(detail)}"
            return RedirectResponse(url=url, status_code=status.HTTP_303_SEE_OTHER)

    try:
        status_value = issues_service.normalise_status(new_company_status)
    except ValueError:
        status_value = issues_service.DEFAULT_STATUS

    new_company_ids_raw = form.getlist("newCompanyIds")
    selected_companies: list[int] = []
    for raw in new_company_ids_raw:
        try:
            selected_companies.append(int(raw))
        except (TypeError, ValueError):
            continue

    for company_id_value in selected_companies:
        company = await company_repo.get_company_by_id(company_id_value)
        if not company:
            continue
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
    url = f"/admin/issues?issueId={issue_id}&success={quote('Issue updated.')}"
    return RedirectResponse(url=url, status_code=status.HTTP_303_SEE_OTHER)


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
        url = f"/admin/issues?issueId={issue_id}&error={quote('Invalid status selection.')}"
        return RedirectResponse(url=url, status_code=status.HTTP_303_SEE_OTHER)

    try:
        await issues_repo.update_assignment_status(
            assignment_id,
            status=normalised_status,
            updated_by=_main()._get_current_user_id(current_user),
        )
    except ValueError:
        url = f"/admin/issues?issueId={issue_id}&error={quote('Assignment not found.')}"
        return RedirectResponse(url=url, status_code=status.HTTP_303_SEE_OTHER)

    log_info(
        "Issue assignment status updated",
        issue_id=issue_id,
        assignment_id=assignment_id,
        status=normalised_status,
        updated_by=_main()._get_current_user_id(current_user),
    )

    destination = _main()._sanitize_local_redirect_target(
        return_url,
        fallback=f"/admin/issues?issueId={issue_id}&success={quote('Status updated.')}",
        allowed_prefixes=("/admin/issues",),
    )
    if "success=" not in destination:
        separator = "&" if "?" in destination else "?"
        destination = f"{destination}{separator}success={quote('Status updated.')}"
    return RedirectResponse(url=destination, status_code=status.HTTP_303_SEE_OTHER)


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
    separator = "&" if "?" in destination else "?"
    destination = f"{destination}{separator}success={quote('Assignment removed.')}"
    return RedirectResponse(url=destination, status_code=status.HTTP_303_SEE_OTHER)
