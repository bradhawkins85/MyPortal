from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse

from app.api.dependencies.auth import (
    get_current_session,
    get_current_user,
    get_optional_user,
    require_helpdesk_technician,
    require_super_admin,
)
from app.api.dependencies.api_keys import get_optional_api_key
from app.core.logging import log_error
from app.repositories import company_memberships as membership_repo
from app.repositories import staff as staff_repo
from app.repositories import ticket_attachments as attachments_repo
from app.repositories import ticket_tasks as ticket_tasks_repo
from app.repositories import ticket_views as ticket_views_repo
from app.repositories import tickets as tickets_repo
from app.schemas.tickets import (
    LabourTypeCreateRequest,
    LabourTypeListResponse,
    LabourTypeModel,
    LabourTypeUpdateRequest,
    SyncroTicketImportRequest,
    SyncroTicketImportSummary,
    TicketAttachment,
    TicketAttachmentCreate,
    TicketAttachmentListResponse,
    TicketAttachmentUpdate,
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
    TicketTask,
    TicketTaskCreate,
    TicketTaskListResponse,
    TicketTaskUpdate,
    TicketUpdate,
    TicketViewCreate,
    TicketViewListResponse,
    TicketViewModel,
    TicketViewUpdate,
    TicketWatcher,
    TicketWatcherUpdate,
    TicketSplitRequest,
    TicketSplitResponse,
    TicketMergeRequest,
    TicketMergeResponse,
)
from app.security.session import SessionData
from app.services import labour_types as labour_types_service
from app.services import ticket_attachments as attachments_service
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


async def _resolve_ticket_actor(
    optional_user: dict | None = Depends(get_optional_user),
    api_key_record: dict | None = Depends(get_optional_api_key),
) -> dict[str, Any]:
    if api_key_record:
        return {"user": None, "api_key": api_key_record}
    if optional_user:
        return {"user": optional_user, "api_key": None}
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")


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

    # Get attachments
    attachment_records = []
    if has_helpdesk_access:
        # Helpdesk technicians can see all attachments
        attachment_records = await attachments_repo.list_attachments(ticket_id)
    else:
        # Non-technicians can only see open and closed attachments (not restricted)
        all_attachments = await attachments_repo.list_attachments(ticket_id)
        attachment_records = [
            att for att in all_attachments
            if att.get("access_level") in ("open", "closed")
        ]

    return TicketDetail(
        **ticket,
        replies=[TicketReply(**reply) for reply in sanitised_replies],
        watchers=[TicketWatcher(**watcher) for watcher in watcher_records],
        attachments=[TicketAttachment(**attachment) for attachment in attachment_records],
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
    actor: dict = Depends(_resolve_ticket_actor),
) -> TicketListResponse:
    current_user: dict | None = actor.get("user")
    api_key_record = actor.get("api_key")

    has_helpdesk_access = bool(api_key_record)
    if current_user:
        has_helpdesk_access = has_helpdesk_access or await _has_helpdesk_permission(current_user)

    if has_helpdesk_access:
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
    elif current_user:
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
    else:  # pragma: no cover - defensive guard
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")
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
            is_default=definition.is_default,
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
            is_default=definition.is_default,
        )
        for definition in definitions
    ]
    return TicketStatusListResponse(statuses=items)


@router.get("/views", response_model=TicketViewListResponse)
async def list_ticket_views(
    session: SessionData = Depends(get_current_session),
) -> TicketViewListResponse:
    """List all saved ticket views for the current user"""
    views = await ticket_views_repo.list_views_for_user(session.user_id)
    return TicketViewListResponse(items=[TicketViewModel(**view) for view in views])


