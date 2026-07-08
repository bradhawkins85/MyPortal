from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.dependencies.auth import require_super_admin
from app.repositories import email_blocklist as email_blocklist_repo
from app.schemas.email_blocklist import EmailBlocklistCreate, EmailBlocklistEntry, EmailBlocklistListResponse

router = APIRouter(prefix="/api/tickets/email-blocklist", tags=["Tickets"])


@router.get("", response_model=EmailBlocklistListResponse, summary="List blocked email addresses")
async def list_email_blocklist(
    search: str | None = Query(default=None, description="Search email, reason, or SMTP2Go event type."),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    sort: str = Query(default="created_at", pattern="^(email|created_at|updated_at|source|last_event_type)$"),
    direction: str = Query(default="desc", pattern="^(asc|desc)$"),
    _: dict = Depends(require_super_admin),
) -> EmailBlocklistListResponse:
    items = await email_blocklist_repo.list_entries(search=search, limit=limit, offset=offset, sort=sort, direction=direction)
    total = await email_blocklist_repo.count_entries(search=search)
    return EmailBlocklistListResponse(items=[EmailBlocklistEntry(**item) for item in items], total=total)


@router.post("", response_model=EmailBlocklistEntry, status_code=status.HTTP_201_CREATED, summary="Add a blocked email address")
async def create_email_blocklist_entry(payload: EmailBlocklistCreate, current_user: dict = Depends(require_super_admin)) -> EmailBlocklistEntry:
    try:
        row = await email_blocklist_repo.upsert_entry(
            email=payload.email,
            reason=payload.reason,
            source="manual",
            created_by_user_id=current_user.get("id"),
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    return EmailBlocklistEntry(**row)


@router.delete("/{entry_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Remove a blocked email address")
async def delete_email_blocklist_entry(entry_id: int, _: dict = Depends(require_super_admin)) -> None:
    deleted = await email_blocklist_repo.delete_entry(entry_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Email blocklist entry not found")
