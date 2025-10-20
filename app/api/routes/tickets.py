from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.dependencies.auth import (
    get_current_session,
    get_current_user,
    require_helpdesk_technician,
    require_super_admin,
)
from app.repositories import company_memberships as membership_repo
from app.repositories import staff as staff_repo
from app.repositories import tickets as tickets_repo
from app.schemas.tickets import (
    SyncroTicketImportRequest,
    SyncroTicketImportSummary,
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
from app.services import ticket_importer, tickets as tickets_service

HELPDESK_PERMISSION_KEY = "helpdesk.technician"

router = APIRouter(prefix="/api/tickets", tags=["Tickets"])


async def _has_helpdesk_permission(current_user: dict) -> bool:
    if current_user.get("is_super_admin"):
        return True
    user_id = current_user.get("id")
    try:
        user_id_int = int(user_id)
    except (TypeError, ValueError):
        return False
    try:
        return await membership_repo.user_has_permission(user_id_int, HELPDESK_PERMISSION_KEY)
    except RuntimeError:
        return False


async def _build_ticket_detail(ticket_id: int, current_user: dict) -> TicketDetail:
    ticket = await tickets_repo.get_ticket(ticket_id)
    if not ticket:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ticket not found")
    has_helpdesk_access = await _has_helpdesk_permission(current_user)
    requester_id = ticket.get("requester_id")
    current_user_id = current_user.get("id")
    try:
        current_user_id_int = int(current_user_id)
    except (TypeError, ValueError):
        current_user_id_int = None
    if not has_helpdesk_access and requester_id != current_user_id_int:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ticket not found")

    replies = await tickets_repo.list_replies(
        ticket_id, include_internal=has_helpdesk_access
    )
    watcher_records = []
    if has_helpdesk_access:
        watcher_records = await tickets_repo.list_watchers(ticket_id)
    return TicketDetail(
        **ticket,
        replies=[TicketReply(**reply) for reply in replies],
        watchers=[TicketWatcher(**watcher) for watcher in watcher_records],
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
    current_user: dict = Depends(get_current_user),
) -> TicketListResponse:
    has_helpdesk_access = await _has_helpdesk_permission(current_user)
    requester_id: int | None = None
    if not has_helpdesk_access:
        try:
            requester_id = int(current_user["id"])
        except (TypeError, ValueError):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied") from None
        company_id = None
        assigned_user_id = None
        module_slug = None
    tickets = await tickets_repo.list_tickets(
        status=status_filter,
        module_slug=module_slug,
        company_id=company_id,
        assigned_user_id=assigned_user_id,
        search=search,
        limit=limit,
        offset=offset,
        requester_id=requester_id,
    )
    total = await tickets_repo.count_tickets(
        status=status_filter,
        module_slug=module_slug,
        company_id=company_id,
        assigned_user_id=assigned_user_id,
        search=search,
        requester_id=requester_id,
    )
    return TicketListResponse(
        items=[TicketResponse(**ticket) for ticket in tickets],
        total=total,
    )


@router.post("/", response_model=TicketDetail, status_code=status.HTTP_201_CREATED)
async def create_ticket(
    payload: TicketCreate,
    session: SessionData = Depends(get_current_session),
    current_user: dict = Depends(get_current_user),
) -> TicketDetail:
    has_helpdesk_access = await _has_helpdesk_permission(current_user)
    requester_id = session.user_id
    company_id = payload.company_id if has_helpdesk_access else current_user.get("company_id")
    if has_helpdesk_access and payload.requester_id is not None:
        if company_id is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Link the ticket to a company before selecting a requester.",
            )
        allowed_requesters = await staff_repo.list_enabled_staff_users(company_id)
        allowed_ids = {
            int(option.get("id"))
            for option in allowed_requesters
            if option.get("id") is not None
        }
        if payload.requester_id not in allowed_ids:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Requester must be an enabled staff member for the linked company.",
            )
        requester_id = payload.requester_id
    assigned_user_id = payload.assigned_user_id if has_helpdesk_access else None
    priority = payload.priority if has_helpdesk_access else "normal"
    status_value = payload.status if has_helpdesk_access else "open"
    category = payload.category if has_helpdesk_access else None
    module_slug = payload.module_slug if has_helpdesk_access else None
    external_reference = payload.external_reference if has_helpdesk_access else None
    ticket = await tickets_repo.create_ticket(
        subject=payload.subject,
        description=payload.description,
        requester_id=requester_id,
        company_id=company_id,
        assigned_user_id=assigned_user_id,
        priority=priority,
        status=status_value,
        category=category,
        module_slug=module_slug,
        external_reference=external_reference,
    )
    await tickets_repo.add_watcher(ticket["id"], session.user_id)
    try:
        await tickets_service.refresh_ticket_ai_summary(ticket["id"])
    except RuntimeError:
        pass
    await tickets_service.refresh_ticket_ai_tags(ticket["id"])
    return await _build_ticket_detail(ticket["id"], current_user)


@router.get("/{ticket_id}", response_model=TicketDetail)
async def get_ticket(ticket_id: int, current_user: dict = Depends(get_current_user)) -> TicketDetail:
    return await _build_ticket_detail(ticket_id, current_user)


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
    try:
        await tickets_service.refresh_ticket_ai_summary(ticket_id)
    except RuntimeError:
        pass
    await tickets_service.refresh_ticket_ai_tags(ticket_id)
    return await _build_ticket_detail(ticket_id, current_user)


@router.delete("/{ticket_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_ticket(ticket_id: int, current_user: dict = Depends(require_super_admin)) -> None:
    existing = await tickets_repo.get_ticket(ticket_id)
    if not existing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ticket not found")
    await tickets_repo.delete_ticket(ticket_id)


@router.post(
    "/import/syncro",
    response_model=SyncroTicketImportSummary,
    status_code=status.HTTP_202_ACCEPTED,
)
async def import_syncro_tickets_endpoint(
    payload: SyncroTicketImportRequest,
    current_user: dict = Depends(require_super_admin),
) -> SyncroTicketImportSummary:
    try:
        summary = await ticket_importer.import_from_request(
            mode=payload.mode.value,
            ticket_id=payload.ticket_id,
            start_id=payload.start_id,
            end_id=payload.end_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return SyncroTicketImportSummary(**summary.as_dict())


@router.post("/{ticket_id}/replies", response_model=TicketReplyResponse, status_code=status.HTTP_201_CREATED)
async def add_reply(
    ticket_id: int,
    payload: TicketReplyCreate,
    session: SessionData = Depends(get_current_session),
    current_user: dict = Depends(get_current_user),
) -> TicketReplyResponse:
    ticket = await tickets_repo.get_ticket(ticket_id)
    if not ticket:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ticket not found")
    has_helpdesk_access = await _has_helpdesk_permission(current_user)
    if not has_helpdesk_access and ticket.get("requester_id") != session.user_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ticket not found")
    reply = await tickets_repo.create_reply(
        ticket_id=ticket_id,
        author_id=session.user_id,
        body=payload.body,
        is_internal=payload.is_internal if has_helpdesk_access else False,
    )
    try:
        await tickets_service.refresh_ticket_ai_summary(ticket_id)
    except RuntimeError:
        pass
    await tickets_service.refresh_ticket_ai_tags(ticket_id)
    updated_ticket = await tickets_repo.get_ticket(ticket_id)
    ticket_payload = updated_ticket or ticket
    ticket_response = TicketResponse(**ticket_payload)
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
    return await _build_ticket_detail(ticket_id, current_user)