@router.post("/views", response_model=TicketViewModel, status_code=status.HTTP_201_CREATED)
async def create_ticket_view(
    payload: TicketViewCreate,
    session: SessionData = Depends(get_current_session),
) -> TicketViewModel:
    """Create a new saved ticket view"""
    filters_dict = payload.filters.model_dump() if payload.filters else None
    view = await ticket_views_repo.create_view(
        user_id=session.user_id,
        name=payload.name,
        description=payload.description,
        filters=filters_dict,
        grouping_field=payload.grouping_field,
        sort_field=payload.sort_field,
        sort_direction=payload.sort_direction,
        is_default=payload.is_default,
    )
    return TicketViewModel(**view)


@router.get("/views/{view_id}", response_model=TicketViewModel)
async def get_ticket_view(
    view_id: int,
    session: SessionData = Depends(get_current_session),
) -> TicketViewModel:
    """Get a specific saved ticket view"""
    view = await ticket_views_repo.get_view(view_id, session.user_id)
    if not view:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="View not found")
    return TicketViewModel(**view)


@router.put("/views/{view_id}", response_model=TicketViewModel)
async def update_ticket_view(
    view_id: int,
    payload: TicketViewUpdate,
    session: SessionData = Depends(get_current_session),
) -> TicketViewModel:
    """Update a saved ticket view"""
    update_data = payload.model_dump(exclude_unset=True)
    
    view = await ticket_views_repo.update_view(view_id, session.user_id, **update_data)
    if not view:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="View not found")
    return TicketViewModel(**view)


@router.delete("/views/{view_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_ticket_view(
    view_id: int,
    session: SessionData = Depends(get_current_session),
) -> None:
    """Delete a saved ticket view"""
    deleted = await ticket_views_repo.delete_view(view_id, session.user_id)
    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="View not found")


