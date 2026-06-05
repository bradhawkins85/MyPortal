"""Notification page routes for the ``notifications`` feature pack."""

from __future__ import annotations

import json
import math
from datetime import timedelta
from typing import Any

from fastapi import APIRouter, HTTPException, Request, status
from fastapi.responses import HTMLResponse

from app.core.notifications import DEFAULT_NOTIFICATION_EVENT_TYPES, merge_event_types
from app.repositories import notification_exclusions as exclusions_repo
from app.repositories import notification_preferences as notification_preferences_repo
from app.repositories import notifications as notifications_repo
from app.services import modules as modules_service
from app.services import notification_event_settings as event_settings_service


router = APIRouter(tags=["Notifications"])


def _main():
    from app import main as main_module

    return main_module


@router.get("/notifications", response_class=HTMLResponse)
async def notifications_dashboard(request: Request):
    main_module = _main()
    user, redirect = await main_module._require_super_admin_page(request)
    if redirect:
        return redirect

    params = request.query_params
    search_term = (params.get("q") or "").strip()
    read_state = (params.get("read_state") or "all").lower()
    valid_read_states = {option[0] for option in main_module._NOTIFICATION_READ_OPTIONS}
    if read_state not in valid_read_states:
        read_state = "all"

    sort_by = (params.get("sort_by") or "created_at").lower()
    valid_sort_columns = {option[0] for option in main_module._NOTIFICATION_SORT_CHOICES}
    if sort_by not in valid_sort_columns:
        sort_by = "created_at"

    sort_order = (params.get("sort_order") or "desc").lower()
    if sort_order not in {"asc", "desc"}:
        sort_order = "desc"

    event_type_filter = (params.get("event_type") or "").strip()
    created_from_raw = (params.get("created_from") or "").strip()
    created_to_raw = (params.get("created_to") or "").strip()

    page_size = main_module._parse_int_in_range(
        params.get("page_size"), default=25, minimum=5, maximum=100
    )
    if main_module._NOTIFICATION_PAGE_SIZES:
        page_size = min(
            main_module._NOTIFICATION_PAGE_SIZES,
            key=lambda size: abs(size - page_size),
        )
    page = main_module._parse_int_in_range(
        params.get("page"), default=1, minimum=1, maximum=1000
    )

    created_from_dt = main_module._parse_input_datetime(created_from_raw)
    created_to_dt = None
    created_to_candidate = main_module._parse_input_datetime(
        created_to_raw, assume_midnight=True
    )
    if created_to_candidate:
        if created_to_raw and all(separator not in created_to_raw for separator in ("T", " ")):
            created_to_dt = created_to_candidate + timedelta(days=1)
        else:
            created_to_dt = created_to_candidate

    search_filter = search_term or None
    event_filters = [event_type_filter] if event_type_filter else None
    repo_read_state = read_state if read_state in {"unread", "read"} else None

    try:
        user_id = int(user.get("id"))
    except (TypeError, ValueError):
        user_id = None

    total_count = await notifications_repo.count_notifications(
        user_id=user_id,
        read_state=repo_read_state,
        event_types=event_filters,
        search=search_filter,
        created_from=created_from_dt,
        created_to=created_to_dt,
    )

    total_pages = max(1, math.ceil(total_count / page_size)) if page_size else 1
    if page > total_pages:
        page = total_pages

    offset = (page - 1) * page_size

    records = await notifications_repo.list_notifications(
        user_id=user_id,
        read_state=repo_read_state,
        event_types=event_filters,
        search=search_filter,
        created_from=created_from_dt,
        created_to=created_to_dt,
        sort_by=sort_by,
        sort_direction=sort_order,
        limit=page_size,
        offset=offset,
    )

    filtered_unread_count = await notifications_repo.count_notifications(
        user_id=user_id,
        read_state="unread",
        event_types=event_filters,
        search=search_filter,
        created_from=created_from_dt,
        created_to=created_to_dt,
    )

    global_unread_count = await notifications_repo.count_notifications(
        user_id=user_id,
        read_state="unread",
    )

    prepared_notifications: list[dict[str, Any]] = []
    excluded_event_types: set[str] = set()
    if user_id is not None:
        excluded_event_types = set(await exclusions_repo.list_excluded_event_types(user_id))
    for record in records:
        metadata_items = main_module._prepare_notification_metadata(record.get("metadata"))
        raw_metadata = record.get("metadata") or {}
        created_iso = main_module._to_iso(record.get("created_at")) or ""
        read_iso = main_module._to_iso(record.get("read_at")) or ""
        is_unread = record.get("read_at") is None
        event_type = str(record.get("event_type") or "")
        is_excluded = bool(event_type) and event_type in excluded_event_types
        prepared_notifications.append(
            {
                "id": record.get("id"),
                "event_type": event_type,
                "message": record.get("message"),
                "metadata_items": metadata_items,
                "raw_metadata": raw_metadata,
                "created_iso": created_iso,
                "read_iso": read_iso,
                "is_unread": is_unread,
                "is_excluded": is_excluded,
                "status_label": "Unread" if is_unread else "Read",
                "status_class": "status status--unread" if is_unread else "status status--read",
                "metadata_json": json.dumps(
                    main_module._serialise_for_json(record.get("metadata")),
                    ensure_ascii=False,
                    indent=2,
                )
                if record.get("metadata") is not None
                else None,
            }
        )

    pagination = {
        "page": page,
        "page_size": page_size,
        "total_pages": total_pages,
        "total_count": total_count,
        "start": offset + 1 if total_count else 0,
        "end": offset + len(prepared_notifications),
        "has_previous": page > 1,
        "has_next": page < total_pages,
        "previous_url": str(request.url.include_query_params(page=page - 1)) if page > 1 else None,
        "next_url": str(request.url.include_query_params(page=page + 1)) if page < total_pages else None,
    }

    filters = {
        "query": search_term,
        "read_state": read_state,
        "event_type": event_type_filter,
        "created_from": created_from_raw,
        "created_to": created_to_raw,
        "sort_by": sort_by,
        "sort_order": sort_order,
        "page_size": page_size,
        "page": page,
    }

    active_filters = any(
        [
            bool(search_term),
            read_state != "all",
            bool(event_type_filter),
            bool(created_from_raw),
            bool(created_to_raw),
        ]
    )

    event_type_options = await notifications_repo.list_event_types(user_id=user_id)

    extra = {
        "title": "Notifications",
        "notifications": prepared_notifications,
        "filters": filters,
        "filters_active": active_filters,
        "sort_options": main_module._NOTIFICATION_SORT_CHOICES,
        "order_options": main_module._NOTIFICATION_ORDER_CHOICES,
        "read_options": main_module._NOTIFICATION_READ_OPTIONS,
        "event_type_options": event_type_options,
        "pagination": pagination,
        "total_count": total_count,
        "filtered_unread_count": filtered_unread_count,
        "page_size_options": main_module._NOTIFICATION_PAGE_SIZES,
        "notification_unread_count": global_unread_count,
    }

    return await main_module._render_template("notifications/index.html", request, user, extra=extra)


