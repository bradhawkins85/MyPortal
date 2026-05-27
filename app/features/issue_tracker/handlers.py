"""Issue tracker handlers for the ``issue_tracker`` feature pack."""

from __future__ import annotations

from fastapi import Query, Request


def _main():
    from app import main as main_module

    return main_module


async def admin_issue_tracker(
    request: Request,
    search: str | None = Query(default=None, max_length=255),
    status: str | None = Query(default=None, max_length=32),
    company_id: int | None = Query(default=None, alias="companyId"),
    issue_id: int | None = Query(default=None, alias="issueId"),
):
    return await _main().admin_issue_tracker(
        request=request,
        search=search,
        status=status,
        company_id=company_id,
        issue_id=issue_id,
    )


async def admin_create_issue(request: Request):
    return await _main().admin_create_issue(request=request)


async def admin_update_issue(issue_id: int, request: Request):
    return await _main().admin_update_issue(issue_id=issue_id, request=request)


async def admin_update_issue_assignment_status(
    issue_id: int, assignment_id: int, request: Request
):
    return await _main().admin_update_issue_assignment_status(
        issue_id=issue_id,
        assignment_id=assignment_id,
        request=request,
    )


async def admin_delete_issue_assignment(
    issue_id: int, assignment_id: int, request: Request
):
    return await _main().admin_delete_issue_assignment(
        issue_id=issue_id,
        assignment_id=assignment_id,
        request=request,
    )