@router.post("/", response_model=TicketDetail, status_code=status.HTTP_201_CREATED)
async def create_ticket(
    payload: TicketCreate,
    actor: dict = Depends(_resolve_ticket_actor),
) -> TicketDetail:
    current_user: dict | None = actor.get("user")
    api_key_record: dict | None = actor.get("api_key")

    # API key requests get full helpdesk access; user requests check permissions
    if api_key_record:
        has_helpdesk_access = True
        # API key requests must provide requester_id since there's no session user
        if payload.requester_id is None:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="requester_id is required when using API key authentication.",
            )
        requester_id = payload.requester_id
        author_id: int | None = None  # No author for API key requests
    elif current_user:
        has_helpdesk_access = await _has_helpdesk_permission(current_user)
        requester_id = int(current_user["id"])
        author_id = int(current_user["id"])
        # Helpdesk users can specify a different requester
        if has_helpdesk_access and payload.requester_id is not None:
            requester_id = payload.requester_id
    else:  # pragma: no cover - defensive guard
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Authentication required")

    company_id = payload.company_id if has_helpdesk_access else (current_user.get("company_id") if current_user else None)

    # Validate requester is an enabled staff member for the company (when company is specified)
    if has_helpdesk_access and payload.requester_id is not None and company_id is not None:
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
        status_value = await tickets_service.resolve_status_or_default(None)
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
        initial_reply_author_id=author_id,
    )
    # Add the requester as a watcher (if we have a valid requester_id)
    if requester_id is not None:
        await tickets_repo.add_watcher(ticket["id"], requester_id)
    try:
        await tickets_service.refresh_ticket_ai_summary(ticket["id"])
    except RuntimeError as exc:
        log_error(f"Failed to refresh AI summary for ticket {ticket['id']}: {exc}", exc_info=True)
    await tickets_service.refresh_ticket_ai_tags(ticket["id"])
    # For API key requests, pass a minimal user dict for building ticket detail
    detail_user = current_user or {"id": requester_id, "is_super_admin": False}
    return await _build_ticket_detail(ticket["id"], detail_user)


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
    # Check if this ticket has been merged into another
    merged_target_id = await tickets_repo.get_merged_target_ticket_id(ticket_id)
    if merged_target_id and merged_target_id != ticket_id:
        # Redirect to the merged target ticket
        ticket_id = merged_target_id
    
    ticket = await tickets_repo.get_ticket(ticket_id)
    if not ticket:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ticket not found")
    
    # Prevent adding time to billed tickets
    if ticket.get("xero_invoice_number"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot add replies to a billed ticket. This ticket has been invoiced and closed.",
        )
    
    has_helpdesk_access = await _has_helpdesk_permission(current_user)
    if not has_helpdesk_access and ticket.get("requester_id") != session.user_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ticket not found")
    sanitised_body = sanitize_rich_text(payload.body)
    if not sanitised_body.has_rich_content:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Reply body cannot be empty.",
        )

    labour_type_id = payload.labour_type_id if has_helpdesk_access else None
    
    # If time is being logged but no labour type specified, use the default labour type
    if has_helpdesk_access and payload.minutes_spent and payload.minutes_spent > 0 and labour_type_id is None:
        default_labour = await labour_types_service.get_default_labour_type()
        if default_labour:
            labour_type_id = default_labour.get("id")
    
    if labour_type_id is not None:
        labour_record = await labour_types_service.get_labour_type(labour_type_id)
        if not labour_record:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Labour type not found")

    reply = await tickets_repo.create_reply(
        ticket_id=ticket_id,
        author_id=session.user_id,
        body=sanitised_body.html,
        is_internal=payload.is_internal if has_helpdesk_access else False,
        minutes_spent=payload.minutes_spent if has_helpdesk_access else None,
        is_billable=payload.is_billable if has_helpdesk_access else False,
        labour_type_id=labour_type_id,
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
    labour_type_name = reply_payload.get("labour_type_name")
    time_summary = tickets_service.format_reply_time_summary(minutes_spent, billable_flag, labour_type_name)
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
    
    # Prevent updating time on billed tickets
    if ticket.get("xero_invoice_number"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot update time entries on a billed ticket. This ticket has been invoiced and closed.",
        )
    
    reply = await tickets_repo.get_reply_by_id(reply_id)
    if not reply or reply.get("ticket_id") != ticket_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Reply not found")

    fields_set = payload.model_fields_set
    update_kwargs: dict[str, Any] = {}
    if "minutes_spent" in fields_set:
        update_kwargs["minutes_spent"] = payload.minutes_spent
    if "is_billable" in fields_set and payload.is_billable is not None:
        update_kwargs["is_billable"] = payload.is_billable
    if "labour_type_id" in fields_set:
        if payload.labour_type_id is None:
            update_kwargs["labour_type_id"] = None
        else:
            labour_record = await labour_types_service.get_labour_type(payload.labour_type_id)
            if not labour_record:
                raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Labour type not found")
            update_kwargs["labour_type_id"] = payload.labour_type_id
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
    labour_type_name = updated.get("labour_type_name")
    time_summary = tickets_service.format_reply_time_summary(minutes_spent, billable_flag, labour_type_name)
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
    await tickets_repo.replace_watchers(ticket_id, payload.user_ids, payload.emails)
    await tickets_service.emit_ticket_updated_event(
        ticket_id,
        actor_type="technician",
        actor=current_user,
    )
    return await _build_ticket_detail(ticket_id, current_user)


@router.post("/{ticket_id}/watchers/{user_id}", response_model=TicketDetail, status_code=status.HTTP_201_CREATED)
async def add_watcher(
    ticket_id: int,
    user_id: int,
    current_user: dict = Depends(require_helpdesk_technician),
) -> TicketDetail:
    ticket = await tickets_repo.get_ticket(ticket_id)
    if not ticket:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ticket not found")
    await tickets_repo.add_watcher(ticket_id, user_id=user_id)
    await tickets_service.emit_ticket_updated_event(
        ticket_id,
        actor_type="technician",
        actor=current_user,
    )
    return await _build_ticket_detail(ticket_id, current_user)


@router.post("/{ticket_id}/watchers/email", response_model=TicketDetail, status_code=status.HTTP_201_CREATED)
async def add_watcher_by_email(
    ticket_id: int,
    email: str = Query(..., description="Email address of the watcher"),
    current_user: dict = Depends(require_helpdesk_technician),
) -> TicketDetail:
    """Add a watcher to a ticket by email address."""
    ticket = await tickets_repo.get_ticket(ticket_id)
    if not ticket:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ticket not found")
    
    email_normalized = email.strip().lower()
    if not email_normalized or "@" not in email_normalized:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid email address"
        )
    
    await tickets_repo.add_watcher(ticket_id, email=email_normalized)
    await tickets_service.emit_ticket_updated_event(
        ticket_id,
        actor_type="technician",
        actor=current_user,
    )
    return await _build_ticket_detail(ticket_id, current_user)


