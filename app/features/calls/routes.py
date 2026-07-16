"""Phone call webhook and admin routes for the calls feature pack."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse

from app.repositories import calls as calls_repo


router = APIRouter(tags=["Calls"])


def _main():
    from app import main as main_module

    return main_module


@router.get("/phonewebhook/{webhook_token}/")
async def receive_phone_webhook(webhook_token: str, request: Request) -> JSONResponse:
    """Receive ActionURL-compatible HTTP GET call events from phones."""
    raw_params = {key: value for key, value in request.query_params.multi_items()}
    supported_params = calls_repo.filter_supported_params(raw_params)
    event_name = calls_repo.normalise_event(raw_params.get("event") or raw_params.get("event_name"))
    source_ip = request.client.host if request.client else None
    user_agent = request.headers.get("user-agent")
    event = await calls_repo.create_call_event(
        webhook_token=webhook_token,
        event_name=event_name,
        supported_params=supported_params,
        raw_params=raw_params,
        source_ip=source_ip,
        user_agent=user_agent,
    )
    return JSONResponse({"ok": True, "id": event.get("id")})


@router.get("/admin/calls", response_class=HTMLResponse)
async def admin_calls_page(request: Request):
    """Admin page for viewing phone call webhook events."""
    main_module = _main()
    current_user, _membership, redirect = await main_module._require_administration_access(request)
    if redirect:
        return redirect

    events = await calls_repo.list_call_events()
    return await main_module._render_template(
        "admin/calls.html",
        request,
        current_user,
        extra={
            "title": "Calls",
            "call_events": events,
            "supported_events": calls_repo.SUPPORTED_EVENTS,
            "supported_variables": calls_repo.SUPPORTED_VARIABLES,
        },
    )


__all__ = ["router"]
