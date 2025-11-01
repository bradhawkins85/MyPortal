from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.dependencies.auth import (
    get_current_session,
    get_current_user,
    require_helpdesk_technician,
    require_super_admin,
)
from app.core.logging import log_error
from app.repositories import company_memberships as membership_repo
from app.repositories import staff as staff_repo
from app.repositories import tickets as tickets_repo
from app.schemas.tickets import (
    SyncroTicketImportRequest,
    SyncroTicketImportSummary,
    TicketCreate,
    TicketDashboardResponse,
    TicketDashboardRow,
    TicketDetail,
    TicketListResponse,
    TicketReply,
    TicketReplyCreate,
    TicketReplyTimeUpdate,
    TicketReplyResponse,
    TicketResponse,
    TicketSearchFilters,
    TicketStatusDefinitionModel,
    TicketStatusListResponse,
    TicketStatusUpdateRequest,
    TicketUpdate,
    TicketWatcher,
    TicketWatcherUpdate,
)
from app.security.session import SessionData
from app.services import ticket_importer, tickets as tickets_service
from app.services.sanitization import sanitize_rich_text

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
        return await membership_repo.user_has_permission(
            user_id_int, tickets_service.HELPDESK_PERMISSION_KEY
        )
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
    if not has_helpdesk_access:
        if requester_id != current_user_id_int:
            is_watcher = False
            if current_user_id_int is not None:
                is_watcher = await tickets_repo.is_ticket_watcher(ticket_id, current_user_id_int)
            if not is_watcher:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ticket not found")

    replies = await tickets_repo.list_replies(
        ticket_id, include_internal=has_helpdesk_access
    )
    ordered_replies = list(reversed(replies))
    watcher_records = []
    if has_helpdesk_access:
        watcher_records = await tickets_repo.list_watchers(ticket_id)
    sanitised_replies = []
    for reply in ordered_replies:
        sanitised = sanitize_rich_text(str(reply.get("body") or ""))
        minutes_value = reply.get("minutes_spent")
        minutes_spent = minutes_value if isinstance(minutes_value, int) and minutes_value >= 0 else None
        billable_flag = bool(reply.get("is_billable"))
        time_summary = tickets_service.format_reply_time_summary(minutes_spent, billable_flag)
        payload = {
            **reply,
            "body": sanitised.html,
            "minutes_spent": minutes_spent,
            "is_billable": billable_flag,
        }
        if time_summary:
            payload["time_summary"] = time_summary
        sanitised_replies.append(payload)

    return TicketDetail(
        **ticket,
        replies=[TicketReply(**reply) for reply in sanitised_replies],
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
    if not has_helpdesk_access:
        try:
            current_user_id = int(current_user["id"])
        except (TypeError, ValueError):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied") from None
        tickets = await tickets_repo.list_tickets_for_user(
            current_user_id,
            search=search,
            status=status_filter,
            limit=limit,
            offset=offset,
        )
        total = await tickets_repo.count_tickets_for_user(
            current_user_id,
            search=search,
            status=status_filter,
        )
    else:
        tickets = await tickets_repo.list_tickets(
            status=status_filter,
            module_slug=module_slug,
            company_id=company_id,
            assigned_user_id=assigned_user_id,
            search=search,
            limit=limit,
            offset=offset,
            requester_id=None,
        )
        total = await tickets_repo.count_tickets(
            status=status_filter,
            module_slug=module_slug,
            company_id=company_id,
            assigned_user_id=assigned_user_id,
            search=search,
            requester_id=None,
        )
    return TicketListResponse(
        items=[TicketResponse(**ticket) for ticket in tickets],
        total=total,
    )


@router.get("/dashboard", response_model=TicketDashboardResponse)
async def get_ticket_dashboard(
    status_filter: str | None = Query(default=None, alias="status"),
    module_slug: str | None = Query(default=None, alias="module"),
    company_id: int | None = Query(default=None, alias="companyId"),
    assigned_user_id: int | None = Query(default=None, alias="assignedUserId"),
    search: str | None = Query(default=None, min_length=1),
    limit: int = Query(default=200, ge=1, le=500),
    current_user: dict = Depends(require_helpdesk_technician),
) -> TicketDashboardResponse:
    state = await tickets_service.load_dashboard_state(
        status_filter=status_filter,
        module_filter=module_slug,
        company_id=company_id,
        assigned_user_id=assigned_user_id,
        search=search,
        limit=limit,
    )
    rows: list[TicketDashboardRow] = []
    for ticket in state.tickets:
        identifier = ticket.get("id")
        try:
            numeric_id = int(identifier)
        except (TypeError, ValueError):
            continue
        company_record = state.company_lookup.get(ticket.get("company_id"))
        assigned_record = state.user_lookup.get(ticket.get("assigned_user_id"))
        rows.append(
            TicketDashboardRow(
                id=numeric_id,
                subject=str(ticket.get("subject") or ""),
                status=str(ticket.get("status") or "open"),
                priority=str(ticket.get("priority") or "normal"),
                company_id=ticket.get("company_id"),
                company_name=(company_record or {}).get("name") if isinstance(company_record, dict) else None,
                assigned_user_id=ticket.get("assigned_user_id"),
                assigned_user_email=(assigned_record or {}).get("email")
                if isinstance(assigned_record, dict)
                else None,
                module_slug=ticket.get("module_slug"),
                requester_id=ticket.get("requester_id"),
                updated_at=ticket.get("updated_at"),
            )
        )
    filters = TicketSearchFilters(
        status=status_filter,
        module_slug=module_slug,
        company_id=company_id,
        assigned_user_id=assigned_user_id,
        search=search,
        limit=limit,
        offset=0,
    )
    return TicketDashboardResponse(
        items=rows,
        total=state.total,
        status_counts=dict(state.status_counts),
        filters=filters,
    )


@router.get("/statuses", response_model=TicketStatusListResponse)
async def list_ticket_statuses_endpoint(
    current_user: dict = Depends(require_helpdesk_technician),
) -> TicketStatusListResponse:
    definitions = await tickets_service.list_status_definitions()
    items = [
        TicketStatusDefinitionModel(
            tech_status=definition.tech_status,
            tech_label=definition.tech_label,
            public_status=definition.public_status,
        )
        for definition in definitions
    ]
    return TicketStatusListResponse(statuses=items)


@router.put("/statuses", response_model=TicketStatusListResponse)
async def replace_ticket_statuses_endpoint(
    payload: TicketStatusUpdateRequest,
    current_user: dict = Depends(require_super_admin),
) -> TicketStatusListResponse:
    try:
        definitions = await tickets_service.replace_ticket_statuses(payload.statuses)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    items = [
        TicketStatusDefinitionModel(
            tech_status=definition.tech_status,
            tech_label=definition.tech_label,
            public_status=definition.public_status,
        )
        for definition in definitions
    ]
    return TicketStatusListResponse(statuses=items)


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
    if has_helpdesk_access:
        if payload.status:
            try:
                status_value = await tickets_service.validate_status_choice(payload.status)
            except ValueError as exc:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=str(exc),
                ) from exc
        else:
            status_value = await tickets_service.resolve_status_or_default(None)
    else:
        status_value = await tickets_service.resolve_status_or_default("open")
    category = payload.category if has_helpdesk_access else None
    module_slug = payload.module_slug if has_helpdesk_access else None
    external_reference = payload.external_reference if has_helpdesk_access else None
    ticket = await tickets_service.create_ticket(
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
        trigger_automations=True,
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
    description_marker = object()
    description_value = fields.pop("description", description_marker)
    if "status" in fields and fields["status"] is not None:
        try:
            fields["status"] = await tickets_service.validate_status_choice(fields["status"])
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    if fields:
        await tickets_repo.update_ticket(ticket_id, **fields)
    if description_value is not description_marker:
        await tickets_service.update_ticket_description(ticket_id, description_value)
    try:
        await tickets_service.refresh_ticket_ai_summary(ticket_id)
    except RuntimeError:
        pass
    await tickets_service.refresh_ticket_ai_tags(ticket_id)
    await tickets_service.broadcast_ticket_event(action="updated", ticket_id=ticket_id)
    await tickets_service.emit_ticket_updated_event(
        ticket_id,
        actor_type="technician",
        actor=current_user,
    )
    return await _build_ticket_detail(ticket_id, current_user)


@router.delete("/{ticket_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_ticket(ticket_id: int, current_user: dict = Depends(require_super_admin)) -> None:
    existing = await tickets_repo.get_ticket(ticket_id)
    if not existing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ticket not found")
    await tickets_repo.delete_ticket(ticket_id)
    await tickets_service.broadcast_ticket_event(action="deleted", ticket_id=ticket_id)


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
    sanitised_body = sanitize_rich_text(payload.body)
    if not sanitised_body.has_rich_content:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Reply body cannot be empty.",
        )

    reply = await tickets_repo.create_reply(
        ticket_id=ticket_id,
        author_id=session.user_id,
        body=sanitised_body.html,
        is_internal=payload.is_internal if has_helpdesk_access else False,
        minutes_spent=payload.minutes_spent if has_helpdesk_access else None,
        is_billable=payload.is_billable if has_helpdesk_access else False,
    )
    try:
        await tickets_service.refresh_ticket_ai_summary(ticket_id)
    except RuntimeError:
        pass
    await tickets_service.refresh_ticket_ai_tags(ticket_id)
    await tickets_service.emit_ticket_updated_event(
        ticket_id,
        actor_type="technician" if has_helpdesk_access else "requester",
        actor=current_user,
    )
    updated_ticket = await tickets_repo.get_ticket(ticket_id)
    ticket_payload = updated_ticket or ticket
    ticket_response = TicketResponse(**ticket_payload)
    sanitised_reply_payload = sanitize_rich_text(str(reply.get("body") or ""))
    reply_payload = {**reply, "body": sanitised_reply_payload.html}
    minutes_value = reply_payload.get("minutes_spent")
    minutes_spent = minutes_value if isinstance(minutes_value, int) and minutes_value >= 0 else None
    billable_flag = bool(reply_payload.get("is_billable"))
    time_summary = tickets_service.format_reply_time_summary(minutes_spent, billable_flag)
    if time_summary:
        reply_payload["time_summary"] = time_summary
    await tickets_service.broadcast_ticket_event(action="reply", ticket_id=ticket_id)
    return TicketReplyResponse(ticket=ticket_response, reply=TicketReply(**reply_payload))


@router.patch("/{ticket_id}/replies/{reply_id}", response_model=TicketReplyResponse)
async def update_reply_time_entry(
    ticket_id: int,
    reply_id: int,
    payload: TicketReplyTimeUpdate,
    current_user: dict = Depends(require_helpdesk_technician),
) -> TicketReplyResponse:
    ticket = await tickets_repo.get_ticket(ticket_id)
    if not ticket:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ticket not found")
    reply = await tickets_repo.get_reply_by_id(reply_id)
    if not reply or reply.get("ticket_id") != ticket_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Reply not found")

    fields_set = payload.model_fields_set
    update_kwargs: dict[str, Any] = {}
    if "minutes_spent" in fields_set:
        update_kwargs["minutes_spent"] = payload.minutes_spent
    if "is_billable" in fields_set and payload.is_billable is not None:
        update_kwargs["is_billable"] = payload.is_billable
    if not update_kwargs:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Provide minutes_spent or is_billable to update the reply.",
        )

    updated = await tickets_repo.update_reply(reply_id, **update_kwargs)
    if not updated:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Reply not found")

    minutes_value = updated.get("minutes_spent")
    minutes_spent = minutes_value if isinstance(minutes_value, int) and minutes_value >= 0 else None
    billable_flag = bool(updated.get("is_billable"))
    time_summary = tickets_service.format_reply_time_summary(minutes_spent, billable_flag)
    reply_payload = {
        **updated,
        "minutes_spent": minutes_spent,
        "is_billable": billable_flag,
    }
    if time_summary:
        reply_payload["time_summary"] = time_summary

    await tickets_service.emit_ticket_updated_event(
        ticket_id,
        actor_type="technician",
        actor=current_user,
    )

    ticket_payload = TicketResponse(**ticket)
    return TicketReplyResponse(ticket=ticket_payload, reply=TicketReply(**reply_payload))


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
    await tickets_service.emit_ticket_updated_event(
        ticket_id,
        actor_type="technician",
        actor=current_user,
    )
    return await _build_ticket_detail(ticket_id, current_user)