@router.delete("/{ticket_id}/watchers/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_watcher(
    ticket_id: int,
    user_id: int,
    current_user: dict = Depends(require_helpdesk_technician),
) -> None:
    ticket = await tickets_repo.get_ticket(ticket_id)
    if not ticket:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ticket not found")
    await tickets_repo.remove_watcher(ticket_id, user_id=user_id)
    await tickets_service.emit_ticket_updated_event(
        ticket_id,
        actor_type="technician",
        actor=current_user,
    )


@router.delete("/{ticket_id}/watchers/email/{email:path}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_watcher_by_email(
    ticket_id: int,
    email: str,
    current_user: dict = Depends(require_helpdesk_technician),
) -> None:
    """Remove a watcher from a ticket by email address."""
    ticket = await tickets_repo.get_ticket(ticket_id)
    if not ticket:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ticket not found")
    
    email_normalized = email.strip().lower()
    await tickets_repo.remove_watcher(ticket_id, email=email_normalized)
    await tickets_service.emit_ticket_updated_event(
        ticket_id,
        actor_type="technician",
        actor=current_user,
    )


@router.get("/labour-types", response_model=LabourTypeListResponse)
async def list_labour_types_endpoint(
    current_user: dict = Depends(require_helpdesk_technician),
) -> LabourTypeListResponse:
    labour_types = await labour_types_service.list_labour_types()
    items = [
        LabourTypeModel(
            id=int(item.get("id")),
            code=str(item.get("code") or ""),
            name=str(item.get("name") or ""),
            rate=item.get("rate"),
            is_default=bool(item.get("is_default", False)),
            created_at=item.get("created_at"),
            updated_at=item.get("updated_at"),
        )
        for item in labour_types
        if item.get("id") is not None
    ]
    return LabourTypeListResponse(labour_types=items)


@router.post("/labour-types", response_model=LabourTypeModel, status_code=status.HTTP_201_CREATED)
async def create_labour_type_endpoint(
    payload: LabourTypeCreateRequest,
    current_user: dict = Depends(require_super_admin),
) -> LabourTypeModel:
    try:
        record = await labour_types_service.create_labour_type(code=payload.code, name=payload.name, rate=payload.rate)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return LabourTypeModel(**record)


