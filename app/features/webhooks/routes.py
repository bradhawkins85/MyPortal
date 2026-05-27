"""Webhook admin routes for the ``webhooks`` feature pack."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.repositories import webhook_events as webhook_events_repo


router = APIRouter(tags=["Webhooks"])


def _main():
    from app import main as main_module

    return main_module


@router.get("/admin/webhooks", response_class=HTMLResponse)
async def admin_webhooks(request: Request):
    main_module = _main()
    current_user, redirect = await main_module._require_super_admin_page(request)
    if redirect:
        return redirect
    events = await webhook_events_repo.list_events(limit=100)
    prepared_events: list[dict[str, Any]] = []
    for event in events:
        serialised_event = main_module._serialise_mapping(event)
        serialised_event["created_iso"] = main_module._to_iso(event.get("created_at"))
        serialised_event["updated_iso"] = main_module._to_iso(event.get("updated_at"))
        serialised_event["next_attempt_iso"] = main_module._to_iso(event.get("next_attempt_at"))
        prepared_events.append(serialised_event)
    extra = {
        "title": "Webhook delivery queue",
        "events": prepared_events,
    }
    return await main_module._render_template(
        "admin/webhooks.html",
        request,
        current_user,
        extra=extra,
    )


__all__ = ["router"]
