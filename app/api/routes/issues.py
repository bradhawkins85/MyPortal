from __future__ import annotations

from typing import Any, Mapping

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from app.api.dependencies.auth import require_issue_tracker_access
from app.api.dependencies.database import require_database
from app.core.logging import log_error, log_info
from app.repositories import companies as company_repo
from app.repositories import issues as issues_repo
from app.schemas.issues import (
    IssueAssignmentResponse,
    IssueCreate,
    IssueListResponse,
    IssueResponse,
    IssueStatusResponse,
    IssueStatusUpdate,
    IssueUpdate,
)
from app.services import issues as issues_service

router = APIRouter(prefix="/api/issues", tags=["Issues"])


def _build_issue_response(overview: issues_service.IssueOverview) -> IssueResponse:
    assignments = [
        IssueAssignmentResponse(
            assignment_id=assignment.assignment_id,
            issue_id=assignment.issue_id,
            company_id=assignment.company_id,
            company_name=assignment.company_name,
            status=assignment.status,
            status_label=assignment.status_label,
            updated_at=assignment.updated_at,
            updated_at_iso=assignment.updated_at_iso,
        )
        for assignment in overview.assignments
    ]
    return IssueResponse(
        name=overview.name,
        description=overview.description,
        created_at=overview.created_at,
        created_at_iso=overview.created_at_iso,
        updated_at=overview.updated_at,
        updated_at_iso=overview.updated_at_iso,
        assignments=assignments,
    )


async def _resolve_company_filter(
    company_id: int | None,
    company_name: str | None,
) -> int | None:
    if company_id is not None:
        return company_id
    if company_name:
        company = await company_repo.get_company_by_name(company_name)
        if not company:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Company not found")
        return int(company["id"])
    return None


def _extract_user_id(user: Mapping[str, Any] | None) -> int | None:
    if not user:
        return None
    try:
        return int(user.get("id"))  # type: ignore[arg-type]
    except (TypeError, ValueError, AttributeError):
        return None


@router.get(
    "/",
    response_model=IssueListResponse,
    summary="List issues",
    response_description="Issues and company assignments visible to technicians.",
)
async def list_issues(
    request: Request,
    search: str | None = Query(default=None, max_length=255),
    status_filter: str | None = Query(default=None, alias="status", max_length=32),
    company_id: int | None = Query(default=None, ge=1, alias="companyId"),
    company_name: str | None = Query(default=None, alias="companyName", max_length=255),
    _: None = Depends(require_database),
    current_user: dict[str, Any] = Depends(require_issue_tracker_access),
) -> IssueListResponse:
    del current_user  # Access control handled by dependency
    resolved_company_id = company_id
    if resolved_company_id is None:
        raw_company_id = request.query_params.get("company_id")
        if raw_company_id:
            try:
                candidate = int(raw_company_id)
                if candidate >= 1:
                    resolved_company_id = candidate
            except ValueError as exc:
                raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid company filter") from exc
    resolved_company_id = await _resolve_company_filter(resolved_company_id, company_name)
    try:
        status_value = issues_service.normalise_status(status_filter) if status_filter else None
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid status filter") from exc

    if search:
        search_value = search.strip()
        search_value = search_value.lower() if search_value else None
    else:
        search_value = None

    overviews = await issues_service.build_issue_overview(
        search=search_value,
        status=status_value,
        company_id=resolved_company_id,
    )
    items = [_build_issue_response(overview) for overview in overviews]
    return IssueListResponse(items=items, total=len(items))