@router.put("/labour-types/{labour_type_id}", response_model=LabourTypeModel)
async def update_labour_type_endpoint(
    labour_type_id: int,
    payload: LabourTypeUpdateRequest,
    current_user: dict = Depends(require_super_admin),
) -> LabourTypeModel:
    try:
        record = await labour_types_service.update_labour_type(
            labour_type_id,
            code=payload.code,
            name=payload.name,
            rate=payload.rate,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    if not record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Labour type not found")
    return LabourTypeModel(**record)


@router.delete("/labour-types/{labour_type_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_labour_type_endpoint(
    labour_type_id: int,
    current_user: dict = Depends(require_super_admin),
) -> None:
    existing = await labour_types_service.get_labour_type(labour_type_id)
    if not existing:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Labour type not found")
    await labour_types_service.delete_labour_type(labour_type_id)


@router.get("/{ticket_id}/tasks", response_model=TicketTaskListResponse)
async def list_ticket_tasks(
    ticket_id: int,
    current_user: dict = Depends(get_current_user),
) -> TicketTaskListResponse:
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
    
    tasks = await ticket_tasks_repo.list_tasks(ticket_id)
    return TicketTaskListResponse(items=[TicketTask(**task) for task in tasks])


@router.post("/{ticket_id}/tasks", response_model=TicketTask, status_code=status.HTTP_201_CREATED)
async def create_ticket_task(
    ticket_id: int,
    payload: TicketTaskCreate,
    current_user: dict = Depends(get_current_user),
) -> TicketTask:
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
    
    task = await ticket_tasks_repo.create_task(
        ticket_id=ticket_id,
        task_name=payload.task_name,
        sort_order=payload.sort_order,
    )
    return TicketTask(**task)


@router.put("/{ticket_id}/tasks/{task_id}", response_model=TicketTask)
async def update_ticket_task(
    ticket_id: int,
    task_id: int,
    payload: TicketTaskUpdate,
    session: SessionData = Depends(get_current_session),
    current_user: dict = Depends(get_current_user),
) -> TicketTask:
    ticket = await tickets_repo.get_ticket(ticket_id)
    if not ticket:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ticket not found")
    
    task = await ticket_tasks_repo.get_task(task_id)
    if not task or task.get("ticket_id") != ticket_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    
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
    
    update_kwargs = {}
    if payload.task_name is not None:
        update_kwargs["task_name"] = payload.task_name
    if payload.is_completed is not None:
        update_kwargs["is_completed"] = payload.is_completed
        if payload.is_completed:
            update_kwargs["completed_by"] = session.user_id
    if payload.sort_order is not None:
        update_kwargs["sort_order"] = payload.sort_order
    
    updated_task = await ticket_tasks_repo.update_task(task_id, **update_kwargs)
    if not updated_task:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    
    return TicketTask(**updated_task)


@router.delete("/{ticket_id}/tasks/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_ticket_task(
    ticket_id: int,
    task_id: int,
    current_user: dict = Depends(get_current_user),
) -> None:
    ticket = await tickets_repo.get_ticket(ticket_id)
    if not ticket:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ticket not found")
    
    task = await ticket_tasks_repo.get_task(task_id)
    if not task or task.get("ticket_id") != ticket_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Task not found")
    
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
    
    await ticket_tasks_repo.delete_task(task_id)


# ==================== Ticket Attachments ====================


@router.get("/{ticket_id}/attachments", response_model=TicketAttachmentListResponse)
async def list_ticket_attachments(
    ticket_id: int,
    current_user: dict = Depends(get_current_user),
) -> TicketAttachmentListResponse:
    """List all attachments for a ticket"""
    ticket = await tickets_repo.get_ticket(ticket_id)
    if not ticket:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ticket not found")
    
    has_helpdesk_access = await _has_helpdesk_permission(current_user)
    
    # Check access permissions
    if not has_helpdesk_access:
        requester_id = ticket.get("requester_id")
        current_user_id = current_user.get("id")
        try:
            current_user_id_int = int(current_user_id)
        except (TypeError, ValueError):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
        
        if requester_id != current_user_id_int:
            is_watcher = await tickets_repo.is_ticket_watcher(ticket_id, current_user_id_int)
            if not is_watcher:
                raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ticket not found")
    
    # Get attachments
    all_attachments = await attachments_repo.list_attachments(ticket_id)
    
    # Filter based on access level
    if has_helpdesk_access:
        attachments = all_attachments
    else:
        # Non-technicians cannot see restricted attachments
        attachments = [
            att for att in all_attachments
            if att.get("access_level") in ("open", "closed")
        ]
    
    return TicketAttachmentListResponse(
        items=[TicketAttachment(**attachment) for attachment in attachments]
    )


@router.post(
    "/{ticket_id}/attachments",
    response_model=TicketAttachment,
    status_code=status.HTTP_201_CREATED,
)
async def upload_ticket_attachment(
    ticket_id: int,
    file: UploadFile = File(...),
    access_level: str = Query(default="closed"),
    session: SessionData = Depends(get_current_session),
    current_user: dict = Depends(get_current_user),
) -> TicketAttachment:
    """Upload a file attachment to a ticket"""
    ticket = await tickets_repo.get_ticket(ticket_id)
    if not ticket:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ticket not found")
    
    has_helpdesk_access = await _has_helpdesk_permission(current_user)
    
    # Check if user can add attachments
    if not has_helpdesk_access:
        requester_id = ticket.get("requester_id")
        if requester_id != session.user_id:
            is_watcher = await tickets_repo.is_ticket_watcher(ticket_id, session.user_id)
            if not is_watcher:
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
    
    # Validate access level
    valid_levels = {"open", "closed", "restricted"}
    if access_level not in valid_levels:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid access level. Must be one of: {', '.join(valid_levels)}"
        )
    
    # Only helpdesk technicians can set restricted or open access
    if not has_helpdesk_access and access_level in ("restricted", "open"):
        access_level = "closed"
    
    # Save the file
    try:
        attachment = await attachments_service.save_uploaded_file(
            ticket_id=ticket_id,
            file=file,
            access_level=access_level,
            uploaded_by_user_id=session.user_id,
        )
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    except IOError as e:
        log_error(f"Failed to upload attachment: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to save file"
        )
    
    return TicketAttachment(**attachment)


