from __future__ import annotations

from datetime import datetime
from typing import Any, Literal, Mapping

from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.api.dependencies.auth import get_current_user
from app.api.dependencies.auth import require_super_admin
from app.api.dependencies.database import require_database
from app.core.notifications import DEFAULT_NOTIFICATION_EVENT_TYPES, merge_event_types
from app.repositories import notifications as notifications_repo
from app.repositories import notification_preferences as preferences_repo
from app.schemas.notifications import (
    NotificationAcknowledgeRequest,
    NotificationCreate,
    NotificationEventSettingResponse,
    NotificationEventSettingUpdate,
    NotificationPreferenceResponse,
    NotificationPreferenceUpdateRequest,
    NotificationResponse,
    NotificationSummaryResponse,
)
from app.services import notification_event_settings as event_settings_service

router = APIRouter(prefix="/api/notifications", tags=["Notifications"])


def _serialise_event_setting(setting: Mapping[str, Any]) -> NotificationEventSettingResponse:
    event_type = str(setting.get("event_type") or "").strip()
    description_value = setting.get("description")
    if isinstance(description_value, str):
        description_value = description_value.strip() or None
    actions_payload: list[dict[str, Any]] = []
    actions_source = setting.get("module_actions") or []
    if isinstance(actions_source, Mapping):
        actions_source = [actions_source]
    if isinstance(actions_source, (list, tuple)):
        for entry in actions_source:
            if not isinstance(entry, Mapping):
                continue
            module = str(entry.get("module") or "").strip()
            if not module:
                continue
            actions_payload.append({"module": module, "payload": entry.get("payload")})
    return NotificationEventSettingResponse(
        event_type=event_type,
        display_name=str(setting.get("display_name") or event_type),
        description=description_value,
        message_template=str(setting.get("message_template") or "{{ message }}"),
        is_user_visible=bool(setting.get("is_user_visible", True)),
        allow_channel_in_app=bool(setting.get("allow_channel_in_app", True)),
        allow_channel_email=bool(setting.get("allow_channel_email", False)),
        allow_channel_sms=bool(setting.get("allow_channel_sms", False)),
        default_channel_in_app=bool(setting.get("default_channel_in_app", True)),
        default_channel_email=bool(setting.get("default_channel_email", False)),
        default_channel_sms=bool(setting.get("default_channel_sms", False)),
        module_actions=actions_payload,
    )


