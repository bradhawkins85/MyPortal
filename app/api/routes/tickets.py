from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.dependencies.auth import (
    get_current_session,
    require_helpdesk_technician,
)
from app.repositories import tickets as tickets_repo
from app.schemas.tickets import (
    TicketCreate,
    TicketDetail,
    TicketListResponse,
    TicketReply,
    TicketReplyCreate,
    TicketReplyResponse,
    TicketResponse,
    TicketUpdate,
    TicketWatcher,
    TicketWatcherUpdate,
)
from app.security.session import SessionData

router = APIRouter(prefix="/api/tickets", tags=["Tickets"])


async def _build_ticket_detail(ticket_id: int) -> TicketDetail:
    ticket = await tickets_repo.get_ticket(ticket_id)
    if not ticket:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ticket not found")
    replies = await tickets_repo.list_replies(ticket_id)
    watchers = await tickets_repo.list_watchers(ticket_id)
    return TicketDetail(
        **ticket,
        replies=[TicketReply(**reply) for reply in replies],
        watchers=[TicketWatcher(**watcher) for watcher in watchers],
    )


@router.get("/", response_model=TicketListResponse)
async def list_tickets(
    status_filter: str | None = Query(default=None, alias="status"),
    module_slug: str | None = Query(default=None),
    company_id: int | None = Query(default=None),
    assigned_user_id: int | None = Query(default=None),
    search: str | None = Query(default=None, min_length=1),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    current_user: dict = Depends(require_helpdesk_technician),
) -> TicketListResponse:
    tickets = await tickets_repo.list_tickets(
        status=status_filter,
        module_slug=module_slug,
        company_id=company_id,
        assigned_user_id=assigned_user_id,
        search=search,
        limit=limit,
        offset=offset,
    )
    total = await tickets_repo.count_tickets(
        status=status_filter,
        module_slug=module_slug,
        company_id=company_id,
        assigned_user_id=assigned_user_id,
        search=search,
    )
    return TicketListResponse(
        items=[TicketResponse(**ticket) for ticket in tickets],
        total=total,
    )


@router.post("/", response_model=TicketDetail, status_code=status.HTTP_201_CREATED)
async def create_ticket(
    payload: TicketCreate,
    session: SessionData = Depends(get_current_session),
    current_user: dict = Depends(require_helpdesk_technician),
) -> TicketDetail:
    ticket = await tickets_repo.create_ticket(
        subject=payload.subject,
        description=payload.description,
        requester_id=payload.requester_id or session.user_id,
        company_id=payload.company_id,
        assigned_user_id=payload.assigned_user_id,
        priority=payload.priority,
        status=payload.status,
        category=payload.category,
        module_slug=payload.module_slug,
        external_reference=payload.external_reference,
    )
    await tickets_repo.add_watcher(ticket["id"], session.user_id)
    return await _build_ticket_detail(ticket["id"])


@router.get("/{ticket_id}", response_model=TicketDetail)
async def get_ticket(
    ticket_id: int, current_user: dict = Depends(require_helpdesk_technician)
) -> TicketDetail:
    return await _build_ticket_detail(ticket_id)


@router.put("/{ticket_id}", response_model=TicketDetail)
async def update_ticket(
    ticket_id: int,
    payload: TicketUpdate,
    current_user: dict = Depends(require_helpdesk_technician),
) -> TicketDetail:
    existing = await tickets_repo.get_ticket(ticket_id)
    if not existing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ticket not found")
    fields = payload.model_dump(exclude_unset=True)
    if fields:
        await tickets_repo.update_ticket(ticket_id, **fields)
    return await _build_ticket_detail(ticket_id)


@router.delete("/{ticket_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_ticket(
    ticket_id: int, current_user: dict = Depends(require_helpdesk_technician)
) -> None:
    existing = await tickets_repo.get_ticket(ticket_id)
    if not existing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ticket not found")
    await tickets_repo.delete_ticket(ticket_id)


@router.post("/{ticket_id}/replies", response_model=TicketReplyResponse, status_code=status.HTTP_201_CREATED)
async def add_reply(
    ticket_id: int,
    payload: TicketReplyCreate,
    session: SessionData = Depends(get_current_session),
    current_user: dict = Depends(require_helpdesk_technician),
) -> TicketReplyResponse:
    ticket = await tickets_repo.get_ticket(ticket_id)
    if not ticket:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ticket not found")
    reply = await tickets_repo.create_reply(
        ticket_id=ticket_id,
        author_id=session.user_id,
        body=payload.body,
        is_internal=payload.is_internal,
    )
    ticket_response = TicketResponse(**ticket)
    return TicketReplyResponse(ticket=ticket_response, reply=TicketReply(**reply))


@router.put("/{ticket_id}/watchers", response_model=TicketDetail)
async def update_watchers(
    ticket_id: int,
    payload: TicketWatcherUpdate,
    current_user: dict = Depends(require_helpdesk_technician),
) -> TicketDetail:
    ticket = await tickets_repo.get_ticket(ticket_id)
    if not ticket:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ticket not found")
    await tickets_repo.replace_watchers(ticket_id, payload.user_ids)
    return await _build_ticket_detail(ticket_id)