@router.get("/{ticket_id}/attachments/{attachment_id}/download")
async def download_ticket_attachment(
    ticket_id: int,
    attachment_id: int,
    current_user: dict = Depends(get_current_user),
):
    """Download a ticket attachment (requires authentication)"""
    attachment = await attachments_repo.get_attachment(attachment_id)
    if not attachment or attachment.get("ticket_id") != ticket_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Attachment not found")
    
    ticket = await tickets_repo.get_ticket(ticket_id)
    if not ticket:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ticket not found")
    
    has_helpdesk_access = await _has_helpdesk_permission(current_user)
    access_level = attachment.get("access_level")
    
    # Check access permissions
    if access_level == "restricted" and not has_helpdesk_access:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
    
    if access_level == "closed" and not has_helpdesk_access:
        requester_id = ticket.get("requester_id")
        current_user_id = current_user.get("id")
        try:
            current_user_id_int = int(current_user_id)
        except (TypeError, ValueError):
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
        
        if requester_id != current_user_id_int:
            is_watcher = await tickets_repo.is_ticket_watcher(ticket_id, current_user_id_int)
            if not is_watcher:
                raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
    
    # Get file path
    file_path = attachments_service.get_attachment_file_path(attachment.get("filename"))
    if not file_path.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")
    
    return FileResponse(
        path=file_path,
        filename=attachment.get("original_filename"),
        media_type=attachment.get("mime_type") or "application/octet-stream",
    )


@router.get("/attachments/open/{token}")
async def download_open_attachment(token: str):
    """Download an attachment with an open access token (no authentication required)"""
    # Verify token
    attachment_id = attachments_service.verify_open_access_token(token)
    if not attachment_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Invalid or expired token"
        )
    
    attachment = await attachments_repo.get_attachment(attachment_id)
    if not attachment:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Attachment not found")
    
    # Verify this is an open access attachment
    if attachment.get("access_level") != "open":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Access denied")
    
    # Get file path
    file_path = attachments_service.get_attachment_file_path(attachment.get("filename"))
    if not file_path.exists():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")
    
    return FileResponse(
        path=file_path,
        filename=attachment.get("original_filename"),
        media_type=attachment.get("mime_type") or "application/octet-stream",
    )