@router.get(
    "/summary",
    response_model=NotificationSummaryResponse,
    summary="Summarise notifications",
    response_description="Aggregate counts for notifications matching the provided filters.",
)
async def summarise_notifications(
    unread_only: bool = Query(
        default=False,
        description="Deprecated. Use read_state=unread instead.",
        deprecated=True,
    ),
    read_state: Literal["all", "unread", "read"] = Query(
        default="all",
        description="Read state to filter by when calculating the total count.",
    ),
    event_types: list[str] | None = Query(
        default=None,
        alias="event_type",
        description="Filter by one or more event types.",
    ),
    search: str | None = Query(
        default=None,
        min_length=1,
        max_length=200,
        description="Full-text search across notification messages and metadata.",
    ),
    created_from: datetime | None = Query(
        default=None,
        description="Return notifications created on or after this ISO 8601 timestamp.",
    ),
    created_to: datetime | None = Query(
        default=None,
        description=(
            "Return notifications created before this ISO 8601 timestamp. "
            "Provide a date with time 00:00 to include the entire day."
        ),
    ),
    _: None = Depends(require_database),
    current_user: dict = Depends(get_current_user),
):
    try:
        user_id = int(current_user.get("id"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User session is invalid")

    selected_read_state = read_state
    if unread_only and read_state == "all":
        selected_read_state = "unread"

    repo_read_state: str | None = selected_read_state if selected_read_state in {"unread", "read"} else None
    search_filter = search.strip() if search else None

    total_count = await notifications_repo.count_notifications(
        user_id=user_id,
        read_state=repo_read_state,
        event_types=event_types,
        search=search_filter,
        created_from=created_from,
        created_to=created_to,
    )

    filtered_unread_count = await notifications_repo.count_notifications(
        user_id=user_id,
        read_state="unread",
        event_types=event_types,
        search=search_filter,
        created_from=created_from,
        created_to=created_to,
    )

    global_unread_count = await notifications_repo.count_notifications(
        user_id=user_id,
        read_state="unread",
    )

    return NotificationSummaryResponse(
        total_count=total_count,
        filtered_unread_count=filtered_unread_count,
        global_unread_count=global_unread_count,
    )


@router.get(
    "/event-types",
    response_model=list[str],
    summary="List notification event types",
    response_description="All known notification event types available to the authenticated user.",
)
async def list_notification_event_types(
    _: None = Depends(require_database),
    current_user: dict = Depends(get_current_user),
):
    try:
        user_id = int(current_user.get("id"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User session is invalid")

    stored_preferences = await preferences_repo.list_preferences(user_id)
    merged = merge_event_types(
        DEFAULT_NOTIFICATION_EVENT_TYPES,
        [preference.get("event_type") for preference in stored_preferences],
        await notifications_repo.list_event_types(user_id=user_id),
    )
    return merged


@router.get(
    "",
    response_model=list[NotificationResponse],
    summary="List notifications",
    response_description="A collection of notifications that match the requested filters.",
)
async def list_notifications(
    unread_only: bool = Query(
        default=False,
        description="Deprecated. Use read_state=unread instead.",
        deprecated=True,
    ),
    read_state: Literal["all", "unread", "read"] = Query(
        default="all",
        description="Read state to filter by.",
    ),
    event_types: list[str] | None = Query(
        default=None,
        alias="event_type",
        description="Filter by one or more event types.",
    ),
    search: str | None = Query(
        default=None,
        min_length=1,
        max_length=200,
        description="Full-text search across notification messages and metadata.",
    ),
    created_from: datetime | None = Query(
        default=None,
        description="Return notifications created on or after this ISO 8601 timestamp.",
    ),
    created_to: datetime | None = Query(
        default=None,
        description=(
            "Return notifications created before this ISO 8601 timestamp. "
            "Provide a date with time 00:00 to include the entire day."
        ),
    ),
    sort_by: Literal["created_at", "event_type", "read_at"] = Query(
        default="created_at",
        description="Column to sort by.",
    ),
    sort_direction: Literal["asc", "desc"] = Query(
        default="desc",
        alias="sort_order",
        description="Sort direction.",
    ),
    limit: int = Query(default=50, ge=1, le=200, description="Maximum records to return."),
    offset: int = Query(default=0, ge=0, description="Number of records to skip."),
    _: None = Depends(require_database),
    current_user: dict = Depends(get_current_user),
):
    selected_read_state = read_state
    if unread_only and read_state == "all":
        selected_read_state = "unread"

    records = await notifications_repo.list_notifications(
        user_id=current_user.get("id"),
        read_state=selected_read_state,
        event_types=event_types,
        search=search.strip() if search else None,
        created_from=created_from,
        created_to=created_to,
        sort_by=sort_by,
        sort_direction=sort_direction,
        limit=limit,
        offset=offset,
    )
    return records


@router.post(
    "",
    response_model=NotificationResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a notification",
    response_description="The newly created notification resource.",
)
async def create_notification(
    payload: NotificationCreate,
    _: None = Depends(require_database),
    _current_user: dict = Depends(require_super_admin),
):
    """Create a notification entry for an individual or all users."""

    record = await notifications_repo.create_notification(
        event_type=payload.event_type,
        message=payload.message,
        user_id=payload.user_id,
        metadata=payload.metadata or {},
    )
    return record


@router.post(
    "/{notification_id}/read",
    response_model=NotificationResponse,
    summary="Mark a notification as read",
    response_description="The updated notification resource.",
)
async def mark_notification_read(
    notification_id: int,
    _: None = Depends(require_database),
    current_user: dict = Depends(get_current_user),
):
    record = await notifications_repo.get_notification(notification_id)
    if not record:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Notification not found")
    if record.get("user_id") not in (None, current_user.get("id")):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not permitted to update this notification")
    updated = await notifications_repo.mark_read(notification_id)
    return updated


@router.post(
    "/acknowledge",
    response_model=list[NotificationResponse],
    summary="Mark multiple notifications as read",
    response_description="The updated notifications in the order they were acknowledged.",
)
async def acknowledge_notifications(
    payload: NotificationAcknowledgeRequest,
    _: None = Depends(require_database),
    current_user: dict = Depends(get_current_user),
):
    """Acknowledge a collection of notifications in a single request."""

    unique_ids: list[int] = []
    seen: set[int] = set()
    for identifier in payload.notification_ids:
        if identifier in seen:
            continue
        unique_ids.append(identifier)
        seen.add(identifier)

    if not unique_ids:
        return []

    for notification_id in unique_ids:
        record = await notifications_repo.get_notification(notification_id)
        if not record:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Notification not found")
        if record.get("user_id") not in (None, current_user.get("id")):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Not permitted to update one or more notifications",
            )

    updated = await notifications_repo.mark_read_bulk(unique_ids)
    return updated


@router.get(
    "/preferences",
    response_model=list[NotificationPreferenceResponse],
    summary="List notification preferences",
    response_description="Notification delivery preferences for the authenticated user.",
)
async def list_notification_preferences(
    _: None = Depends(require_database),
    current_user: dict = Depends(get_current_user),
):
    try:
        user_id = int(current_user.get("id"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User session is invalid")

    stored_preferences = await preferences_repo.list_preferences(user_id)
    is_super_admin = bool(current_user.get("is_super_admin"))
    base_settings = await event_settings_service.list_event_settings(include_hidden=is_super_admin)
    settings_map = {item["event_type"]: item for item in base_settings}

    event_types = merge_event_types(
        settings_map.keys(),
        [preference.get("event_type") for preference in stored_preferences],
        await notifications_repo.list_event_types(user_id=user_id),
    )

    mapped = {pref.get("event_type"): pref for pref in stored_preferences if pref.get("event_type")}

    results: list[NotificationPreferenceResponse] = []
    for event_type in event_types:
        setting = settings_map.get(event_type)
        if not setting:
            setting = await event_settings_service.get_event_setting(event_type)
            settings_map[event_type] = setting
        if not is_super_admin and not bool(setting.get("is_user_visible", True)):
            continue
        pref = mapped.get(event_type)
        allow_in_app = bool(setting.get("allow_channel_in_app", True))
        allow_email = bool(setting.get("allow_channel_email", False))
        allow_sms = bool(setting.get("allow_channel_sms", False))
        if pref:
            channel_in_app = bool(pref.get("channel_in_app")) and allow_in_app
            channel_email = bool(pref.get("channel_email")) and allow_email
            channel_sms = bool(pref.get("channel_sms")) and allow_sms
        else:
            channel_in_app = bool(setting.get("default_channel_in_app", True)) and allow_in_app
            channel_email = bool(setting.get("default_channel_email", False)) and allow_email
            channel_sms = bool(setting.get("default_channel_sms", False)) and allow_sms
        results.append(
            NotificationPreferenceResponse(
                event_type=event_type,
                channel_in_app=channel_in_app,
                channel_email=channel_email,
                channel_sms=channel_sms,
                display_name=str(setting.get("display_name") or event_type),
                description=setting.get("description"),
                allow_channel_in_app=allow_in_app,
                allow_channel_email=allow_email,
                allow_channel_sms=allow_sms,
                default_channel_in_app=bool(setting.get("default_channel_in_app", True)),
                default_channel_email=bool(setting.get("default_channel_email", False)),
                default_channel_sms=bool(setting.get("default_channel_sms", False)),
                is_user_visible=bool(setting.get("is_user_visible", True)),
            )
        )
    return results


@router.put(
    "/preferences",
    response_model=list[NotificationPreferenceResponse],
    summary="Update notification preferences",
    response_description="The persisted preferences after applying the update.",
)
async def update_notification_preferences(
    payload: NotificationPreferenceUpdateRequest,
    _: None = Depends(require_database),
    current_user: dict = Depends(get_current_user),
):
    try:
        user_id = int(current_user.get("id"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="User session is invalid")

    is_super_admin = bool(current_user.get("is_super_admin"))
    base_settings = await event_settings_service.list_event_settings(include_hidden=True)
    settings_map = {item["event_type"]: item for item in base_settings}

    filtered_preferences: list[dict[str, Any]] = []
    for preference in payload.preferences:
        event_type = (preference.event_type or "").strip()
        if not event_type:
            continue
        setting = settings_map.get(event_type)
        if not setting:
            setting = await event_settings_service.get_event_setting(event_type)
            settings_map[event_type] = setting
        if not is_super_admin and not bool(setting.get("is_user_visible", True)):
            continue
        allow_in_app = bool(setting.get("allow_channel_in_app", True))
        allow_email = bool(setting.get("allow_channel_email", False))
        allow_sms = bool(setting.get("allow_channel_sms", False))
        filtered_preferences.append(
            {
                "event_type": event_type,
                "channel_in_app": bool(preference.channel_in_app) and allow_in_app,
                "channel_email": bool(preference.channel_email) and allow_email,
                "channel_sms": bool(preference.channel_sms) and allow_sms,
            }
        )

    updated = await preferences_repo.upsert_preferences(user_id, filtered_preferences)
    mapped = {pref.get("event_type"): pref for pref in updated if pref.get("event_type")}

    event_types = merge_event_types(
        settings_map.keys(),
        mapped.keys(),
        await notifications_repo.list_event_types(user_id=user_id),
    )

    results: list[NotificationPreferenceResponse] = []
    for event_type in event_types:
        setting = settings_map.get(event_type)
        if not setting:
            setting = await event_settings_service.get_event_setting(event_type)
            settings_map[event_type] = setting
        if not is_super_admin and not bool(setting.get("is_user_visible", True)):
            continue
        pref = mapped.get(event_type)
        allow_in_app = bool(setting.get("allow_channel_in_app", True))
        allow_email = bool(setting.get("allow_channel_email", False))
        allow_sms = bool(setting.get("allow_channel_sms", False))
        if pref:
            channel_in_app = bool(pref.get("channel_in_app")) and allow_in_app
            channel_email = bool(pref.get("channel_email")) and allow_email
            channel_sms = bool(pref.get("channel_sms")) and allow_sms
        else:
            channel_in_app = bool(setting.get("default_channel_in_app", True)) and allow_in_app
            channel_email = bool(setting.get("default_channel_email", False)) and allow_email
            channel_sms = bool(setting.get("default_channel_sms", False)) and allow_sms
        results.append(
            NotificationPreferenceResponse(
                event_type=event_type,
                channel_in_app=channel_in_app,
                channel_email=channel_email,
                channel_sms=channel_sms,
                display_name=str(setting.get("display_name") or event_type),
                description=setting.get("description"),
                allow_channel_in_app=allow_in_app,
                allow_channel_email=allow_email,
                allow_channel_sms=allow_sms,
                default_channel_in_app=bool(setting.get("default_channel_in_app", True)),
                default_channel_email=bool(setting.get("default_channel_email", False)),
                default_channel_sms=bool(setting.get("default_channel_sms", False)),
                is_user_visible=bool(setting.get("is_user_visible", True)),
            )
        )
    return results


@router.get(
    "/events/settings",
    response_model=list[NotificationEventSettingResponse],
    summary="List notification event settings",
    response_description="Notification orchestration settings configured by super administrators.",
)
async def list_notification_event_settings(
    _: None = Depends(require_database),
    __: dict = Depends(require_super_admin),
):
    settings = await event_settings_service.list_event_settings(include_hidden=True)
    return [_serialise_event_setting(setting) for setting in settings]


@router.put(
    "/events/settings/{event_type}",
    response_model=NotificationEventSettingResponse,
    summary="Update notification event settings",
    response_description="Persisted notification event configuration.",
)
async def update_notification_event_settings(
    event_type: str,
    payload: NotificationEventSettingUpdate,
    _: None = Depends(require_database),
    __: dict = Depends(require_super_admin),
):
    updated = await event_settings_service.update_event_setting(event_type, payload.model_dump())
    return _serialise_event_setting(updated)
