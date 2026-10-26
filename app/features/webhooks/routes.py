"""Webhook admin routes for the ``webhooks`` feature pack."""

from __future__ import annotations

from datetime import datetime, time, timedelta
from typing import Any

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse

from app.repositories import webhook_events as webhook_events_repo
from app.repositories import webhook_deletion_rules as deletion_rules_repo


router = APIRouter(tags=["Webhooks"])


def _main():
    from app import main as main_module

    return main_module


@router.get("/admin/webhooks", response_class=HTMLResponse)
async def admin_webhooks(
    request: Request,
    q: str = "",
    status: str = "",
    event_limit: int = Query(default=1000, ge=1, le=5000),
    llm_feature: str = "",
    llm_start: str = "",
    llm_end: str = "",
):
    main_module = _main()
    current_user, redirect = await main_module._require_super_admin_page(request)
    if redirect:
        return redirect
    status_filter = str(status or "").strip().lower() or None
    llm_filter: dict[str, Any] = {}
    llm_context = None
    if llm_feature:
        from app.services import llm_usage

        start_date, end_date = llm_usage.resolve_date_range(llm_start, llm_end)
        llm_filter = {
            "llm_feature": llm_feature,
            "llm_start": datetime.combine(start_date, time.min),
            "llm_end": datetime.combine(end_date + timedelta(days=1), time.min),
        }
        llm_context = {
            "feature": llm_feature,
            "label": llm_usage.feature_label(llm_feature),
            "start": start_date.isoformat(),
            "end": end_date.isoformat(),
        }
    events = await webhook_events_repo.list_events(
        status=status_filter, search=q, limit=event_limit, **llm_filter
    )
    prepared_events: list[dict[str, Any]] = []
    for event in events:
        serialised_event = main_module._serialise_mapping(event)
        serialised_event["created_iso"] = main_module._to_iso(event.get("created_at"))
        serialised_event["updated_iso"] = main_module._to_iso(event.get("updated_at"))
        serialised_event["next_attempt_iso"] = main_module._to_iso(event.get("next_attempt_at"))
        prepared_events.append(serialised_event)
    prepared_rules: list[dict[str, Any]] = []
    for rule in await deletion_rules_repo.list_rules():
        serialised_rule = main_module._serialise_mapping(rule)
        serialised_rule["next_run_at"] = main_module._to_iso(rule.get("next_run_at"))
        serialised_rule["last_run_at"] = main_module._to_iso(rule.get("last_run_at"))
        prepared_rules.append(serialised_rule)
    status_counts = {
        state: await webhook_events_repo.count_events_by_status(state)
        for state in ("failed", "pending", "in_progress", "succeeded")
    }
    extra = {
        "title": "Webhooks",
        "webhook_status_counts": status_counts,
        "events": prepared_events,
        "webhook_search": q,
        "webhook_status": status_filter or "",
        "webhook_event_limit": event_limit,
        "llm_context": llm_context,
        "webhook_event_limit_options": (200, 500, 1000, 2500, 5000),
        "deletion_rules": prepared_rules,
        "retention": await deletion_rules_repo.get_retention(),
    }
    return await main_module._render_template(
        "admin/webhooks.html",
        request,
        current_user,
        extra=extra,
    )


__all__ = ["router"]