@router.patch("/{ticket_id}/attachments/{attachment_id}", response_model=TicketAttachment)
async def update_ticket_attachment(
    ticket_id: int,
    attachment_id: int,
    payload: TicketAttachmentUpdate,
    current_user: dict = Depends(require_helpdesk_technician),
) -> TicketAttachment:
    """Update attachment metadata (e.g., access level) - technicians only"""
    attachment = await attachments_repo.get_attachment(attachment_id)
    if not attachment or attachment.get("ticket_id") != ticket_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Attachment not found")
    
    fields = payload.model_dump(exclude_unset=True)
    if fields:
        await attachments_repo.update_attachment(attachment_id, **fields)
    
    updated_attachment = await attachments_repo.get_attachment(attachment_id)
    if not updated_attachment:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Attachment not found")
    
    return TicketAttachment(**updated_attachment)


@router.delete("/{ticket_id}/attachments/{attachment_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_ticket_attachment(
    ticket_id: int,
    attachment_id: int,
    current_user: dict = Depends(require_helpdesk_technician),
) -> None:
    """Delete a ticket attachment - technicians only"""
    attachment = await attachments_repo.get_attachment(attachment_id)
    if not attachment or attachment.get("ticket_id") != ticket_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Attachment not found")
    
    try:
        await attachments_service.delete_attachment_file(attachment)
    except Exception as e:
        log_error(f"Failed to delete attachment: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to delete attachment"
        )


@router.get("/{ticket_id}/attachments/{attachment_id}/token")
async def get_open_access_token(
    ticket_id: int,
    attachment_id: int,
    current_user: dict = Depends(require_helpdesk_technician),
) -> dict[str, str]:
    """Generate an open access token for an attachment - technicians only"""
    attachment = await attachments_repo.get_attachment(attachment_id)
    if not attachment or attachment.get("ticket_id") != ticket_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Attachment not found")
    
    if attachment.get("access_level") != "open":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Can only generate tokens for open access attachments"
        )
    
    token = attachments_service.generate_open_access_token(attachment_id)
    return {"token": token, "url": f"/api/tickets/attachments/open/{token}"}


@router.post("/{ticket_id}/split", response_model=TicketSplitResponse, status_code=status.HTTP_201_CREATED)
async def split_ticket(
    ticket_id: int,
    payload: TicketSplitRequest,
    current_user: dict = Depends(require_helpdesk_technician),
) -> TicketSplitResponse:
    """
    Split a ticket by moving selected replies to a new ticket.
    The new ticket will have the same company and requester as the original.
    Requires helpdesk technician permission.
    """
    # Validate original ticket exists
    original_ticket = await tickets_repo.get_ticket(ticket_id)
    if not original_ticket:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Ticket not found")
    
    # Perform the split (validation happens in service layer)
    try:
        original, new_ticket, moved_count = await tickets_service.split_ticket(
            original_ticket_id=ticket_id,
            reply_ids=payload.reply_ids,
            new_subject=payload.new_subject,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    
    if not original or not new_ticket:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to split ticket"
        )
    
    return TicketSplitResponse(
        original_ticket=TicketResponse(**original),
        new_ticket=TicketResponse(**new_ticket),
        moved_reply_count=moved_count,
    )


@router.post("/merge", response_model=TicketMergeResponse)
async def merge_tickets(
    payload: TicketMergeRequest,
    current_user: dict = Depends(require_helpdesk_technician),
) -> TicketMergeResponse:
    """
    Merge multiple tickets into one target ticket.
    All replies and time entries are moved to the target ticket.
    Source tickets are marked as closed and merged.
    Requires helpdesk technician permission.
    """
    # Perform the merge (validation happens in service layer)
    try:
        merged_ticket, merged_ids, moved_count = await tickets_service.merge_tickets(
            ticket_ids=payload.ticket_ids,
            target_ticket_id=payload.target_ticket_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    
    if not merged_ticket:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to merge tickets"
        )
    
    # Count time entries efficiently using database query
    time_entry_count = await tickets_repo.count_time_entries(payload.target_ticket_id)
    
    return TicketMergeResponse(
        merged_ticket=TicketResponse(**merged_ticket),
        merged_ticket_ids=merged_ids,
        moved_reply_count=moved_count,
        moved_time_entry_count=time_entry_count,
    )