@router.post(
    "/",
    response_model=IssueResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create an issue",
    response_description="Issue created with optional company assignments.",
)
async def create_issue(
    payload: IssueCreate,
    _: None = Depends(require_database),
    current_user: dict[str, Any] = Depends(require_issue_tracker_access),
) -> IssueResponse:
    user_id = _extract_user_id(current_user)
    try:
        await issues_service.ensure_issue_name_available(payload.name)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    try:
        issue = await issues_repo.create_issue(
            name=payload.name,
            description=payload.description,
            created_by=user_id,
        )
    except Exception as exc:  # pragma: no cover - integrity errors tested via FastAPI
        log_error("Failed to create issue", error=str(exc))
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Unable to create issue") from exc

    try:
        issue_id = int(issue.get("issue_id"))
    except (TypeError, ValueError):
        issue_id = None
    if issue_id is None:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Issue identifier missing")

    for assignment in payload.companies:
        company = await company_repo.get_company_by_name(assignment.company_name)
        if not company:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Company not found")
        status_value = issues_service.normalise_status(assignment.status)
        await issues_repo.assign_issue_to_company(
            issue_id=issue_id,
            company_id=int(company["id"]),
            status=status_value,
            updated_by=user_id,
        )

    overview = await issues_service.get_issue_overview(issue_id)
    if not overview:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Issue lookup failed")

    log_info(
        "Issue created",
        issue_id=issue_id,
        name=payload.name,
        created_by=user_id,
    )
    return _build_issue_response(overview)


@router.put(
    "/status",
    response_model=IssueStatusResponse,
    summary="Update issue status for a company",
    response_description="Status assignment updated for the specified company.",
)
async def update_issue_status(
    payload: IssueStatusUpdate,
    _: None = Depends(require_database),
    current_user: dict[str, Any] = Depends(require_issue_tracker_access),
) -> IssueStatusResponse:
    user_id = _extract_user_id(current_user)
    try:
        assignment = await issues_service.upsert_issue_status_by_name(
            issue_name=payload.issue_name,
            company_name=payload.company_name,
            status=payload.status,
            updated_by=user_id,
        )
    except ValueError as exc:
        message = str(exc)
        if "not found" in message.lower():
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=message) from exc
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=message) from exc

    log_info(
        "Issue status updated",
        issue_id=assignment.issue_id,
        company_id=assignment.company_id,
        status=assignment.status,
        updated_by=user_id,
    )
    return IssueStatusResponse(
        issue_name=payload.issue_name,
        company_name=assignment.company_name,
        status=assignment.status,
        status_label=assignment.status_label,
        updated_at=assignment.updated_at,
        updated_at_iso=assignment.updated_at_iso,
    )


@router.put(
    "/{issue_name}",
    response_model=IssueResponse,
    summary="Update an issue",
    response_description="Issue metadata and company assignments updated.",
)
async def update_issue(
    issue_name: str,
    payload: IssueUpdate,
    _: None = Depends(require_database),
    current_user: dict[str, Any] = Depends(require_issue_tracker_access),
) -> IssueResponse:
    issue = await issues_repo.get_issue_by_name(issue_name)
    if not issue or issue.get("issue_id") is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Issue not found")
    issue_id = int(issue["issue_id"])
    user_id = _extract_user_id(current_user)

    updates: dict[str, Any] = {}
    if payload.description is not None:
        updates["description"] = payload.description
    if payload.new_name:
        try:
            await issues_service.ensure_issue_name_available(payload.new_name, exclude_issue_id=issue_id)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        updates["name"] = payload.new_name

    if updates:
        updates["updated_by"] = user_id
        await issues_repo.update_issue(issue_id, **updates)

    for assignment in payload.add_companies:
        company = await company_repo.get_company_by_name(assignment.company_name)
        if not company:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Company '{assignment.company_name}' not found")
        status_value = issues_service.normalise_status(assignment.status)
        await issues_repo.assign_issue_to_company(
            issue_id=issue_id,
            company_id=int(company["id"]),
            status=status_value,
            updated_by=user_id,
        )

    overview = await issues_service.get_issue_overview(issue_id)
    if not overview:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Issue lookup failed")

    log_info(
        "Issue updated",
        issue_id=issue_id,
        name=overview.name,
        updated_by=user_id,
    )
    return _build_issue_response(overview)