@router.get("/notifications/settings", response_class=HTMLResponse)
async def notification_settings_page(request: Request):
    main_module = _main()
    user, redirect = await main_module._require_super_admin_page(request)
    if redirect:
        return redirect

    try:
        user_id = int(user.get("id"))
    except (TypeError, ValueError):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="User session invalid"
        )

    stored_preferences = await notification_preferences_repo.list_preferences(user_id)
    is_super_admin = bool(user.get("is_super_admin"))
    base_settings = await event_settings_service.list_event_settings(include_hidden=True)
    settings_map = {item["event_type"]: item for item in base_settings}

    event_types = merge_event_types(
        settings_map.keys(),
        [preference.get("event_type") for preference in stored_preferences],
        await notifications_repo.list_event_types(user_id=user_id),
    )

    mapped = {pref.get("event_type"): pref for pref in stored_preferences if pref.get("event_type")}
    preferences: list[dict[str, Any]] = []
    for event_type in event_types:
        setting = settings_map.get(event_type)
        if not setting:
            setting = await event_settings_service.get_event_setting(event_type)
            settings_map[event_type] = setting
        if not is_super_admin and not bool(setting.get("is_user_visible", True)):
            continue
        pref = mapped.get(event_type) or {}
        allow_in_app = bool(setting.get("allow_channel_in_app", True))
        allow_email = bool(setting.get("allow_channel_email", False))
        allow_sms = bool(setting.get("allow_channel_sms", False))
        channel_in_app = bool(pref.get("channel_in_app", setting.get("default_channel_in_app", True))) and allow_in_app
        channel_email = bool(pref.get("channel_email", setting.get("default_channel_email", False))) and allow_email
        channel_sms = bool(pref.get("channel_sms", setting.get("default_channel_sms", False))) and allow_sms
        description_value = setting.get("description")
        if isinstance(description_value, str):
            description_value = description_value.strip() or None
        preferences.append(
            {
                "event_type": event_type,
                "display_name": str(setting.get("display_name") or event_type),
                "description": description_value,
                "channel_in_app": channel_in_app,
                "channel_email": channel_email,
                "channel_sms": channel_sms,
                "allow_channel_in_app": allow_in_app,
                "allow_channel_email": allow_email,
                "allow_channel_sms": allow_sms,
                "default_channel_in_app": bool(setting.get("default_channel_in_app", True)),
                "default_channel_email": bool(setting.get("default_channel_email", False)),
                "default_channel_sms": bool(setting.get("default_channel_sms", False)),
                "is_user_visible": bool(setting.get("is_user_visible", True)),
            }
        )

    modules = await modules_service.list_modules()
    modules_payload = main_module._serialise_for_json(modules)
    event_settings_payload = [
        {
            "event_type": item.get("event_type"),
            "display_name": item.get("display_name"),
            "description": item.get("description"),
            "message_template": item.get("message_template"),
            "is_user_visible": bool(item.get("is_user_visible", True)),
            "allow_channel_in_app": bool(item.get("allow_channel_in_app", True)),
            "allow_channel_email": bool(item.get("allow_channel_email", False)),
            "allow_channel_sms": bool(item.get("allow_channel_sms", False)),
            "default_channel_in_app": bool(item.get("default_channel_in_app", True)),
            "default_channel_email": bool(item.get("default_channel_email", False)),
            "default_channel_sms": bool(item.get("default_channel_sms", False)),
            "module_actions": main_module._serialise_for_json(item.get("module_actions") or []),
        }
        for item in base_settings
    ]

    if is_super_admin:
        menu_events = [
            {
                "event_type": item.get("event_type"),
                "display_name": item.get("display_name") or item.get("event_type"),
                "description": item.get("description"),
                "is_user_visible": bool(item.get("is_user_visible", True)),
            }
            for item in event_settings_payload
        ]
    else:
        menu_events = [
            {
                "event_type": item.get("event_type"),
                "display_name": item.get("display_name") or item.get("event_type"),
                "description": item.get("description"),
                "is_user_visible": bool(item.get("is_user_visible", True)),
            }
            for item in preferences
        ]

    extra = {
        "title": "Notification settings",
        "preferences": preferences,
        "preferences_endpoint": "/api/notifications/preferences",
        "channel_descriptions": {
            "channel_in_app": "Store notifications in the in-app feed",
            "channel_email": "Email the notification to your primary address",
            "channel_sms": "Send a text message to your mobile number",
        },
        "default_event_types": set(DEFAULT_NOTIFICATION_EVENT_TYPES),
        "is_super_admin": is_super_admin,
        "event_settings": event_settings_payload,
        "event_settings_endpoint": "/api/notifications/events/settings",
        "modules": modules_payload,
        "event_menu": menu_events,
    }

    return await main_module._render_template("notifications/settings.html", request, user, extra=extra)


__all__ = ["router"]
