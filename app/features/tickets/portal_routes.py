"""Public-portal ticket routes for the ``tickets`` feature pack.

Mirrors the routes that used to live in ``app/main.py``:

* ``GET  /tickets/new``           — legacy redirect to ``/tickets``.
* ``GET  /tickets``               — portal ticket list.
* ``POST /tickets``               — create a ticket from the portal.
* ``GET  /tickets/{ticket_id}``   — portal ticket detail.
* ``POST /tickets/{ticket_id}/replies`` — post a reply from the portal.

URLs and behaviour are intentionally identical to the previous in-line
handlers so external links, bookmarks, and tests keep working after
the migration.  Helpers and service dependencies are imported lazily
from ``app.main`` and the existing service modules; see the pack
``__init__`` for the rationale.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse

from app.core.logging import log_error
from app.security.flash import flash_redirect
from app.repositories import ticket_views as ticket_views_repo
from app.repositories import tickets as tickets_repo
from app.services import ticket_attachments as attachments_service
from app.services import tickets as tickets_service
from app.services.sanitization import sanitize_rich_text


router = APIRouter(tags=["Tickets"])


def _main():
    """Return the ``app.main`` module.

    The helpers we depend on (``_require_authenticated_user`` etc.) are
    defined there.  We import lazily so the pack file can be imported
    in isolation by tests without dragging in the full app.
    """

    from app import main as main_module

    return main_module


@router.get("/tickets/new", response_class=HTMLResponse)
async def portal_tickets_new_redirect(request: Request):
    user, redirect = await _main()._require_menu_page_access(request, "menu.tickets")
    if redirect:
        return redirect
    return RedirectResponse(url="/tickets", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/tickets", response_class=HTMLResponse)
async def portal_tickets_page(request: Request):
    user, redirect = await _main()._require_menu_page_access(request, "menu.tickets")
    if redirect:
        return redirect

    params = request.query_params
    status_filter = (params.get("status") or "").strip() or None
    search_term = (params.get("q") or "").strip() or None
    success_message = params.get("success")
    error_message = params.get("error")

    # If no explicit filters are provided, try to load the default view
    if not status_filter and not search_term:
        try:
            user_id = int(user.get("id"))
            default_view = await ticket_views_repo.get_default_view(user_id)
            if default_view:
                filters = default_view.get("filters") or {}
                # Apply status filter from default view
                if filters.get("status"):
                    status_list = filters["status"]
                    if isinstance(status_list, list) and status_list:
                        status_filter = ",".join(str(s) for s in status_list)
                # Apply search filter from default view
                if filters.get("search"):
                    search_term = str(filters["search"])
        except (TypeError, ValueError, RuntimeError):
            # If we can't load the default view, just continue without it
            pass

    return await _main()._render_portal_tickets_page(
        request,
        user,
        status_filter=status_filter,
        search_term=search_term,
        success_message=success_message,
        error_message=error_message,
    )


@router.post("/tickets", response_class=HTMLResponse)
async def portal_create_ticket(request: Request):
    main_module = _main()
    user, redirect = await main_module._require_menu_page_access(request, "menu.tickets")
    if redirect:
        return redirect

    params = request.query_params
    status_filter = (params.get("status") or "").strip() or None
    search_term = (params.get("q") or "").strip() or None

    form = await request.form()
    subject = str(form.get("subject") or "").strip()
    description = str(form.get("description") or "").strip()
    form_values = {"subject": subject, "description": description}

    if not subject:
        return await main_module._render_portal_tickets_page(
            request,
            user,
            status_filter=status_filter,
            search_term=search_term,
            error_message="Provide a subject for your ticket.",
            form_values=form_values,
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    sanitized_description = sanitize_rich_text(description)
    description_payload = sanitized_description.html

    try:
        requester_id = int(user.get("id"))
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Access denied"
        ) from None

    company_raw = user.get("company_id")
    try:
        company_id = int(company_raw) if company_raw is not None else None
    except (TypeError, ValueError):
        company_id = None
    if company_id is None:
        return await main_module._render_portal_tickets_page(
            request,
            user,
            status_filter=status_filter,
            search_term=search_term,
            error_message="Select a company before creating a ticket.",
            form_values=form_values,
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    try:
        status_value = await tickets_service.resolve_status_or_default(None)
        ticket = await tickets_service.create_ticket(
            subject=subject,
            description=description_payload,
            requester_id=requester_id,
            company_id=company_id,
            assigned_user_id=None,
            priority="normal",
            status=status_value,
            category=None,
            module_slug=None,
            external_reference=None,
            trigger_automations=True,
            initial_reply_author_id=requester_id,
        )
        await tickets_repo.add_watcher(ticket["id"], requester_id)

        # Handle file attachments
        attachments = form.getlist("attachments")
        if attachments:
            for attachment in attachments:
                if hasattr(attachment, "filename") and attachment.filename:
                    try:
                        await attachments_service.save_uploaded_file(
                            ticket_id=ticket["id"],
                            file=attachment,
                            access_level="closed",  # Users can only create closed-access attachments
                            uploaded_by_user_id=requester_id,
                        )
                    except (ValueError, IOError) as attach_error:
                        log_error(f"Failed to save attachment: {attach_error}")
                        # Continue processing ticket even if attachment fails

        try:
            await tickets_service.refresh_ticket_ai_summary(ticket["id"])
        except RuntimeError:
            pass
        await tickets_service.refresh_ticket_ai_tags(ticket["id"])
    except Exception as exc:  # pragma: no cover - defensive logging
        log_error("Failed to create portal ticket", error=str(exc))
        return await main_module._render_portal_tickets_page(
            request,
            user,
            status_filter=status_filter,
            search_term=search_term,
            error_message="We couldn't create your ticket right now. Please try again.",
            form_values=form_values,
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    return flash_redirect(f"/tickets/{ticket['id']}", "Ticket created.", "success")


@router.get("/tickets/{ticket_id}", response_class=HTMLResponse)
async def portal_ticket_detail(request: Request, ticket_id: int):
    main_module = _main()
    user, redirect = await main_module._require_menu_page_access(request, "menu.tickets")
    if redirect:
        return redirect

    params = request.query_params
    success_message = params.get("success")
    error_message = params.get("error")

    return await main_module._render_portal_ticket_detail(
        request,
        user,
        ticket_id=ticket_id,
        success_message=success_message,
        error_message=error_message,
    )


@router.post("/tickets/{ticket_id}/replies", response_class=HTMLResponse)
async def portal_ticket_reply(request: Request, ticket_id: int):
    main_module = _main()
    user, redirect = await main_module._require_menu_page_access(request, "menu.tickets")
    if redirect:
        return redirect

    try:
        user_id = int(user.get("id"))
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Access denied"
        ) from None

    ticket = await tickets_repo.get_ticket(ticket_id)
    if not ticket:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Ticket not found"
        )

    has_helpdesk_access = await main_module._is_helpdesk_technician(user, request)
    is_super_admin = bool(user.get("is_super_admin"))
    is_requester = ticket.get("requester_id") == user_id
    if not (has_helpdesk_access or is_super_admin or is_requester):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Ticket not found"
        )

    form = await request.form()
    body = str(form.get("body") or "").strip()
    sanitized_body = sanitize_rich_text(body)
    if not sanitized_body.has_rich_content:
        return await main_module._render_portal_ticket_detail(
            request,
            user,
            ticket_id=ticket_id,
            reply_error="Reply message cannot be empty.",
            reply_body=body,
            status_code=status.HTTP_400_BAD_REQUEST,
        )

    try:
        await tickets_repo.create_reply(
            ticket_id=ticket_id,
            author_id=user_id,
            body=sanitized_body.html,
            is_internal=False,
            minutes_spent=None,
            is_billable=False,
        )

        # Handle file attachments
        attachments = form.getlist("attachments")
        if attachments:
            for attachment in attachments:
                if hasattr(attachment, "filename") and attachment.filename:
                    try:
                        await attachments_service.save_uploaded_file(
                            ticket_id=ticket_id,
                            file=attachment,
                            access_level="closed",
                            uploaded_by_user_id=user_id,
                        )
                    except Exception as exc:
                        log_error(
                            "Failed to save attachment",
                            ticket_id=ticket_id,
                            filename=attachment.filename,
                            error=str(exc),
                        )
    except Exception as exc:  # pragma: no cover - defensive logging
        log_error(
            "Failed to create portal ticket reply",
            ticket_id=ticket_id,
            error=str(exc),
        )
        return await main_module._render_portal_ticket_detail(
            request,
            user,
            ticket_id=ticket_id,
            error_message="We couldn't post your reply. Please try again.",
            reply_body=body,
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    try:
        await tickets_service.refresh_ticket_ai_summary(ticket_id)
    except RuntimeError:
        pass
    await tickets_service.refresh_ticket_ai_tags(ticket_id)
    actor_type = "technician" if has_helpdesk_access or is_super_admin else "requester"
    await tickets_service.emit_ticket_updated_event(
        ticket_id,
        actor_type=actor_type,
        actor=user,
    )
    await tickets_service.broadcast_ticket_event(action="reply", ticket_id=ticket_id)

    return flash_redirect(f"/tickets/{ticket_id}", "Reply posted.", "success")


__all__ = ["router"]
