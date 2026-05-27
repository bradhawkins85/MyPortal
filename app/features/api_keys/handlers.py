"""API key admin handlers for the ``api_keys`` feature pack."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime, time, timezone
from ipaddress import ip_network
from typing import Any

from fastapi import Request

from app.schemas.api_keys import ALLOWED_API_KEY_HTTP_METHODS
from app.security.api_keys import mask_api_key


def _main():
    from app import main as main_module

    return main_module


_API_KEY_ORDER_CHOICES: list[tuple[str, str]] = [
    ("created_at", "Creation date"),
    ("last_used_at", "Last activity"),
    ("expiry_date", "Expiry date"),
    ("usage_count", "Usage count"),
    ("description", "Description"),
]
_API_KEY_ORDER_COLUMNS = {choice[0] for choice in _API_KEY_ORDER_CHOICES}
_API_KEY_DIRECTION_CHOICES: list[tuple[str, str]] = [
    ("desc", "Descending"),
    ("asc", "Ascending"),
]


def _normalise_api_key_order(order_by: str | None) -> str:
    if not order_by:
        return "created_at"
    if order_by in _API_KEY_ORDER_COLUMNS:
        return order_by
    return "created_at"


def _normalise_direction(direction: str | None) -> str:
    if not direction:
        return "desc"
    return "asc" if direction.lower() == "asc" else "desc"


def _extract_api_key_filters(data: Mapping[str, Any]) -> dict[str, Any]:
    main_module = _main()
    search = (str(data.get("search", "")).strip() or None) if data else None
    include_expired = main_module._parse_bool(data.get("include_expired"))
    order_by = _normalise_api_key_order(str(data.get("order_by", "")))
    order_direction = _normalise_direction(str(data.get("order_direction", "")))
    service_filter = (str(data.get("service_filter", "")).strip() or None)
    correlation_search = (str(data.get("correlation_search", "")).strip() or None)
    return {
        "search": search,
        "include_expired": include_expired,
        "order_by": order_by,
        "order_direction": order_direction,
        "service_filter": service_filter,
        "correlation_search": correlation_search,
    }


def _format_correlation_label(raw_key: str) -> str:
    prefix, _, value = raw_key.partition(":")
    safe_value = (value or "").strip()
    if prefix == "api_key":
        preview = safe_value[-4:] if safe_value else "••••"
        return f"API key fingerprint …{preview}"
    if prefix == "api_key_meta":
        preview = safe_value[-4:] if safe_value else "••••"
        return f"Metadata API key …{preview}"
    if prefix == "ip":
        return f"Source IP {safe_value or 'unknown'}"
    if prefix.endswith("_id"):
        label = prefix.replace("_", " ").title()
        return f"{label} #{safe_value or '?'}"
    if prefix:
        label = prefix.replace("_", " ").title()
        return f"{label} {safe_value}".strip()
    return safe_value or "Correlation"


def _format_entity_label(log: Mapping[str, Any]) -> str:
    entity_type = str(log.get("entity_type") or "system").strip()
    entity_id = log.get("entity_id")
    if entity_id is not None:
        return f"{entity_type} #{entity_id}"
    return entity_type


def _derive_correlation_keys(log: Mapping[str, Any]) -> list[str]:
    keys: list[str] = []
    entity_type = log.get("entity_type")
    entity_id = log.get("entity_id")
    if entity_type and entity_id is not None:
        keys.append(f"{entity_type}:{entity_id}")
    metadata = log.get("metadata")
    if isinstance(metadata, Mapping):
        for candidate in ("api_key_id", "company_id", "webhook_event_id", "task_id", "user_id"):
            value = metadata.get(candidate)
            if value in (None, "", [], {}):
                continue
            keys.append(f"{candidate}:{value}")
        if metadata.get("source_ip"):
            keys.append(f"ip:{metadata['source_ip']}")
        if metadata.get("api_key"):
            keys.append(f"api_key_meta:{metadata['api_key']}")
    if log.get("api_key"):
        keys.append(f"api_key:{log['api_key']}")
    if log.get("ip_address"):
        keys.append(f"ip:{log['ip_address']}")
    seen: set[str] = set()
    unique_keys: list[str] = []
    for key in keys:
        if key not in seen:
            unique_keys.append(key)
            seen.add(key)
    return unique_keys


def _extract_audit_service(action: Any) -> str:
    if not action:
        return "system"
    text = str(action)
    return text.split(".", 1)[0]


def _parse_permission_lines(value: str | None) -> tuple[list[dict[str, Any]], list[str]]:
    if value is None:
        return [], []
    entries: dict[str, set[str]] = {}
    errors: list[str] = []
    allowed_methods = ", ".join(sorted(ALLOWED_API_KEY_HTTP_METHODS))
    for index, raw_line in enumerate(str(value).splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        slash_index = line.find("/")
        if slash_index <= 0:
            errors.append(f"Line {index}: Enter values as 'METHOD /path'.")
            continue
        method_part = line[:slash_index].strip()
        path = line[slash_index:].strip()
        if not path.startswith("/"):
            errors.append(f"Line {index}: Paths must start with '/'.")
            continue
        raw_methods = [
            token.strip().upper()
            for token in method_part.replace(",", " ").split()
        ]
        methods = [token for token in raw_methods if token]
        if not methods:
            errors.append(f"Line {index}: Provide at least one HTTP method.")
            continue
        invalid = [token for token in methods if token not in ALLOWED_API_KEY_HTTP_METHODS]
        if invalid:
            errors.append(
                f"Line {index}: Unsupported method(s) {', '.join(invalid)}. Allowed methods: {allowed_methods}."
            )
            continue
        entries.setdefault(path, set()).update(methods)
    parsed = [
        {"path": path, "methods": sorted(methods)}
        for path, methods in sorted(entries.items(), key=lambda item: item[0])
    ]
    return parsed, errors


def _parse_ip_restriction_lines(value: str | None) -> tuple[list[str], list[str]]:
    if value is None:
        return [], []
    entries: set[str] = set()
    errors: list[str] = []
    for index, raw_line in enumerate(str(value).splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue
        try:
            network = ip_network(line, strict=False)
        except ValueError:
            errors.append(f"Line {index}: Enter a valid IP address or CIDR range.")
            continue
        entries.add(network.with_prefixlen)
    ordered = sorted(entries)
    return ordered, errors


def _format_api_key_permissions(
    permissions: list[dict[str, Any]] | None,
) -> tuple[list[dict[str, Any]], str, str]:
    display_entries: list[dict[str, Any]] = []
    if permissions:
        for entry in permissions:
            path = str(entry.get("path", "")).strip()
            methods = sorted(
                {str(method).strip().upper() for method in entry.get("methods", []) if str(method).strip()}
            )
            if not path or not methods:
                continue
            display_entries.append({"path": path, "methods": methods})
    display_entries.sort(key=lambda item: item["path"])
    permissions_text = "\n".join(
        f"{', '.join(entry['methods'])} {entry['path']}" for entry in display_entries
    )
    if display_entries:
        summary_parts = [
            f"{', '.join(entry['methods'])} {entry['path']}" for entry in display_entries[:2]
        ]
        remaining = len(display_entries) - 2
        if remaining > 0:
            summary_parts.append(f"+{remaining} more")
        access_summary = ", ".join(summary_parts)
    else:
        access_summary = "All endpoints"
    return display_entries, permissions_text, access_summary


def _format_api_key_ip_restrictions(
    restrictions: list[dict[str, Any]] | list[str] | None,
) -> tuple[list[dict[str, str]], str, str]:
    display_entries: list[dict[str, str]] = []
    if restrictions:
        for entry in restrictions:
            if isinstance(entry, Mapping):
                raw_value = entry.get("cidr")
            else:
                raw_value = entry
            value = str(raw_value or "").strip()
            if not value:
                continue
            try:
                network = ip_network(value, strict=False)
            except ValueError:
                continue
            canonical = network.with_prefixlen
            if network.prefixlen == network.max_prefixlen:
                label = network.network_address.compressed
            else:
                label = canonical
            display_entries.append({"cidr": canonical, "label": label})
    display_entries.sort(key=lambda item: item["label"])
    text_value = "\n".join(item["label"] for item in display_entries)
    if display_entries:
        summary_parts = [item["label"] for item in display_entries[:2]]
        remaining = len(display_entries) - 2
        if remaining > 0:
            summary_parts.append(f"+{remaining} more")
        summary = ", ".join(summary_parts)
    else:
        summary = "Any IP address"
    return display_entries, text_value, summary


def _prepare_api_key_rows(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, int]]:
    main_module = _main()
    today = date.today()
    prepared: list[dict[str, Any]] = []
    active_count = 0
    disabled_count = 0
    expired_count = 0
    for row in rows:
        expiry = row.get("expiry_date")
        is_expired = bool(expiry and isinstance(expiry, date) and expiry < today)
        is_enabled = bool(row.get("is_enabled", True))
        if is_expired:
            expired_count += 1
        elif is_enabled:
            active_count += 1
        else:
            disabled_count += 1
        usage_entries: list[dict[str, Any]] = []
        for entry in row.get("usage", []) or []:
            usage_entries.append(
                {
                    "ip_address": entry.get("ip_address"),
                    "usage_count": entry.get("usage_count", 0),
                    "last_used_iso": main_module._to_iso(entry.get("last_used_at")),
                }
            )
        display_permissions, permissions_text, endpoint_summary = _format_api_key_permissions(
            row.get("permissions")
        )
        display_ip_restrictions, ip_text, ip_summary = _format_api_key_ip_restrictions(
            row.get("ip_restrictions")
        )
        if endpoint_summary and ip_summary:
            access_summary = f"{endpoint_summary} • {ip_summary}"
        elif endpoint_summary:
            access_summary = endpoint_summary
        else:
            access_summary = ip_summary
        expiry_iso = None
        if isinstance(expiry, date):
            expiry_iso = datetime.combine(expiry, time.min, tzinfo=timezone.utc).isoformat()
        prepared.append(
            {
                "id": row["id"],
                "description": row.get("description"),
                "key_preview": mask_api_key(row.get("key_prefix")),
                "created_iso": main_module._to_iso(row.get("created_at")),
                "expiry_date": expiry.isoformat() if isinstance(expiry, date) else None,
                "expiry_iso": expiry_iso,
                "last_used_iso": main_module._to_iso(row.get("last_used_at")),
                "last_seen_iso": main_module._to_iso(row.get("last_seen_at")),
                "usage_count": row.get("usage_count", 0),
                "is_expired": is_expired,
                "usage": usage_entries,
                "permissions": display_permissions,
                "permissions_text": permissions_text,
                "ip_restrictions": display_ip_restrictions,
                "ip_restrictions_text": ip_text,
                "ip_summary": ip_summary,
                "endpoint_summary": endpoint_summary,
                "access_summary": access_summary,
                "is_restricted": bool(display_permissions or display_ip_restrictions),
                "is_enabled": is_enabled,
            }
        )
    stats = {
        "total": len(prepared),
        "active": active_count,
        "expired": expired_count,
        "disabled": disabled_count,
    }
    return prepared, stats


def _build_audit_correlations(
    logs: list[dict[str, Any]],
    *,
    service_filter: str | None = None,
    text_query: str | None = None,
    limit: int = 25,
) -> tuple[list[dict[str, Any]], list[str]]:
    main_module = _main()
    services: set[str] = set()
    groups: dict[str, list[dict[str, Any]]] = {}
    for log in logs:
        service = _extract_audit_service(log.get("action"))
        services.add(service)
        for key in _derive_correlation_keys(log):
            groups.setdefault(key, []).append(log)
    correlations: list[dict[str, Any]] = []
    text = text_query.lower().strip() if text_query else None
    for key, items in groups.items():
        if len(items) < 2:
            continue
        item_services = sorted({_extract_audit_service(item.get("action")) for item in items})
        if service_filter and service_filter not in item_services:
            continue
        label = _format_correlation_label(key)
        if text and text not in label.lower():
            continue
        sorted_items = sorted(
            items,
            key=lambda entry: entry.get("created_at") or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )
        latest = sorted_items[0]
        events: list[dict[str, Any]] = []
        for entry in sorted_items[:8]:
            events.append(
                {
                    "id": entry.get("id"),
                    "action": entry.get("action"),
                    "service": _extract_audit_service(entry.get("action")),
                    "created_at_iso": main_module._to_iso(entry.get("created_at")),
                    "entity_label": _format_entity_label(entry),
                    "user_email": entry.get("user_email"),
                    "user_id": entry.get("user_id"),
                    "ip_address": entry.get("ip_address"),
                    "metadata": main_module._serialise_for_json(entry.get("metadata")),
                }
            )
        correlations.append(
            {
                "key": key,
                "label": label,
                "services": item_services,
                "event_count": len(items),
                "latest_iso": main_module._to_iso(latest.get("created_at")),
                "events": events,
            }
        )
    correlations.sort(
        key=lambda item: item.get("latest_iso") or "",
        reverse=True,
    )
    return correlations[:limit], sorted(services)


async def _render_api_keys_dashboard(
    request: Request,
    current_user: dict[str, Any],
    *,
    search: str | None,
    include_expired: bool,
    order_by: str,
    order_direction: str,
    service_filter: str | None,
    correlation_search: str | None,
    status_message: str | None = None,
    errors: list[str] | None = None,
    new_api_key: dict[str, Any] | None = None,
):
    main_module = _main()
    rows = await main_module.api_key_repo.list_api_keys_with_usage(
        search=search,
        include_expired=include_expired,
        order_by=order_by,
        order_direction=order_direction,
    )
    prepared_keys, stats = _prepare_api_key_rows(rows)
    logs = await main_module.audit_repo.list_audit_logs(limit=250)
    correlations, service_names = _build_audit_correlations(
        logs,
        service_filter=service_filter,
        text_query=correlation_search,
    )
    filter_state = {
        "search": search or "",
        "include_expired": "1" if include_expired else "0",
        "order_by": order_by,
        "order_direction": order_direction,
        "service_filter": service_filter or "",
        "correlation_search": correlation_search or "",
    }
    filters = {
        "search": search or "",
        "include_expired": include_expired,
        "order_by": order_by,
        "order_direction": order_direction,
        "service_filter": service_filter or "",
        "correlation_search": correlation_search or "",
    }
    order_options = [
        {"value": value, "label": label}
        for value, label in _API_KEY_ORDER_CHOICES
    ]
    direction_options = [
        {"value": value, "label": label}
        for value, label in _API_KEY_DIRECTION_CHOICES
    ]
    service_options = [
        {"value": value, "label": value.replace("_", " ").title()}
        for value in service_names
    ]
    extra = {
        "title": "API credentials",
        "api_keys": prepared_keys,
        "api_key_stats": stats,
        "filters": filters,
        "filter_state": filter_state,
        "order_options": order_options,
        "direction_options": direction_options,
        "service_options": service_options,
        "correlations": correlations,
        "status_message": status_message,
        "errors": errors or [],
        "new_api_key": new_api_key,
        "allowed_methods": sorted(ALLOWED_API_KEY_HTTP_METHODS),
    }
    return await main_module._render_template("admin/api_keys.html", request, current_user, extra=extra)


async def admin_api_keys_page(request: Request):
    main_module = _main()
    current_user, redirect = await main_module._require_super_admin_page(request)
    if redirect:
        return redirect
    filters = _extract_api_key_filters(request.query_params)
    return await _render_api_keys_dashboard(request, current_user, **filters)


async def admin_create_api_key_page(request: Request):
    main_module = _main()
    current_user, redirect = await main_module._require_super_admin_page(request)
    if redirect:
        return redirect
    form = await request.form()
    filters = _extract_api_key_filters(form)
    description = (str(form.get("description", "")).strip() or None)
    expiry_raw = form.get("expiry_date")
    expiry_date = main_module._parse_input_date(expiry_raw) if expiry_raw else None
    permissions_raw = form.get("permissions")
    permissions_text = str(permissions_raw).strip() if permissions_raw is not None else ""
    parsed_permissions, permission_errors = _parse_permission_lines(permissions_text)
    allowed_ips_raw = form.get("allowed_ips")
    allowed_ips_text = str(allowed_ips_raw).strip() if allowed_ips_raw is not None else ""
    parsed_ip_restrictions, ip_errors = _parse_ip_restriction_lines(allowed_ips_text)
    errors: list[str] = []
    if expiry_raw and expiry_date is None:
        errors.append("Enter an expiry date in YYYY-MM-DD format.")
    errors.extend(permission_errors)
    errors.extend(ip_errors)
    is_enabled = True
    if "is_enabled_present" in form:
        is_enabled = str(form.get("is_enabled")) == "1"
    if errors:
        return await _render_api_keys_dashboard(
            request,
            current_user,
            **filters,
            status_message=None,
            errors=errors,
        )
    try:
        raw_key, row = await main_module.api_key_repo.create_api_key(
            description=description,
            expiry_date=expiry_date,
            permissions=parsed_permissions,
            ip_restrictions=parsed_ip_restrictions,
            is_enabled=is_enabled,
        )
    except Exception as exc:  # pragma: no cover - defensive logging
        main_module.log_error("Failed to create API key from admin form", error=str(exc))
        errors.append("Unable to create API key. Please try again.")
        return await _render_api_keys_dashboard(
            request,
            current_user,
            **filters,
            status_message=None,
            errors=errors,
        )
    await main_module.audit_service.log_action(
        action="api_keys.create",
        user_id=current_user.get("id"),
        entity_type="api_key",
        entity_id=row["id"],
        new_value={
            "description": description,
            "expiry_date": expiry_date.isoformat() if isinstance(expiry_date, date) else None,
            "permissions": parsed_permissions,
            "allowed_ips": parsed_ip_restrictions,
            "is_enabled": is_enabled,
        },
        request=request,
    )
    display_permissions, permissions_text_value, endpoint_summary = _format_api_key_permissions(
        row.get("permissions")
    )
    display_ip_restrictions, allowed_ips_text_value, ip_summary = _format_api_key_ip_restrictions(
        row.get("ip_restrictions")
    )
    if endpoint_summary and ip_summary:
        access_summary = f"{endpoint_summary} • {ip_summary}"
    elif endpoint_summary:
        access_summary = endpoint_summary
    else:
        access_summary = ip_summary
    new_api_key = {
        "id": row["id"],
        "value": raw_key,
        "key_preview": mask_api_key(row.get("key_prefix")),
        "description": row.get("description"),
        "expiry_iso": (
            datetime.combine(row.get("expiry_date"), time.min, tzinfo=timezone.utc).isoformat()
            if row.get("expiry_date")
            else None
        ),
        "permissions": display_permissions,
        "permissions_text": permissions_text_value,
        "ip_restrictions": display_ip_restrictions,
        "ip_restrictions_text": allowed_ips_text_value,
        "access_summary": access_summary,
        "ip_summary": ip_summary,
        "endpoint_summary": endpoint_summary,
        "is_enabled": bool(row.get("is_enabled", True)),
    }
    status_message = "New API key created. Store the value securely; it will not be shown again."
    return await _render_api_keys_dashboard(
        request,
        current_user,
        **filters,
        status_message=status_message,
        errors=None,
        new_api_key=new_api_key,
    )


async def admin_update_api_key_page(request: Request):
    main_module = _main()
    current_user, redirect = await main_module._require_super_admin_page(request)
    if redirect:
        return redirect
    form = await request.form()
    filters = _extract_api_key_filters(form)
    errors: list[str] = []
    api_key_id_raw = form.get("api_key_id")
    try:
        api_key_id = int(api_key_id_raw)
    except (TypeError, ValueError):
        errors.append("Invalid API key identifier supplied for update.")
        return await _render_api_keys_dashboard(
            request,
            current_user,
            **filters,
            status_message=None,
            errors=errors,
        )

    existing = await main_module.api_key_repo.get_api_key_with_usage(api_key_id)
    if not existing:
        errors.append("API key not found or no longer available.")
        return await _render_api_keys_dashboard(
            request,
            current_user,
            **filters,
            status_message=None,
            errors=errors,
        )

    description_raw = form.get("description")
    description_text = str(description_raw).strip() if description_raw is not None else ""
    new_description = description_text or None

    expiry_raw = form.get("expiry_date")
    expiry_date = main_module._parse_input_date(expiry_raw) if expiry_raw else None
    if expiry_raw and expiry_date is None:
        errors.append("Enter a valid expiry date in YYYY-MM-DD format.")

    permissions_raw = form.get("permissions")
    permissions_text = str(permissions_raw).strip() if permissions_raw is not None else ""
    parsed_permissions, permission_errors = _parse_permission_lines(permissions_text)
    errors.extend(permission_errors)

    is_enabled_argument: bool | None = None
    if "is_enabled_present" in form:
        is_enabled_argument = str(form.get("is_enabled")) == "1"

    if errors:
        return await _render_api_keys_dashboard(
            request,
            current_user,
            **filters,
            status_message=None,
            errors=errors,
        )

    update_kwargs: dict[str, Any] = {
        "description": new_description,
        "expiry_date": expiry_date,
        "permissions": parsed_permissions,
    }
    if is_enabled_argument is not None:
        update_kwargs["is_enabled"] = is_enabled_argument

    try:
        updated = await main_module.api_key_repo.update_api_key(
            api_key_id,
            **update_kwargs,
        )
    except Exception as exc:  # pragma: no cover - defensive logging
        main_module.log_error(
            "Failed to update API key from admin form",
            api_key_id=api_key_id,
            error=str(exc),
        )
        errors.append("Unable to save API key changes. Please try again.")
        return await _render_api_keys_dashboard(
            request,
            current_user,
            **filters,
            status_message=None,
            errors=errors,
        )

    await main_module.audit_service.log_action(
        action="api_keys.update",
        user_id=current_user.get("id"),
        entity_type="api_key",
        entity_id=api_key_id,
        previous_value={
            "description": existing.get("description"),
            "expiry_date": existing.get("expiry_date").isoformat()
            if isinstance(existing.get("expiry_date"), date)
            else None,
            "permissions": existing.get("permissions", []),
            "is_enabled": bool(existing.get("is_enabled", True)),
        },
        new_value={
            "description": updated.get("description"),
            "expiry_date": updated.get("expiry_date").isoformat()
            if isinstance(updated.get("expiry_date"), date)
            else None,
            "permissions": updated.get("permissions", []),
            "is_enabled": bool(updated.get("is_enabled", True)),
        },
        request=request,
    )

    status_message = "API key changes saved."
    return await _render_api_keys_dashboard(
        request,
        current_user,
        **filters,
        status_message=status_message,
        errors=None,
    )


async def admin_rotate_api_key_page(request: Request):
    main_module = _main()
    current_user, redirect = await main_module._require_super_admin_page(request)
    if redirect:
        return redirect
    form = await request.form()
    filters = _extract_api_key_filters(form)
    errors: list[str] = []
    api_key_id_raw = form.get("api_key_id")
    try:
        api_key_id = int(api_key_id_raw)
    except (TypeError, ValueError):
        errors.append("Invalid API key identifier supplied for rotation.")
        return await _render_api_keys_dashboard(
            request,
            current_user,
            **filters,
            status_message=None,
            errors=errors,
        )
    description = str(form.get("description", "")).strip() or None
    expiry_raw = form.get("expiry_date")
    expiry_date = main_module._parse_input_date(expiry_raw) if expiry_raw else None
    if expiry_raw and expiry_date is None:
        errors.append("Enter a valid expiry date in YYYY-MM-DD format.")
    permissions_raw = form.get("permissions")
    permissions_text = str(permissions_raw).strip() if permissions_raw is not None else ""
    parsed_permissions, permission_errors = _parse_permission_lines(permissions_text)
    allowed_ips_raw = form.get("allowed_ips")
    allowed_ips_text = str(allowed_ips_raw).strip() if allowed_ips_raw is not None else ""
    parsed_ip_restrictions, ip_errors = _parse_ip_restriction_lines(allowed_ips_text)
    retire_previous = main_module._parse_bool(form.get("retire_previous"), default=True)
    errors.extend(permission_errors)
    errors.extend(ip_errors)
    if errors:
        return await _render_api_keys_dashboard(
            request,
            current_user,
            **filters,
            status_message=None,
            errors=errors,
        )
    existing = await main_module.api_key_repo.get_api_key_with_usage(api_key_id)
    if not existing:
        errors.append("The selected API key could not be found. It may have been deleted.")
        return await _render_api_keys_dashboard(
            request,
            current_user,
            **filters,
            status_message=None,
            errors=errors,
        )
    final_description = description if description is not None else existing.get("description")
    final_expiry = expiry_date if expiry_date is not None else existing.get("expiry_date")
    permissions = parsed_permissions if permissions_raw is not None else existing.get("permissions", [])
    ip_restrictions = (
        parsed_ip_restrictions
        if allowed_ips_raw is not None
        else [entry.get("cidr") for entry in existing.get("ip_restrictions", [])]
    )
    try:
        raw_key, row = await main_module.api_key_repo.create_api_key(
            description=final_description,
            expiry_date=final_expiry,
            permissions=permissions,
            ip_restrictions=ip_restrictions,
        )
    except Exception as exc:  # pragma: no cover - defensive logging
        main_module.log_error("Failed to rotate API key from admin form", api_key_id=api_key_id, error=str(exc))
        errors.append("Unable to rotate API key. Please try again.")
        return await _render_api_keys_dashboard(
            request,
            current_user,
            **filters,
            status_message=None,
            errors=errors,
        )
    metadata = {
        "rotated_from": api_key_id,
        "retired_previous": retire_previous,
    }
    await main_module.audit_service.log_action(
        action="api_keys.rotate",
        user_id=current_user.get("id"),
        entity_type="api_key",
        entity_id=row["id"],
        previous_value=None,
        new_value={
            "description": final_description,
            "expiry_date": final_expiry.isoformat() if isinstance(final_expiry, date) else None,
            "permissions": permissions,
            "allowed_ips": ip_restrictions,
        },
        metadata=metadata,
        request=request,
    )
    if retire_previous:
        retirement_date = date.today()
        await main_module.api_key_repo.update_api_key_expiry(api_key_id, retirement_date)
        await main_module.audit_service.log_action(
            action="api_keys.retire",
            user_id=current_user.get("id"),
            entity_type="api_key",
            entity_id=api_key_id,
            previous_value={
                "description": existing.get("description"),
                "expiry_date": existing.get("expiry_date").isoformat()
                if isinstance(existing.get("expiry_date"), date)
                else None,
                "key_preview": mask_api_key(existing.get("key_prefix")),
            },
            new_value={"expiry_date": retirement_date.isoformat()},
            metadata={"rotated_to": row["id"]},
            request=request,
        )
    display_permissions, permissions_text_value, endpoint_summary = _format_api_key_permissions(
        row.get("permissions")
    )
    display_ip_restrictions, allowed_ips_text_value, ip_summary = _format_api_key_ip_restrictions(
        row.get("ip_restrictions")
    )
    if endpoint_summary and ip_summary:
        access_summary = f"{endpoint_summary} • {ip_summary}"
    elif endpoint_summary:
        access_summary = endpoint_summary
    else:
        access_summary = ip_summary
    new_api_key = {
        "id": row["id"],
        "value": raw_key,
        "key_preview": mask_api_key(row.get("key_prefix")),
        "description": row.get("description"),
        "expiry_iso": (
            datetime.combine(row.get("expiry_date"), time.min, tzinfo=timezone.utc).isoformat()
            if row.get("expiry_date")
            else None
        ),
        "rotated_from": api_key_id,
        "permissions": display_permissions,
        "permissions_text": permissions_text_value,
        "ip_restrictions": display_ip_restrictions,
        "ip_restrictions_text": allowed_ips_text_value,
        "access_summary": access_summary,
        "ip_summary": ip_summary,
        "endpoint_summary": endpoint_summary,
    }
    status_message = (
        "API key rotated. Copy the replacement key below and distribute it to integrated services."
    )
    return await _render_api_keys_dashboard(
        request,
        current_user,
        **filters,
        status_message=status_message,
        errors=None,
        new_api_key=new_api_key,
    )


async def admin_delete_api_key_page(request: Request):
    main_module = _main()
    current_user, redirect = await main_module._require_super_admin_page(request)
    if redirect:
        return redirect
    form = await request.form()
    filters = _extract_api_key_filters(form)
    errors: list[str] = []
    api_key_id_raw = form.get("api_key_id")
    try:
        api_key_id = int(api_key_id_raw)
    except (TypeError, ValueError):
        errors.append("Invalid API key identifier supplied for deletion.")
        return await _render_api_keys_dashboard(
            request,
            current_user,
            **filters,
            status_message=None,
            errors=errors,
        )
    existing = await main_module.api_key_repo.get_api_key_with_usage(api_key_id)
    if not existing:
        errors.append("API key not found or already deleted.")
        return await _render_api_keys_dashboard(
            request,
            current_user,
            **filters,
            status_message=None,
            errors=errors,
        )
    await main_module.api_key_repo.delete_api_key(api_key_id)
    await main_module.audit_service.log_action(
        action="api_keys.delete",
        user_id=current_user.get("id"),
        entity_type="api_key",
        entity_id=api_key_id,
        previous_value={
            "description": existing.get("description"),
            "expiry_date": existing.get("expiry_date").isoformat()
            if isinstance(existing.get("expiry_date"), date)
            else None,
            "key_preview": mask_api_key(existing.get("key_prefix")),
        },
        request=request,
    )
    status_message = f"API key {mask_api_key(existing.get('key_prefix'))} has been revoked."
    return await _render_api_keys_dashboard(
        request,
        current_user,
        **filters,
        status_message=status_message,
        errors=None,
        new_api_key=None,
    )


__all__ = [
    "admin_api_keys_page",
    "admin_create_api_key_page",
    "admin_update_api_key_page",
    "admin_rotate_api_key_page",
    "admin_delete_api_key_page",
    "_render_api_keys_dashboard",
]
